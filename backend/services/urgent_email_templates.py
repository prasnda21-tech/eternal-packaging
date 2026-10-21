"""Urgent PO email templates — HTML + plain text (Bahasa Indonesia)."""
from __future__ import annotations

from html import escape
from typing import Dict, List, Optional, Tuple


def po_deep_link(base_url: str, order_id: str) -> str:
    base = str(base_url or "").rstrip("/")
    oid = str(order_id or "").strip()
    return f"{base}/po/{oid}" if base and oid else ""


def _customer_label(order: Optional[dict]) -> str:
    if not isinstance(order, dict):
        return "-"
    c = order.get("customer")
    if isinstance(c, dict):
        return str(c.get("name") or c.get("customer_name") or "-").strip() or "-"
    return str(c or "-").strip() or "-"


def build_request_email(*, order: dict, request: dict, base_url: str) -> Dict[str, str]:
    po = str(order.get("po_number") or request.get("po_number") or "-")
    customer = _customer_label(order)
    requester = str(request.get("requested_by_name") or request.get("requested_by_email") or "-")
    reason = str(request.get("reason") or "-")
    target = str(request.get("target_completion_date") or "-")
    link = po_deep_link(base_url, str(order.get("id") or request.get("order_id") or ""))
    subject = f"[Urgent PO] Pengajuan baru — {po}"
    plain = (
        "Eternal Packaging — Notifikasi Urgent PO\n\n"
        "Pengajuan Urgent baru menunggu persetujuan.\n\n"
        f"Nomor PO: {po}\n"
        f"Customer: {customer}\n"
        f"Pengaju: {requester}\n"
        f"Alasan: {reason}\n"
        f"Target selesai: {target}\n"
        "Status: Menunggu Persetujuan\n\n"
        f"Buka detail PO (setelah login):\n{link}\n\n"
        "Email ini tidak melakukan persetujuan otomatis.\n"
    )
    html = _wrap_html(
        title="Pengajuan Urgent PO",
        badge="URGENT",
        rows=[
            ("Nomor PO", po),
            ("Customer", customer),
            ("Pengaju", requester),
            ("Alasan", reason),
            ("Target selesai", target),
            ("Status", "Menunggu Persetujuan"),
        ],
        link=link,
        link_label="Buka Detail PO",
        footer="Email ini hanya pemberitahuan. Persetujuan dilakukan di aplikasi setelah login.",
    )
    return {"subject": subject, "text": plain, "html": html}


def build_decision_email(*, order: dict, request: dict, base_url: str) -> Dict[str, str]:
    po = str(order.get("po_number") or request.get("po_number") or "-")
    status = str(request.get("status") or "").lower()
    approved = status == "approved"
    decision_label = "Disetujui" if approved else "Ditolak"
    decider = str(request.get("decided_by_name") or request.get("decided_by_email") or "-")
    notes = str(request.get("decision_notes") or "").strip()
    link = po_deep_link(base_url, str(order.get("id") or request.get("order_id") or ""))
    subject = f"[Urgent PO] {decision_label} — {po}"
    plain_lines = [
        "Eternal Packaging — Notifikasi Urgent PO",
        "",
        f"Pengajuan Urgent Anda telah {decision_label.lower()}.",
        "",
        f"Nomor PO: {po}",
        f"Keputusan: {decision_label}",
        f"Diputuskan oleh: {decider}",
    ]
    rows: List[Tuple[str, str]] = [
        ("Nomor PO", po),
        ("Keputusan", decision_label),
        ("Diputuskan oleh", decider),
    ]
    if not approved:
        plain_lines.append(f"Alasan penolakan: {notes or '-'}")
        rows.append(("Alasan penolakan", notes or "-"))
    elif notes:
        plain_lines.append(f"Catatan: {notes}")
        rows.append(("Catatan", notes))
    plain_lines.extend(
        [
            "",
            f"Buka detail PO (setelah login):\n{link}",
            "",
            "Email ini tidak melakukan persetujuan otomatis.",
            "",
        ]
    )
    html = _wrap_html(
        title=f"Pengajuan Urgent {decision_label}",
        badge="URGENT" if approved else "INFO",
        rows=rows,
        link=link,
        link_label="Buka Detail PO",
        footer="Email ini hanya pemberitahuan. Tindakan lebih lanjut dilakukan di aplikasi setelah login.",
        decision_ok=approved,
    )
    return {"subject": subject, "text": "\n".join(plain_lines), "html": html}


def _wrap_html(
    *,
    title: str,
    badge: str,
    rows: List[Tuple[str, str]],
    link: str,
    link_label: str,
    footer: str,
    decision_ok: Optional[bool] = None,
) -> str:
    green = "#1B7A4E"
    amber = "#D97706"
    accent = amber if badge == "URGENT" else green
    if decision_ok is True:
        accent = green
    elif decision_ok is False:
        accent = "#B45309"
    row_html = "".join(
        (
            '<tr><td style="padding:8px 0;color:#64748b;width:38%;font-size:14px">'
            f"{escape(str(k))}</td>"
            '<td style="padding:8px 0;color:#0f172a;font-size:14px;font-weight:600">'
            f"{escape(str(v))}</td></tr>"
        )
        for k, v in rows
    )
    btn = ""
    if link:
        btn = (
            '<p style="margin:24px 0 8px">'
            f'<a href="{escape(link)}" style="display:inline-block;background:{green};'
            "color:#fff;text-decoration:none;padding:12px 18px;border-radius:8px;"
            f'font-weight:600">{escape(link_label)}</a></p>'
        )
    return f"""<!DOCTYPE html>
<html lang="id"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{escape(title)}</title></head>
<body style="margin:0;padding:0;background:#f1f5f9;font-family:Segoe UI,Roboto,Helvetica,Arial,sans-serif">
<table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="background:#f1f5f9;padding:24px 12px">
<tr><td align="center">
<table role="presentation" width="100%" style="max-width:560px;background:#ffffff;border-radius:12px;overflow:hidden;border:1px solid #e2e8f0">
<tr><td style="background:{green};padding:18px 22px;color:#fff">
<div style="font-size:13px;opacity:.9;letter-spacing:.04em">ETERNAL PACKAGING</div>
<div style="font-size:20px;font-weight:700;margin-top:4px">{escape(title)}</div>
</td></tr>
<tr><td style="padding:22px">
<span style="display:inline-block;background:{accent};color:#fff;font-size:11px;font-weight:700;padding:4px 10px;border-radius:999px;letter-spacing:.06em">{escape(badge)}</span>
<table role="presentation" width="100%" style="margin-top:16px;border-collapse:collapse">{row_html}</table>
{btn}
<p style="margin:16px 0 0;color:#64748b;font-size:12px;line-height:1.5">{escape(footer)}</p>
</td></tr>
</table>
</td></tr></table>
</body></html>"""
