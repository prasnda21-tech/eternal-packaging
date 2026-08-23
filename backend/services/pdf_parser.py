"""PDF PO parser.

Reads embedded text (no OCR). Returns preview dict in the same shape as
`import_parser.parse_import_file`. Fields we can't confidently identify are
left as None so the UI can render 'Perlu verifikasi' rather than guessing.

Strategy:
1. Use pdfplumber to extract text + tables per page.
2. Search text with a small set of Indonesian/English labelled regex.
3. If pdfplumber found real tables (bordered), use those as the line-item
   source of truth. Otherwise fall back to "column-guess" from column x-
   coordinates of words.

Never OCRs a scan. If the document has zero extractable text, we raise
ImportParseError so the UI can tell the admin to re-upload a text-based PDF.
"""
from __future__ import annotations

import io
import re
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional, Tuple

try:
    import pdfplumber  # type: ignore
except ImportError:  # pragma: no cover
    pdfplumber = None  # noqa: N816


class PDFParseError(ValueError):
    """Raised when the PDF cannot be parsed as a text-based PO."""


# --- Label patterns ----------------------------------------------------
_LABEL_PATTERNS: Dict[str, List[str]] = {
    "po_number": [r"(?:nomor\s*)?(?:no\.?\s*)?(?:po|sales\s*order|so)\s*(?:number)?", r"po\s*#", r"purchase\s*order\s*no\.?"],
    "order_date": [r"tanggal\s*(?:po|order)", r"po\s*date", r"order\s*date", r"tgl\.?\s*po"],
    "estimated_delivery": [r"estimasi\s*(?:kirim|pengiriman)", r"delivery\s*date", r"tanggal\s*kirim", r"est\.?\s*delivery"],
    "customer": [r"customer", r"pelanggan", r"buyer", r"kepada"],
    "outlet": [r"outlet", r"cabang", r"branch"],
    "recipient": [r"penerima", r"recipient", r"pic", r"contact\s*person"],
    "address": [r"alamat", r"address", r"shipping\s*address", r"ship\s*to"],
    "payment_terms": [r"(?:termin|term|syarat)\s*(?:bayar|pembayaran)?", r"payment\s*terms?"],
    "expedition": [r"ekspedisi", r"expedition", r"kurir", r"courier"],
    "delivery_zone": [r"zona(?:\s*(?:kirim|pengiriman))?", r"delivery\s*zone", r"wilayah"],
    "sales": [r"sales(?:person|man)?", r"penjual", r"tenaga\s*penjual"],
    "created_by_user": [r"(?:user|pembuat)(?:\s*po)?", r"created\s*by", r"input\s*by", r"dibuat\s*oleh"],
    "notes": [r"catatan", r"keterangan", r"notes?", r"description"],
}

_MONTHS_ID = {
    "januari": 1, "februari": 2, "maret": 3, "april": 4, "mei": 5, "juni": 6,
    "juli": 7, "agustus": 8, "september": 9, "oktober": 10, "november": 11, "desember": 12,
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "jun": 6, "jul": 7, "agu": 8, "ags": 8, "agt": 8,
    "sep": 9, "okt": 10, "nov": 11, "des": 12,
}


def _parse_date(raw: str) -> Optional[str]:
    if not raw:
        return None
    s = raw.strip().rstrip(".,")
    # 2026-02-20 / 20-02-2026 / 20/02/2026 / 20 Feb 2026 / 20 Februari 2026
    for fmt in ("%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y", "%Y/%m/%d", "%d %b %Y", "%d %B %Y",
                "%d %b %y", "%d-%b-%Y", "%d %m %Y"):
        try:
            return datetime.strptime(s, fmt).replace(tzinfo=timezone.utc).isoformat()
        except ValueError:
            continue
    # Indonesian month names
    m = re.match(r"(\d{1,2})[\s\-\/]+([A-Za-z]+)[\s\-\/]+(\d{2,4})", s)
    if m:
        day = int(m.group(1))
        mon_key = m.group(2).lower()[:3]
        year = int(m.group(3))
        if year < 100:
            year += 2000
        mon = _MONTHS_ID.get(mon_key) or _MONTHS_ID.get(m.group(2).lower())
        if mon:
            try:
                return datetime(year, mon, day, tzinfo=timezone.utc).isoformat()
            except ValueError:
                pass
    return None


def _first_match(text: str, patterns: List[str], require_colon: bool = False) -> Optional[str]:
    """Look for `LABEL: value` or `LABEL value` on a single line."""
    sep = r"\s*[:\-]\s*" if require_colon else r"\s*[:\-]?\s*"
    for pat in patterns:
        rx = re.compile(rf"(?im)^\s*{pat}{sep}(?!\n)(.+)$")
        m = rx.search(text)
        if m:
            v = m.group(1).strip()
            v = re.split(r"\s{3,}|\t| \| ", v, maxsplit=1)[0].strip()
            if v and v.lower() not in {"-", "n/a", "na"}:
                return v
    return None


def _extract_field(text: str, key: str) -> Optional[str]:
    # Require ':' or '-' for identifier-like fields so we don't match page titles.
    strict = key in {"po_number", "order_date", "estimated_delivery", "sales", "created_by_user", "customer", "recipient"}
    v = _first_match(text, _LABEL_PATTERNS[key], require_colon=strict)
    if v and (key.endswith("date") or key in {"estimated_delivery", "order_date"}):
        d = _parse_date(v)
        return d or v
    return v


def _to_int(v: Any) -> int:
    if v is None or v == "":
        return 0
    if isinstance(v, (int, float)):
        return int(v)
    s = str(v).strip().replace(".", "").replace(",", "")
    try:
        return int(float(s))
    except ValueError:
        return 0


def _extract_address_block(text: str) -> Optional[str]:
    """Address often spans 2-3 lines after 'Alamat:'. Stop at next known label."""
    m = re.search(r"(?im)^\s*(?:alamat|address|ship\s*to)\s*[:\-]?\s*(.+(?:\n.+){0,4})", text)
    if not m:
        return None
    block = m.group(1).strip()
    stop_rx = re.compile(
        r"(?i)^\s*(customer|pelanggan|outlet|cabang|penerima|recipient|sales|penjual|"
        r"kurir|courier|ekspedisi|expedition|zona|delivery|tanggal|tgl|estimasi|"
        r"po\b|user|pembuat|catatan|keterangan|notes?|syarat|termin|payment|"
        r"line\s*items?|kode|nama|qty|jumlah)"
    )
    lines: List[str] = []
    for line in block.splitlines():
        if stop_rx.match(line):
            break
        s = line.strip()
        if s:
            lines.append(s)
    return " ".join(lines) or None


def _extract_line_items(tables: List[List[List[Optional[str]]]], full_text: str) -> List[Dict[str, Any]]:
    """Prefer detected tables; otherwise best-effort scan of text lines."""
    # 1. Try tables (bordered)
    for tbl in tables:
        if not tbl or len(tbl) < 2:
            continue
        header = [(c or "").strip().lower() for c in tbl[0]]
        if not any(h for h in header):
            continue
        # Column locator
        def find_col(*keys: str) -> Optional[int]:
            for i, h in enumerate(header):
                if any(k in h for k in keys):
                    return i
            return None
        i_name = find_col("nama", "item", "product", "barang", "description")
        i_qty = find_col("qty", "jumlah", "quantity", "kuantitas")
        if i_name is None or i_qty is None:
            continue
        i_code = find_col("kode", "sku", "code", "no.")
        i_unit = find_col("unit", "satuan", "uom")
        i_color = find_col("warna", "color")
        i_notes = find_col("catatan", "notes", "keterangan", "note")
        items: List[Dict[str, Any]] = []
        for row in tbl[1:]:
            if not row:
                continue
            name = (row[i_name] or "").strip() if i_name < len(row) else ""
            qty = _to_int(row[i_qty] if i_qty < len(row) else "")
            if not name or qty <= 0:
                continue
            items.append({
                "item_code": (row[i_code].strip() if i_code is not None and i_code < len(row) and row[i_code] else None),
                "name": name,
                "quantity": qty,
                "unit": (row[i_unit].strip() if i_unit is not None and i_unit < len(row) and row[i_unit] else "pcs"),
                "color": (row[i_color].strip() if i_color is not None and i_color < len(row) and row[i_color] else None),
                "notes": (row[i_notes].strip() if i_notes is not None and i_notes < len(row) and row[i_notes] else None),
            })
        if items:
            return items

    # 2. Fallback: text-line scan — look for lines like:
    #    "CODE  Name of item  <qty> <unit> [color/notes]"
    # pdfplumber collapses whitespace to single spaces, so we anchor on
    # a numeric quantity followed by a unit keyword.
    items: List[Dict[str, Any]] = []
    unit_kw = r"pcs|pc|dus|box|lusin|kg|unit|lembar|roll|pack|set|rim"
    line_rx = re.compile(
        rf"^(?P<code>[A-Z0-9][A-Z0-9\-\/]{{1,19}})\s+(?P<name>.+?)\s+(?P<qty>\d{{1,7}})\s+(?P<unit>{unit_kw})\b(?:\s+(?P<extra>.+))?$",
        re.I,
    )
    for line in full_text.splitlines():
        line = line.strip()
        if not line or len(line) < 8:
            continue
        m = line_rx.match(line)
        if not m:
            continue
        qty = _to_int(m.group("qty"))
        if qty <= 0:
            continue
        extra = (m.group("extra") or "").strip() or None
        items.append({
            "item_code": m.group("code").upper(),
            "name": m.group("name").strip(),
            "quantity": qty,
            "unit": m.group("unit").lower(),
            "color": extra,
            "notes": None,
        })
    return items


def _normalize_label(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").strip().lower())


# Canonical label → internal key. Order matters: more specific first.
_TABLE_LABELS: List[Tuple[str, str]] = [
    ("nomor po", "po_number"),
    ("no po", "po_number"),
    ("no. po", "po_number"),
    ("nomor", "po_number"),
    ("estimasi tanggal pengiriman", "estimated_delivery"),
    ("estimasi pengiriman", "estimated_delivery"),
    ("estimasi kirim", "estimated_delivery"),
    ("tanggal pengiriman", "estimated_delivery"),
    ("tanggal po", "order_date"),
    ("tanggal", "order_date"),
    ("syarat pembayaran", "payment_terms"),
    ("termin pembayaran", "payment_terms"),
    ("payment terms", "payment_terms"),
    ("ekspedisi", "expedition"),
    ("expedition", "expedition"),
    ("kurir", "expedition"),
    ("zona pengiriman", "delivery_zone"),
    ("zona kirim", "delivery_zone"),
    ("zona", "delivery_zone"),
    ("penjual", "sales"),
    ("salesperson", "sales"),
    ("sales", "sales"),
    ("user po", "created_by_user"),
    ("pembuat po", "created_by_user"),
    ("dibuat oleh", "created_by_user"),
    ("created by", "created_by_user"),
    ("user", "created_by_user"),
    ("outlet", "outlet"),
    ("cabang", "outlet"),
    ("keterangan", "notes"),
    ("catatan", "notes"),
]

# Date-typed keys need post-parsing
_DATE_KEYS = {"order_date", "estimated_delivery"}


def _map_label(label: str) -> Optional[str]:
    n = _normalize_label(label)
    for lbl, key in _TABLE_LABELS:
        if n == lbl or n.startswith(lbl + " ") or n.endswith(" " + lbl):
            return key
    return None


def _extract_from_tables(tables: List[List[List[Optional[str]]]]) -> Dict[str, str]:
    """Walk every cell and pick up `label\\nvalue` style pairs from the header
    tables that many Indonesian PO layouts use (Eternal, Accurate print, etc.)."""
    found: Dict[str, str] = {}
    for tbl in tables:
        if not tbl:
            continue
        # Skip pure line-item tables — they carry Kts./Satuan/Kode/Nama columns
        header = [_normalize_label(c or "") for c in (tbl[0] or [])]
        joined = " ".join(header)
        if any(h in joined for h in ("kode barang", "nama barang", "kts.", "satuan", "kuantitas", "qty")):
            continue
        for row in tbl:
            for cell in row or []:
                if not cell:
                    continue
                # Cells like "Syarat Pembayaran\nTRANSFER BANK" or "Total Kuantitas | 600"
                if "\n" in cell:
                    lines = [ln.strip() for ln in cell.split("\n") if ln.strip()]
                    if len(lines) < 2:
                        continue
                    key = _map_label(lines[0])
                    if key and key not in found:
                        value = " ".join(lines[1:]).strip()
                        if value:
                            found[key] = value
    # Post-parse dates
    for k in list(found.keys()):
        if k in _DATE_KEYS:
            d = _parse_date(found[k])
            if d:
                found[k] = d
    return found


def _left_column_text(page, right_boundary: float = 250.0) -> str:
    """Rebuild only the LEFT column of the page as text.

    Words to the right of `right_boundary` (metadata columns) are ignored so
    the "Kepada / PENERIMA / ALAMAT" block doesn't mix with them.
    """
    try:
        words = page.extract_words(x_tolerance=2, y_tolerance=3, use_text_flow=False)
    except Exception:
        return ""
    left = [w for w in words if w.get("x0", 0) < right_boundary]
    if not left:
        return ""
    # Group by y-band
    left.sort(key=lambda w: (w["top"], w["x0"]))
    lines: List[List[dict]] = []
    for w in left:
        if lines and abs(w["top"] - lines[-1][-1]["top"]) < 3.5:
            lines[-1].append(w)
        else:
            lines.append([w])
    return "\n".join(" ".join(x["text"] for x in ln) for ln in lines)


def _extract_kepada_block(pdf) -> Dict[str, Optional[str]]:
    """Extract customer / recipient / address / notes from the left column
    when the PDF uses a multi-column header table (Eternal-style)."""
    out: Dict[str, Optional[str]] = {"customer": None, "recipient": None, "address": None, "notes": None}
    if not pdf.pages:
        return out
    text = _left_column_text(pdf.pages[0])
    if not text:
        return out
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    if not lines:
        return out

    # Anchors
    idx_kepada = next((i for i, ln in enumerate(lines) if re.match(r"(?i)^kepada\b", ln)), None)
    idx_penerima = next((i for i, ln in enumerate(lines) if re.match(r"(?i)^penerima\s*:", ln)), None)
    idx_alamat = next((i for i, ln in enumerate(lines) if re.match(r"(?i)^alamat\s*:", ln)), None)
    idx_ket = next((i for i, ln in enumerate(lines) if re.match(r"(?i)^keterangan\s*:", ln)), None)
    # Stop anchors — lines that mark the END of the header block on the left
    stop_rx = re.compile(
        r"(?i)^(kode\s*barang|nama\s*barang|kts\.?|kuantitas|qty|jumlah|"
        r"total\s*kuantitas|jenis\s*barang|keterangan\s*:|operator|nama$|"
        r"tanda\s*terima|penerima\s*$|pengiriman)"
    )
    idx_stop = next((i for i, ln in enumerate(lines) if stop_rx.match(ln)), len(lines))

    # Customer = line(s) between "Kepada" and the first of {PENERIMA:, ALAMAT:, stop}
    if idx_kepada is not None:
        end_candidates = [x for x in (idx_penerima, idx_alamat, idx_stop) if x is not None and x > idx_kepada]
        end = min(end_candidates) if end_candidates else len(lines)
        cust_lines = [ln for ln in lines[idx_kepada + 1:end] if not re.match(r"(?i)^(penerima|alamat)\s*:", ln)]
        if cust_lines:
            out["customer"] = " ".join(cust_lines).strip() or None

    # Recipient (single line after "PENERIMA :")
    if idx_penerima is not None:
        v = re.sub(r"(?i)^penerima\s*:\s*", "", lines[idx_penerima]).strip()
        v = re.split(r"\s{2,}", v)[0].strip()
        if v:
            out["recipient"] = v

    # Address: from "ALAMAT :" until stop anchor
    if idx_alamat is not None:
        addr_head = re.sub(r"(?i)^alamat\s*:\s*", "", lines[idx_alamat]).strip()
        collected: List[str] = [addr_head] if addr_head else []
        for ln in lines[idx_alamat + 1:]:
            if stop_rx.match(ln) or re.match(r"(?i)^(kepada|penerima|alamat)\s*:", ln):
                break
            collected.append(ln)
        joined = " ".join(collected).strip()
        if joined:
            out["address"] = joined

    # Notes: line(s) after "Keterangan :" in the left column, until next label / block end
    if idx_ket is not None:
        head = re.sub(r"(?i)^keterangan\s*:\s*", "", lines[idx_ket]).strip()
        note_stop = re.compile(
            r"(?i)^(operator|nama$|tanda\s*terima|pengiriman|penerima\s*$|"
            r"total\s*kuantitas|jenis\s*barang)"
        )
        collected = [head] if head else []
        for ln in lines[idx_ket + 1:]:
            if note_stop.match(ln):
                break
            collected.append(ln)
        joined = " ".join(collected).strip()
        if joined:
            out["notes"] = joined
    return out


def parse_pdf_po(data: bytes, filename: str) -> Dict[str, Any]:
    """Parse a text-based PDF PO and return a preview dict."""
    if pdfplumber is None:
        raise PDFParseError("pdfplumber tidak terpasang di server.")
    try:
        pdf = pdfplumber.open(io.BytesIO(data))
    except Exception as e:
        raise PDFParseError(f"Gagal membuka PDF: {e}")
    with pdf:
        pages_text: List[str] = []
        tables: List[List[List[Optional[str]]]] = []
        for page in pdf.pages:
            txt = page.extract_text() or ""
            pages_text.append(txt)
            try:
                for t in page.extract_tables() or []:
                    if t:
                        tables.append(t)
            except Exception:
                continue
        # Layout-aware left-column extraction for Kepada / Penerima / Alamat
        kepada = _extract_kepada_block(pdf)

    full_text = "\n".join(pages_text).strip()
    if not full_text:
        raise PDFParseError(
            "PDF tampaknya berupa scan/gambar tanpa text layer. "
            "Silakan input manual di preview atau unggah PDF text-based."
        )

    # 1. Table-based header extraction (Eternal-style multi-column layout)
    from_tables = _extract_from_tables(tables)

    # 2. Fall back to labelled text regex ONLY for keys we didn't find in tables
    def resolve(key: str) -> Optional[str]:
        if key in from_tables:
            return from_tables[key]
        return _extract_field(full_text, key)

    po_number = resolve("po_number")
    order_date = resolve("order_date")
    est_delivery = resolve("estimated_delivery")
    payment_terms = resolve("payment_terms")
    expedition = resolve("expedition")
    delivery_zone = resolve("delivery_zone")
    sales = resolve("sales")
    created_by_user = resolve("created_by_user")

    # Customer / recipient / address: prefer layout-aware left column, otherwise regex
    customer = kepada.get("customer") or _extract_field(full_text, "customer")
    recipient = kepada.get("recipient") or _extract_field(full_text, "recipient")
    address = kepada.get("address") or _extract_address_block(full_text)

    # Outlet: only if PDF explicitly has an Outlet label (never guess)
    outlet = from_tables.get("outlet")

    # Notes / Keterangan: layout block first, then table, then regex fallback
    notes = kepada.get("notes") or from_tables.get("notes") or _extract_field(full_text, "notes")

    line_items = _extract_line_items(tables, full_text)
    total_qty = sum(li["quantity"] for li in line_items)

    now = datetime.now(timezone.utc)
    return {
        "import_source": "pdf",
        "filename": filename,
        "accurate_id": None,
        "po_number": po_number,
        "customer": customer,
        "outlet": outlet,
        "recipient": recipient,
        "address": address,
        "payment_terms": payment_terms,
        "sales": sales,
        "created_by_user": created_by_user,
        "expedition": expedition,
        "delivery_zone": delivery_zone,
        "seller": sales,
        "order_date": order_date or now.isoformat(),
        "estimated_delivery": est_delivery or (now + timedelta(days=3)).isoformat(),
        "deadline": est_delivery or (now + timedelta(days=3)).isoformat(),
        "notes": notes or "",
        "line_items": line_items,
        "total_quantity": total_qty,
        "item_count": len(line_items),
        "_parse_confidence": {
            "po_number": po_number is not None,
            "customer": customer is not None,
            "line_items": len(line_items) > 0,
        },
    }
