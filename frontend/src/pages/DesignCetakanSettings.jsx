import { useEffect, useMemo, useRef, useState } from "react";
import { AlertCircle, Info, Save } from "lucide-react";
import api from "../lib/api";
import { errDetail } from "../lib/purchaseUi";

const TABS = [
  { id: "umum", label: "Umum", testid: "design-cetakan-tab-umum" },
  { id: "po", label: "PO", testid: "design-cetakan-tab-po" },
  { id: "invoice", label: "Faktur & Proforma", testid: "design-cetakan-tab-invoice" },
  { id: "sj", label: "Surat Jalan", testid: "design-cetakan-tab-sj" },
];

const LOGO_ACCEPT = "image/png,image/jpeg,image/jpg,image/webp";
const SIGNATURE_ACCEPT = "image/png,image/jpeg,image/jpg,image/webp";

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

function emptyAssets() {
  return {
    company_logo: { has_custom: false },
    prepared_signature_image: { has_custom: false },
    approved_signature_image: { has_custom: false },
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

function assetsFromApi(settings) {
  const s = settings || {};
  const inv = s.invoice || {};
  return {
    company_logo: s.company_logo || { has_custom: false },
    prepared_signature_image: inv.prepared_signature_image || { has_custom: false },
    approved_signature_image: inv.approved_signature_image || { has_custom: false },
  };
}

function AssetSlot({
  title,
  slot,
  asset,
  previewUrl,
  canEdit,
  busy,
  accept,
  onPick,
  onRemove,
  removeLabel,
  uploadLabel,
  statusCustom,
  statusDefault,
  fileInputRef,
  testIdPrefix,
}) {
  const hasCustom = Boolean(asset?.has_custom);
  return (
    <div className="settings-grid" data-testid={`${testIdPrefix}-slot`} style={{ gridColumn: "1 / -1" }}>
      <div style={{ gridColumn: "1 / -1" }}>
        <strong>{title}</strong>
        <p className="muted small" style={{ marginTop: 2 }} data-testid={`${testIdPrefix}-status`}>
          {hasCustom ? (statusCustom || "Custom") : (statusDefault || "Default")}
          {hasCustom && asset?.filename ? ` · ${asset.filename}` : ""}
        </p>
      </div>
      <div style={{ gridColumn: "1 / -1", display: "flex", gap: 16, alignItems: "flex-start", flexWrap: "wrap" }}>
        <div
          data-testid={`${testIdPrefix}-preview`}
          style={{
            width: 160,
            minHeight: 72,
            border: "1px solid var(--border, #ddd)",
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            background: "#fafafa",
            padding: 8,
          }}
        >
          {previewUrl ? (
            <img src={previewUrl} alt={title} style={{ maxWidth: "100%", maxHeight: 64, objectFit: "contain" }} />
          ) : (
            <span className="muted small">{hasCustom ? "Memuat…" : "Tidak ada gambar custom"}</span>
          )}
        </div>
        <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
          <input
            ref={fileInputRef}
            type="file"
            accept={accept}
            hidden
            data-testid={`${testIdPrefix}-file`}
            onChange={(e) => {
              const file = e.target.files?.[0];
              e.target.value = "";
              if (file) onPick(file);
            }}
          />
          <button
            type="button"
            className="secondary"
            data-testid={`${testIdPrefix}-upload`}
            disabled={!canEdit || busy}
            onClick={() => fileInputRef.current?.click()}
          >
            {uploadLabel || (hasCustom ? "Ganti" : "Unggah")}
          </button>
          {hasCustom ? (
            <button
              type="button"
              className="secondary"
              data-testid={`${testIdPrefix}-remove`}
              disabled={!canEdit || busy}
              onClick={onRemove}
            >
              {removeLabel}
            </button>
          ) : null}
        </div>
      </div>
    </div>
  );
}

export default function DesignCetakanSettings({ notify, canEdit }) {
  const [tab, setTab] = useState("umum");
  const [draft, setDraft] = useState(emptyDraft);
  const [assets, setAssets] = useState(emptyAssets);
  const [previews, setPreviews] = useState({});
  const [meta, setMeta] = useState({ updated_at: null, updated_by: null });
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  const logoInputRef = useRef(null);
  const preparedInputRef = useRef(null);
  const approvedInputRef = useRef(null);
  const objectUrlsRef = useRef([]);

  const revokeAllPreviews = () => {
    objectUrlsRef.current.forEach((url) => {
      try {
        URL.revokeObjectURL(url);
      } catch (_) {
        /* ignore */
      }
    });
    objectUrlsRef.current = [];
  };

  const applySettings = (s) => {
    setDraft(fromApi(s));
    setAssets(assetsFromApi(s));
    setMeta({ updated_at: s.updated_at || null, updated_by: s.updated_by || null });
  };

  const loadPreviews = async (nextAssets) => {
    revokeAllPreviews();
    const next = {};
    const slots = ["company_logo", "prepared_signature_image", "approved_signature_image"];
    await Promise.all(
      slots.map(async (slot) => {
        if (!nextAssets?.[slot]?.has_custom) return;
        try {
          const blob = await api.designCetakanAssetPreview(slot);
          const url = URL.createObjectURL(blob);
          objectUrlsRef.current.push(url);
          next[slot] = url;
        } catch (_) {
          /* preview optional — status still shows custom */
        }
      })
    );
    setPreviews(next);
  };

  const load = () => {
    setBusy(true);
    setErr("");
    api.designCetakanSettings()
      .then(async (res) => {
        const s = res?.settings || {};
        applySettings(s);
        await loadPreviews(assetsFromApi(s));
      })
      .catch((e) => setErr(errDetail(e)))
      .finally(() => setBusy(false));
  };

  useEffect(() => {
    load();
    return () => revokeAllPreviews();
    // eslint-disable-next-line react-hooks/exhaustive-deps
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
      applySettings(s);
      await loadPreviews(assetsFromApi(s));
      notify?.(res.message || "Design Cetakan berhasil disimpan");
    } catch (ex) {
      setErr(errDetail(ex));
    } finally {
      setBusy(false);
    }
  };

  const uploadSlot = async (slot, file) => {
    if (!canEdit || busy || !file) return;
    const type = String(file.type || "").toLowerCase();
    if (type && !["image/png", "image/jpeg", "image/jpg", "image/webp"].includes(type)) {
      setErr("File harus berupa gambar PNG, JPEG, atau WEBP.");
      return;
    }
    setBusy(true);
    setErr("");
    // Local object-URL preview before upload completes
    const localUrl = URL.createObjectURL(file);
    objectUrlsRef.current.push(localUrl);
    setPreviews((prev) => ({ ...prev, [slot]: localUrl }));
    try {
      const res = await api.uploadDesignCetakanAsset(slot, file);
      const s = res?.settings || {};
      applySettings(s);
      await loadPreviews(assetsFromApi(s));
      notify?.(res.message || "Asset berhasil diunggah");
    } catch (ex) {
      setErr(errDetail(ex));
      await loadPreviews(assets);
    } finally {
      setBusy(false);
    }
  };

  const removeSlot = async (slot) => {
    if (!canEdit || busy) return;
    setBusy(true);
    setErr("");
    try {
      const res = await api.removeDesignCetakanAsset(slot);
      const s = res?.settings || {};
      applySettings(s);
      await loadPreviews(assetsFromApi(s));
      notify?.(res.message || "Asset dihapus");
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
          Letterhead PDF Pesanan Penjualan, Faktur/Proforma, dan Surat Jalan. Logo perusahaan dan tanda tangan Faktur/Proforma dapat diganti di sini.
        </p>
      </div>

      <div className="settings-info" style={{ marginBottom: 16 }}>
        <Info size={16} />
        <div>
          <p><strong>Default mengikuti PDF yang sudah deploy.</strong></p>
          <p className="muted small" style={{ marginTop: 2 }}>
            Judul dokumen, layout, dan nomor halaman tetap fixed. Rekening di sini hanya teks presentasi PDF, bukan master Kas/Bank.
            Tanda tangan PO dan POD Surat Jalan tidak diubah di Design Cetakan.
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
            <AssetSlot
              title="Logo Perusahaan"
              slot="company_logo"
              asset={assets.company_logo}
              previewUrl={previews.company_logo}
              canEdit={canEdit}
              busy={busy}
              accept={LOGO_ACCEPT}
              onPick={(file) => uploadSlot("company_logo", file)}
              onRemove={() => removeSlot("company_logo")}
              removeLabel="Gunakan Logo Default"
              uploadLabel="Ganti Logo"
              statusCustom="Custom"
              statusDefault="Default (logo repo)"
              fileInputRef={logoInputRef}
              testIdPrefix="design-cetakan-logo"
            />
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
            <AssetSlot
              title="Tanda Tangan — Disiapkan Oleh"
              slot="prepared_signature_image"
              asset={assets.prepared_signature_image}
              previewUrl={previews.prepared_signature_image}
              canEdit={canEdit}
              busy={busy}
              accept={SIGNATURE_ACCEPT}
              onPick={(file) => uploadSlot("prepared_signature_image", file)}
              onRemove={() => removeSlot("prepared_signature_image")}
              removeLabel="Hapus"
              uploadLabel="Ganti"
              statusCustom="Custom"
              statusDefault="Teks saja"
              fileInputRef={preparedInputRef}
              testIdPrefix="design-cetakan-prepared-sig"
            />
            <AssetSlot
              title="Tanda Tangan / Stempel — Disetujui Oleh"
              slot="approved_signature_image"
              asset={assets.approved_signature_image}
              previewUrl={previews.approved_signature_image}
              canEdit={canEdit}
              busy={busy}
              accept={SIGNATURE_ACCEPT}
              onPick={(file) => uploadSlot("approved_signature_image", file)}
              onRemove={() => removeSlot("approved_signature_image")}
              removeLabel="Hapus"
              uploadLabel="Ganti"
              statusCustom="Custom"
              statusDefault="Teks saja"
              fileInputRef={approvedInputRef}
              testIdPrefix="design-cetakan-approved-sig"
            />
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
