import { useEffect, useMemo, useState } from "react";
import { AlertCircle, Info, Save } from "lucide-react";
import api from "../lib/api";
import { errDetail } from "../lib/purchaseUi";

const TABS = [
  { id: "umum", label: "Umum", testid: "design-cetakan-tab-umum" },
  { id: "po", label: "PO", testid: "design-cetakan-tab-po" },
  { id: "invoice", label: "Faktur & Proforma", testid: "design-cetakan-tab-invoice" },
  { id: "sj", label: "Surat Jalan", testid: "design-cetakan-tab-sj" },
];

function emptyDraft() {
  return {
    company_name: "",
    address_lines: ["", "", ""],
    accent_color: "#cc1f1a",
    po: {
      signature_labels: ["Operator", "Pengiriman", "Penerima"],
      footer_name_label: "Nama",
    },
    invoice: {
      bank_accounts: [
        { bank_label: "", account_number: "", account_name: "" },
        { bank_label: "", account_number: "", account_name: "" },
      ],
      estimasi_title: "",
      estimasi_lines: ["", "", ""],
      prepared_label: "",
      prepared_footer: "",
      approved_label: "",
      approved_footer: "",
    },
    surat_jalan: {
      signature_labels: ["Gudang", "Cek Barang", "Kurir", "Penerima"],
      date_label: "Tgl.",
    },
  };
}

function fromApi(settings) {
  const s = settings || {};
  const inv = s.invoice || {};
  const banks = Array.isArray(inv.bank_accounts) && inv.bank_accounts.length
    ? inv.bank_accounts.map((row) => ({
        bank_label: row.bank_label || "",
        account_number: row.account_number || "",
        account_name: row.account_name || "",
      }))
    : emptyDraft().invoice.bank_accounts;
  while (banks.length < 2) banks.push({ bank_label: "", account_number: "", account_name: "" });
  return {
    company_name: s.company_name || "",
    address_lines: [...(s.address_lines || []), "", "", ""].slice(0, 4),
    accent_color: s.accent_color || "#cc1f1a",
    po: {
      signature_labels: [...((s.po || {}).signature_labels || []), "", "", ""].slice(0, 3),
      footer_name_label: (s.po || {}).footer_name_label || "Nama",
    },
    invoice: {
      bank_accounts: banks.slice(0, 4),
      estimasi_title: inv.estimasi_title || "",
      estimasi_lines: [...(inv.estimasi_lines || []), "", "", ""].slice(0, 5),
      prepared_label: inv.prepared_label || "",
      prepared_footer: inv.prepared_footer || "",
      approved_label: inv.approved_label || "",
      approved_footer: inv.approved_footer || "",
    },
    surat_jalan: {
      signature_labels: [...((s.surat_jalan || {}).signature_labels || []), "", "", "", ""].slice(0, 4),
      date_label: (s.surat_jalan || {}).date_label || "Tgl.",
    },
  };
}

export default function DesignCetakanSettings({ notify, canEdit }) {
  const [tab, setTab] = useState("umum");
  const [draft, setDraft] = useState(emptyDraft);
  const [meta, setMeta] = useState({ updated_at: null, updated_by: null });
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");

  const load = () => {
    setBusy(true);
    setErr("");
    api.designCetakanSettings()
      .then((res) => {
        const s = res?.settings || {};
        setDraft(fromApi(s));
        setMeta({ updated_at: s.updated_at || null, updated_by: s.updated_by || null });
      })
      .catch((e) => setErr(errDetail(e)))
      .finally(() => setBusy(false));
  };

  useEffect(() => {
    load();
  }, []);

  const updatedByLabel = useMemo(() => {
    const by = meta.updated_by;
    if (!by) return "";
    if (typeof by === "string") return by;
    return by.name || by.email || "";
  }, [meta.updated_by]);

  const patch = (path, value) => {
    setDraft((prev) => {
      const next = JSON.parse(JSON.stringify(prev));
      const parts = path.split(".");
      let cur = next;
      for (let i = 0; i < parts.length - 1; i += 1) {
        const key = parts[i];
        const idx = Number.isInteger(Number(key)) && key === String(Number(key)) ? Number(key) : key;
        cur = cur[idx];
      }
      const last = parts[parts.length - 1];
      const lastKey = Number.isInteger(Number(last)) && last === String(Number(last)) ? Number(last) : last;
      cur[lastKey] = value;
      return next;
    });
  };

  const save = async (e) => {
    e?.preventDefault();
    if (!canEdit || busy) return;
    setBusy(true);
    setErr("");
    try {
      const payload = {
        company_name: draft.company_name,
        address_lines: draft.address_lines.filter((line) => String(line || "").trim()),
        accent_color: draft.accent_color,
        po: {
          signature_labels: draft.po.signature_labels,
          footer_name_label: draft.po.footer_name_label,
        },
        invoice: {
          bank_accounts: draft.invoice.bank_accounts.filter(
            (row) => row.bank_label || row.account_number || row.account_name
          ),
          estimasi_title: draft.invoice.estimasi_title,
          estimasi_lines: draft.invoice.estimasi_lines.filter((line) => String(line || "").trim()),
          prepared_label: draft.invoice.prepared_label,
          prepared_footer: draft.invoice.prepared_footer,
          approved_label: draft.invoice.approved_label,
          approved_footer: draft.invoice.approved_footer,
        },
        surat_jalan: {
          signature_labels: draft.surat_jalan.signature_labels,
          date_label: draft.surat_jalan.date_label,
        },
      };
      const res = await api.updateDesignCetakanSettings(payload);
      const s = res?.settings || {};
      setDraft(fromApi(s));
      setMeta({ updated_at: s.updated_at || null, updated_by: s.updated_by || null });
      notify?.(res.message || "Design Cetakan berhasil disimpan");
    } catch (ex) {
      setErr(errDetail(ex));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="settings-card" data-testid="design-cetakan-settings">
      <div className="settings-card-header" style={{ marginBottom: 16 }}>
        <h2>Design Cetakan</h2>
        <p className="muted small" style={{ marginTop: 4 }}>
          Letterhead PDF Pesanan Penjualan, Faktur/Proforma, dan Surat Jalan. Tanpa upload logo pada fase ini.
        </p>
      </div>

      <div className="settings-info" style={{ marginBottom: 16 }}>
        <Info size={16} />
        <div>
          <p><strong>Default mengikuti PDF yang sudah deploy.</strong></p>
          <p className="muted small" style={{ marginTop: 2 }}>
            Judul dokumen, layout, dan nomor halaman tetap fixed. Rekening di sini hanya teks presentasi PDF, bukan master Kas/Bank.
          </p>
        </div>
      </div>

      {err ? (
        <div className="signal-strip" role="alert" data-testid="design-cetakan-error" style={{ marginBottom: 16 }}>
          <AlertCircle size={16} />
          <span>{err}</span>
        </div>
      ) : null}

      <div className="settings-tabs" role="tablist" aria-label="Bagian Design Cetakan" style={{ marginBottom: 16 }}>
        {TABS.map((item) => (
          <button
            key={item.id}
            type="button"
            role="tab"
            aria-selected={tab === item.id}
            className={tab === item.id ? "is-active" : undefined}
            data-testid={item.testid}
            onClick={() => setTab(item.id)}
          >
            {item.label}
          </button>
        ))}
      </div>

      <form onSubmit={save}>
        {tab === "umum" ? (
          <div className="settings-grid" data-testid="design-cetakan-umum">
            <label>
              Nama perusahaan
              <input
                data-testid="design-cetakan-company-name"
                value={draft.company_name}
                disabled={!canEdit || busy}
                onChange={(e) => patch("company_name", e.target.value)}
              />
            </label>
            <label>
              Warna aksen
              <input
                data-testid="design-cetakan-accent"
                value={draft.accent_color}
                disabled={!canEdit || busy}
                onChange={(e) => patch("accent_color", e.target.value)}
                placeholder="#cc1f1a"
              />
            </label>
            {draft.address_lines.map((line, idx) => (
              <label key={`addr-${idx}`}>
                Alamat baris {idx + 1}
                <input
                  data-testid={`design-cetakan-address-${idx}`}
                  value={line}
                  disabled={!canEdit || busy}
                  onChange={(e) => patch(`address_lines.${idx}`, e.target.value)}
                />
              </label>
            ))}
          </div>
        ) : null}

        {tab === "po" ? (
          <div className="settings-grid" data-testid="design-cetakan-po">
            {draft.po.signature_labels.map((label, idx) => (
              <label key={`po-sig-${idx}`}>
                Label tanda tangan {idx + 1}
                <input
                  data-testid={`design-cetakan-po-sig-${idx}`}
                  value={label}
                  disabled={!canEdit || busy}
                  onChange={(e) => patch(`po.signature_labels.${idx}`, e.target.value)}
                />
              </label>
            ))}
            <label>
              Label nama footer
              <input
                data-testid="design-cetakan-po-footer-name"
                value={draft.po.footer_name_label}
                disabled={!canEdit || busy}
                onChange={(e) => patch("po.footer_name_label", e.target.value)}
              />
            </label>
          </div>
        ) : null}

        {tab === "invoice" ? (
          <div className="settings-grid" data-testid="design-cetakan-invoice">
            {draft.invoice.bank_accounts.map((row, idx) => (
              <div key={`bank-${idx}`} style={{ display: "contents" }}>
                <label>
                  Bank {idx + 1}
                  <input
                    data-testid={`design-cetakan-bank-label-${idx}`}
                    value={row.bank_label}
                    disabled={!canEdit || busy}
                    onChange={(e) => patch(`invoice.bank_accounts.${idx}.bank_label`, e.target.value)}
                  />
                </label>
                <label>
                  Nomor rekening {idx + 1}
                  <input
                    data-testid={`design-cetakan-bank-number-${idx}`}
                    value={row.account_number}
                    disabled={!canEdit || busy}
                    onChange={(e) => patch(`invoice.bank_accounts.${idx}.account_number`, e.target.value)}
                  />
                </label>
                <label>
                  A.N rekening {idx + 1}
                  <input
                    data-testid={`design-cetakan-bank-name-${idx}`}
                    value={row.account_name}
                    disabled={!canEdit || busy}
                    onChange={(e) => patch(`invoice.bank_accounts.${idx}.account_name`, e.target.value)}
                  />
                </label>
              </div>
            ))}
            <label>
              Judul estimasi
              <input
                data-testid="design-cetakan-estimasi-title"
                value={draft.invoice.estimasi_title}
                disabled={!canEdit || busy}
                onChange={(e) => patch("invoice.estimasi_title", e.target.value)}
              />
            </label>
            {draft.invoice.estimasi_lines.map((line, idx) => (
              <label key={`est-${idx}`}>
                Estimasi baris {idx + 1}
                <input
                  data-testid={`design-cetakan-estimasi-line-${idx}`}
                  value={line}
                  disabled={!canEdit || busy}
                  onChange={(e) => patch(`invoice.estimasi_lines.${idx}`, e.target.value)}
                />
              </label>
            ))}
            <label>
              Label Disiapkan
              <input
                data-testid="design-cetakan-prepared-label"
                value={draft.invoice.prepared_label}
                disabled={!canEdit || busy}
                onChange={(e) => patch("invoice.prepared_label", e.target.value)}
              />
            </label>
            <label>
              Footer Disiapkan
              <input
                data-testid="design-cetakan-prepared-footer"
                value={draft.invoice.prepared_footer}
                disabled={!canEdit || busy}
                onChange={(e) => patch("invoice.prepared_footer", e.target.value)}
              />
            </label>
            <label>
              Label Disetujui
              <input
                data-testid="design-cetakan-approved-label"
                value={draft.invoice.approved_label}
                disabled={!canEdit || busy}
                onChange={(e) => patch("invoice.approved_label", e.target.value)}
              />
            </label>
            <label>
              Footer Disetujui
              <input
                data-testid="design-cetakan-approved-footer"
                value={draft.invoice.approved_footer}
                disabled={!canEdit || busy}
                onChange={(e) => patch("invoice.approved_footer", e.target.value)}
              />
            </label>
          </div>
        ) : null}

        {tab === "sj" ? (
          <div className="settings-grid" data-testid="design-cetakan-sj">
            {draft.surat_jalan.signature_labels.map((label, idx) => (
              <label key={`sj-sig-${idx}`}>
                Label tanda tangan {idx + 1}
                <input
                  data-testid={`design-cetakan-sj-sig-${idx}`}
                  value={label}
                  disabled={!canEdit || busy}
                  onChange={(e) => patch(`surat_jalan.signature_labels.${idx}`, e.target.value)}
                />
              </label>
            ))}
            <label>
              Label tanggal
              <input
                data-testid="design-cetakan-sj-date-label"
                value={draft.surat_jalan.date_label}
                disabled={!canEdit || busy}
                onChange={(e) => patch("surat_jalan.date_label", e.target.value)}
              />
            </label>
          </div>
        ) : null}

        <div style={{ display: "flex", gap: 12, alignItems: "center", marginTop: 20 }}>
          <button
            type="submit"
            className="primary"
            data-testid="design-cetakan-save"
            disabled={!canEdit || busy}
          >
            <Save size={16} />
            {busy ? "Menyimpan…" : "Simpan"}
          </button>
          {!canEdit ? (
            <span className="muted small">Anda hanya dapat melihat pengaturan ini.</span>
          ) : null}
          {meta.updated_at ? (
            <span className="muted small" data-testid="design-cetakan-updated">
              Terakhir diubah{updatedByLabel ? ` oleh ${updatedByLabel}` : ""}
            </span>
          ) : null}
        </div>
      </form>
    </div>
  );
}
