import { useEffect, useMemo, useRef, useState } from "react";
import { AlertCircle, CheckCircle2, Copy, Eye, History, Image, Info, Lock, Save, X } from "lucide-react";
import api from "../lib/api";
import { errDetail } from "../lib/purchaseUi";
import "./DesignCetakanSettings.css";

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

const BRANCH_OVERRIDE_FIELDS = [
  "company_name",
  "address_lines",
  "company_logo",
  "bank_accounts",
  "prepared_label",
  "prepared_footer",
  "approved_label",
  "approved_footer",
  "prepared_signature_image",
  "approved_signature_image",
];

const COPY_FIELD_LABELS = {
  company_name: "Nama perusahaan",
  address_lines: "Alamat",
  company_logo: "Logo perusahaan",
  bank_accounts: "Rekening bank",
  prepared_label: "Label Prepared",
  prepared_footer: "Footer Prepared",
  approved_label: "Label Approved",
  approved_footer: "Footer Approved",
  prepared_signature_image: "Tanda tangan Prepared",
  approved_signature_image: "Tanda tangan Approved",
};

function copyFieldLabel(key) {
  return COPY_FIELD_LABELS[key] || key;
}

function copyableKeysFromSettings(settings) {
  const s = settings || {};
  const inv = s.invoice || {};
  const keys = [];
  if (String(s.company_name || "").trim()) keys.push("company_name");
  if ((s.address_lines || []).some((line) => String(line || "").trim())) keys.push("address_lines");
  if (s.company_logo && s.company_logo.has_custom) keys.push("company_logo");
  if ((inv.bank_accounts || []).some((row) => row && (row.bank_label || row.account_number || row.account_name))) {
    keys.push("bank_accounts");
  }
  ["prepared_label", "prepared_footer", "approved_label", "approved_footer"].forEach((field) => {
    if (String(inv[field] || "").trim()) keys.push(field);
  });
  if (inv.prepared_signature_image && inv.prepared_signature_image.has_custom) keys.push("prepared_signature_image");
  if (inv.approved_signature_image && inv.approved_signature_image.has_custom) keys.push("approved_signature_image");
  return keys;
}

function copyableKeysFromOverride(override) {
  const o = override || {};
  const inv = o.invoice || {};
  const keys = [];
  if (o.company_name) keys.push("company_name");
  if (Array.isArray(o.address_lines) && o.address_lines.length) keys.push("address_lines");
  if (o.company_logo && o.company_logo.has_custom) keys.push("company_logo");
  if (Array.isArray(inv.bank_accounts) && inv.bank_accounts.length) keys.push("bank_accounts");
  ["prepared_label", "prepared_footer", "approved_label", "approved_footer"].forEach((field) => {
    if (inv[field]) keys.push(field);
  });
  if (inv.prepared_signature_image && inv.prepared_signature_image.has_custom) keys.push("prepared_signature_image");
  if (inv.approved_signature_image && inv.approved_signature_image.has_custom) keys.push("approved_signature_image");
  return keys;
}

function emptyOverrideFlags() {
  return BRANCH_OVERRIDE_FIELDS.reduce((acc, key) => {
    acc[key] = false;
    return acc;
  }, {});
}

function flagsFromInherited(inherited) {
  const flags = emptyOverrideFlags();
  const src = inherited || {};
  BRANCH_OVERRIDE_FIELDS.forEach((key) => {
    flags[key] = Boolean(src[key] && src[key].overridden);
  });
  return flags;
}

function buildBranchOverridePayload(draft, overrideOn) {
  const on = overrideOn || {};
  const payload = {};
  const invoice = {};
  if (on.company_name) payload.company_name = draft.company_name;
  if (on.address_lines) {
    payload.address_lines = (draft.address_lines || []).filter((line) => String(line || "").trim());
  }
  if (on.bank_accounts) {
    invoice.bank_accounts = (draft.invoice.bank_accounts || []).filter(
      (row) => row.bank_label || row.account_number || row.account_name
    );
  }
  ["prepared_label", "prepared_footer", "approved_label", "approved_footer"].forEach((field) => {
    if (on[field]) invoice[field] = draft.invoice[field];
  });
  if (Object.keys(invoice).length) payload.invoice = invoice;
  return payload;
}

function restoreFieldFromGlobal(draft, field, globalSettings) {
  const base = fromApi(globalSettings);
  const next = JSON.parse(JSON.stringify(draft));
  if (field === "company_name") next.company_name = base.company_name;
  else if (field === "address_lines") next.address_lines = base.address_lines;
  else if (field === "bank_accounts") next.invoice.bank_accounts = base.invoice.bank_accounts;
  else if (["prepared_label", "prepared_footer", "approved_label", "approved_footer"].includes(field)) {
    next.invoice[field] = base.invoice[field];
  }
  return next;
}

function GlobalOnlyMark({ testid, children }) {
  return (
    <span className="dc-badge is-lock" data-testid={testid}>
      <Lock size={12} aria-hidden="true" />
      {children || "Mengikuti Global"}
    </span>
  );
}

function OverrideBar({ field, branchMode, enabled, canEdit, busy, onEnable, onFollow }) {
  if (!branchMode) return null;
  return (
    <span className="dc-inherit" data-testid={`design-cetakan-inherit-${field}`}>
      <span className={`dc-badge ${enabled ? "is-branch" : "is-global"}`} data-testid={`design-cetakan-badge-${field}`}>
        {enabled ? "Khusus Cabang" : "Mengikuti Global"}
      </span>
      {canEdit && !enabled ? (
        <button type="button" className="secondary" data-testid={`design-cetakan-override-${field}`} disabled={busy} onClick={onEnable}>
          Atur khusus cabang
        </button>
      ) : null}
      {canEdit && enabled ? (
        <button type="button" className="secondary" data-testid={`design-cetakan-follow-${field}`} disabled={busy} onClick={onFollow}>
          Ikuti Global
        </button>
      ) : null}
    </span>
  );
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
  statusLabel,
  allowRemove,
  fileInputRef,
  testIdPrefix,
  emptyLabel,
}) {
  const hasCustom = Boolean(asset?.has_custom);
  const canRemove = allowRemove === undefined ? hasCustom : allowRemove;
  return (
    <div className="dc-asset" data-testid={`${testIdPrefix}-slot`}>
      <div className="dc-asset-thumb" data-testid={`${testIdPrefix}-preview`}>
        {previewUrl ? (
          <img src={previewUrl} alt={title} />
        ) : hasCustom ? (
          <span className="muted small">Memuat…</span>
        ) : (
          <span className="dc-asset-empty">
            <Image size={14} aria-hidden="true" />
            <span>{emptyLabel || "Belum ada gambar"}</span>
          </span>
        )}
      </div>
      <div className="dc-asset-copy">
        <strong>{title}</strong>
        <p className="muted small" data-testid={`${testIdPrefix}-status`}>
          {statusLabel || (hasCustom ? (statusCustom || "Custom") : (statusDefault || "Default"))}
          {hasCustom && asset?.filename ? ` · ${asset.filename}` : ""}
        </p>
      </div>
      <div className="dc-asset-actions">
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
        {canRemove ? (
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
  );
}

function formatHistoryAt(iso) {
  const raw = String(iso || "").trim();
  if (!raw) return "";
  const d = new Date(raw);
  if (Number.isNaN(d.getTime())) return raw;
  const months = ["Jan", "Feb", "Mar", "Apr", "Mei", "Jun", "Jul", "Agt", "Sep", "Okt", "Nov", "Des"];
  const dd = String(d.getDate()).padStart(2, "0");
  const hh = String(d.getHours()).padStart(2, "0");
  const mm = String(d.getMinutes()).padStart(2, "0");
  return `${dd} ${months[d.getMonth()] || ""} ${d.getFullYear()} · ${hh}:${mm}`;
}

export default function DesignCetakanSettings({ notify, canEdit }) {
  const [tab, setTab] = useState("umum");
  const [scope, setScope] = useState("global");
  const [catalog, setCatalog] = useState([]);
  const [draft, setDraft] = useState(emptyDraft);
  const [assets, setAssets] = useState(emptyAssets);
  const [overrideOn, setOverrideOn] = useState(emptyOverrideFlags);
  const [previews, setPreviews] = useState({});
  const [meta, setMeta] = useState({ updated_at: null, updated_by: null });
  const [busy, setBusy] = useState(false);
  const [previewBusy, setPreviewBusy] = useState(false);
  const [previewOpen, setPreviewOpen] = useState(false);
  const [historyOpen, setHistoryOpen] = useState(false);
  const [historyScope, setHistoryScope] = useState("all");
  const [historyBranchId, setHistoryBranchId] = useState("");
  const [historyItems, setHistoryItems] = useState([]);
  const [historyPage, setHistoryPage] = useState(1);
  const [historyHasMore, setHistoryHasMore] = useState(false);
  const [historyBusy, setHistoryBusy] = useState(false);
  const [historyErr, setHistoryErr] = useState("");
  const [copyOpen, setCopyOpen] = useState(false);
  const [copySource, setCopySource] = useState("global");
  const [copyTarget, setCopyTarget] = useState("");
  const [copySourceOverride, setCopySourceOverride] = useState(null);
  const [copyTargetOverride, setCopyTargetOverride] = useState(null);
  const [copyBusy, setCopyBusy] = useState(false);
  const [copyErr, setCopyErr] = useState("");
  const [err, setErr] = useState("");
  const [dirty, setDirty] = useState(false);
  const globalSnapshotRef = useRef(null);
  const loadRequestRef = useRef(0);
  const scopeRef = useRef("global");
  const previewWrapRef = useRef(null);
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

  const branchMode = scope !== "global";

  const loadPreviews = async (nextAssets, activeScope, flags, requestId) => {
    if (requestId !== loadRequestRef.current) return;
    revokeAllPreviews();
    const next = {};
    const slots = ["company_logo", "prepared_signature_image", "approved_signature_image"];
    await Promise.all(
      slots.map(async (slot) => {
        if (!nextAssets?.[slot]?.has_custom) return;
        try {
          const useBranch = activeScope && activeScope !== "global" && flags?.[slot];
          const blob = useBranch
            ? await api.designCetakanBranchAssetPreview(activeScope, slot)
            : await api.designCetakanAssetPreview(slot);
          const url = URL.createObjectURL(blob);
          objectUrlsRef.current.push(url);
          next[slot] = url;
        } catch (_) {
          /* preview optional — status still shows custom */
        }
      })
    );
    if (requestId !== loadRequestRef.current) return;
    setPreviews(next);
  };

  const markClean = () => setDirty(false);

  const applyBranchView = (view) => {
    const effective = view?.effective || {};
    applySettings(effective);
    const flags = flagsFromInherited(view?.inherited);
    setOverrideOn(flags);
    return { assets: assetsFromApi(effective), flags };
  };

  const load = (activeScope) => {
    const requestId = ++loadRequestRef.current;
    const target = activeScope || "global";
    const current = () => requestId === loadRequestRef.current && scopeRef.current === target;
    setBusy(true);
    setErr("");
    api.designCetakanSettings()
      .then(async (globalRes) => {
        if (!current()) return;
        const globalSettings = globalRes?.settings || {};
        globalSnapshotRef.current = globalSettings;
        if (target === "global") {
          applySettings(globalSettings);
          setOverrideOn(emptyOverrideFlags());
          markClean();
          await loadPreviews(assetsFromApi(globalSettings), "global", emptyOverrideFlags(), requestId);
          return;
        }
        const branchRes = await api.designCetakanBranch(target);
        if (!current()) return;
        const applied = applyBranchView(branchRes);
        markClean();
        await loadPreviews(applied.assets, target, applied.flags, requestId);
      })
      .catch((e) => {
        if (!current()) return;
        setErr(errDetail(e));
      })
      .finally(() => {
        if (requestId !== loadRequestRef.current) return;
        setBusy(false);
      });
  };

  useEffect(() => {
    api.designCetakanBranches()
      .then((res) => setCatalog(Array.isArray(res?.branches) ? res.branches : []))
      .catch((e) => setErr(errDetail(e)));
  }, []);

  useEffect(() => {
    load(scope);
    return () => revokeAllPreviews();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [scope]);

  const requestScopeChange = (next) => {
    const target = next || "global";
    if (target === scopeRef.current) return;
    if (dirty && !window.confirm("Perubahan yang belum disimpan akan dibuang. Lanjutkan?")) return;
    scopeRef.current = target;
    setScope(target);
  };

  useEffect(() => {
    if (!historyOpen) return undefined;
    let cancelled = false;
    setHistoryBusy(true);
    setHistoryErr("");
    setHistoryItems([]);
    setHistoryPage(1);
    const params = { scope: historyScope, page: 1, page_size: 20 };
    if (historyScope === "branch" && historyBranchId) params.branch_id = historyBranchId;
    api.getDesignCetakanHistory(params)
      .then((res) => {
        if (cancelled) return;
        setHistoryItems(Array.isArray(res?.items) ? res.items : []);
        setHistoryPage(res?.page || 1);
        setHistoryHasMore(Boolean(res?.has_more));
      })
      .catch((e) => {
        if (!cancelled) setHistoryErr(errDetail(e));
      })
      .finally(() => {
        if (!cancelled) setHistoryBusy(false);
      });
    return () => {
      cancelled = true;
    };
  }, [historyOpen, historyScope, historyBranchId]);

  useEffect(() => {
    if (!previewOpen) return undefined;
    const onPointer = (event) => {
      if (!previewWrapRef.current?.contains(event.target)) setPreviewOpen(false);
    };
    const onKey = (event) => {
      if (event.key === "Escape") setPreviewOpen(false);
    };
    document.addEventListener("mousedown", onPointer);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onPointer);
      document.removeEventListener("keydown", onKey);
    };
  }, [previewOpen]);

  const openHistory = () => {
    setCopyOpen(false);
    setHistoryScope("all");
    setHistoryBranchId("");
    setHistoryOpen(true);
  };

  const loadMoreHistory = async () => {
    if (historyBusy || !historyHasMore) return;
    setHistoryBusy(true);
    setHistoryErr("");
    try {
      const next = historyPage + 1;
      const params = { scope: historyScope, page: next, page_size: 20 };
      if (historyScope === "branch" && historyBranchId) params.branch_id = historyBranchId;
      const res = await api.getDesignCetakanHistory(params);
      setHistoryItems((prev) => [...prev, ...(Array.isArray(res?.items) ? res.items : [])]);
      setHistoryPage(res?.page || next);
      setHistoryHasMore(Boolean(res?.has_more));
    } catch (ex) {
      setHistoryErr(errDetail(ex));
    } finally {
      setHistoryBusy(false);
    }
  };

  const openCopy = () => {
    setHistoryOpen(false);
    setCopySource(scope === "global" ? "global" : String(scope));
    setCopyTarget("");
    setCopySourceOverride(null);
    setCopyTargetOverride(null);
    setCopyErr("");
    setCopyOpen(true);
  };

  const copySourceKeys = copySource === "global"
    ? copyableKeysFromSettings(globalSnapshotRef.current)
    : copyableKeysFromOverride(copySourceOverride);
  const copyTargetKeys = copyableKeysFromOverride(copyTargetOverride);
  const copyReplaceKeys = copySourceKeys.filter((key) => copyTargetKeys.includes(key));
  const copyDropKeys = copyTargetKeys.filter((key) => !copySourceKeys.includes(key));
  const copySame = Boolean(copyTarget) && copySource !== "global" && String(copySource) === String(copyTarget);
  const copyEmptyBranch = copySource !== "global" && copySourceKeys.length === 0;
  const copyBlocked = copyBusy || !copyTarget || copySame || copyEmptyBranch || (copySource !== "global" && !copySourceOverride && !copyErr);

  useEffect(() => {
    if (!copyOpen) return undefined;
    let cancelled = false;
    const loadOverride = async (branchId, setter) => {
      if (!branchId) {
        setter(null);
        return;
      }
      try {
        const view = await api.designCetakanBranch(branchId);
        if (!cancelled) setter(view?.override || {});
      } catch (ex) {
        if (!cancelled) {
          setter(null);
          setCopyErr(errDetail(ex));
        }
      }
    };
    setCopyErr("");
    if (copySource === "global") {
      setCopySourceOverride(null);
    } else {
      loadOverride(copySource, setCopySourceOverride);
    }
    if (copyTarget) loadOverride(copyTarget, setCopyTargetOverride);
    else setCopyTargetOverride(null);
    return () => {
      cancelled = true;
    };
  }, [copyOpen, copySource, copyTarget]);

  const submitCopy = async () => {
    if (!canEdit || copyBlocked) return;
    setCopyBusy(true);
    setCopyErr("");
    try {
      const payload = {
        source_scope: copySource === "global" ? "global" : "branch",
        target_branch_id: copyTarget,
      };
      if (payload.source_scope === "branch") payload.source_branch_id = copySource;
      const res = await api.copyDesignCetakan(payload);
      notify?.(res.message || "Pengaturan khusus Design Cetakan berhasil disalin");
      setCopyOpen(false);
      if (scope === copyTarget) {
        const applied = applyBranchView(res?.branch || {});
        await loadPreviews(applied.assets, copyTarget, applied.flags, loadRequestRef.current);
        markClean();
      }
      if (historyOpen) {
        setHistoryItems([]);
        setHistoryPage(1);
        try {
          const hist = await api.getDesignCetakanHistory({
            scope: historyScope,
            page: 1,
            page_size: 20,
            ...(historyScope === "branch" && historyBranchId ? { branch_id: historyBranchId } : {}),
          });
          setHistoryItems(Array.isArray(hist?.items) ? hist.items : []);
          setHistoryHasMore(Boolean(hist?.has_more));
        } catch (_) {
          /* history refresh is optional */
        }
      }
    } catch (ex) {
      setCopyErr(errDetail(ex));
    } finally {
      setCopyBusy(false);
    }
  };

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
    setDirty(true);
  };

  const save = async (e) => {
    e?.preventDefault();
    if (!canEdit || busy) return;
    const requestId = loadRequestRef.current;
    const target = scopeRef.current;
    const stillHere = () => requestId === loadRequestRef.current && scopeRef.current === target;
    setBusy(true);
    setErr("");
    try {
      if (branchMode) {
        const payload = buildBranchOverridePayload(draft, overrideOn);
        const res = await api.updateDesignCetakanBranch(scope, payload);
        if (!stillHere()) return;
        const applied = applyBranchView(res?.branch || {});
        await loadPreviews(applied.assets, scope, applied.flags, requestId);
        markClean();
        notify?.(res.message || "Design Cetakan cabang berhasil disimpan");
      } else {
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
        if (!stillHere()) return;
        const s = res?.settings || {};
        applySettings(s);
        await loadPreviews(assetsFromApi(s), "global", emptyOverrideFlags(), requestId);
        markClean();
        notify?.(res.message || "Design Cetakan berhasil disimpan");
      }
    } catch (ex) {
      if (stillHere()) setErr(errDetail(ex));
    } finally {
      if (requestId !== loadRequestRef.current) return;
      setBusy(false);
    }
  };

  const uploadSlot = async (slot, file) => {
    if (!canEdit || busy || !file) return;
    const requestId = loadRequestRef.current;
    const target = scopeRef.current;
    const stillHere = () => requestId === loadRequestRef.current && scopeRef.current === target;
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
      const res = branchMode
        ? await api.uploadDesignCetakanBranchAsset(scope, slot, file)
        : await api.uploadDesignCetakanAsset(slot, file);
      if (!stillHere()) return;
      if (branchMode) {
        const applied = applyBranchView(res?.branch || {});
        await loadPreviews(applied.assets, scope, applied.flags, requestId);
      } else {
        const s = res?.settings || {};
        applySettings(s);
        await loadPreviews(assetsFromApi(s), "global", emptyOverrideFlags(), requestId);
      }
      markClean();
      notify?.(res.message || "Asset berhasil diunggah");
    } catch (ex) {
      if (!stillHere()) return;
      setErr(errDetail(ex));
      await loadPreviews(assets, target, overrideOn, requestId);
    } finally {
      if (requestId !== loadRequestRef.current) return;
      setBusy(false);
    }
  };

  const removeSlot = async (slot) => {
    if (!canEdit || busy) return;
    const requestId = loadRequestRef.current;
    const stillHere = () => requestId === loadRequestRef.current && scopeRef.current === scope;
    setBusy(true);
    setErr("");
    try {
      const res = branchMode
        ? await api.removeDesignCetakanBranchAsset(scope, slot)
        : await api.removeDesignCetakanAsset(slot);
      if (!stillHere()) return;
      if (branchMode) {
        const applied = applyBranchView(res?.branch || {});
        await loadPreviews(applied.assets, scope, applied.flags, requestId);
      } else {
        const s = res?.settings || {};
        applySettings(s);
        await loadPreviews(assetsFromApi(s), "global", emptyOverrideFlags(), requestId);
      }
      markClean();
      notify?.(res.message || "Asset dihapus");
    } catch (ex) {
      if (stillHere()) setErr(errDetail(ex));
    } finally {
      if (requestId !== loadRequestRef.current) return;
      setBusy(false);
    }
  };

  const enableOverride = (field) => {
    if (!canEdit || busy || !branchMode) return;
    setOverrideOn((prev) => ({ ...prev, [field]: true }));
    setDirty(true);
  };

  const followGlobal = (field) => {
    if (!canEdit || busy || !branchMode) return;
    setOverrideOn((prev) => ({ ...prev, [field]: false }));
    setDraft((prev) => restoreFieldFromGlobal(prev, field, globalSnapshotRef.current));
    setDirty(true);
  };

  const resetBranch = async () => {
    if (!canEdit || busy || !branchMode) return;
    if (!window.confirm("Pengaturan khusus cabang akan dihapus dan cabang kembali mengikuti Global. Perubahan yang belum disimpan juga akan dibuang.")) return;
    const requestId = loadRequestRef.current;
    const target = scopeRef.current;
    const stillHere = () => requestId === loadRequestRef.current && scopeRef.current === target;
    setBusy(true);
    setErr("");
    try {
      const res = await api.resetDesignCetakanBranch(scope);
      if (!stillHere()) return;
      const applied = applyBranchView(res?.branch || {});
      await loadPreviews(applied.assets, scope, applied.flags, requestId);
      markClean();
      notify?.(res.message || "Cabang kembali mengikuti pengaturan global");
    } catch (ex) {
      if (stillHere()) setErr(errDetail(ex));
    } finally {
      if (requestId !== loadRequestRef.current) return;
      setBusy(false);
    }
  };

  const fieldLocked = (field) => !canEdit || busy || (branchMode && !overrideOn[field]);
  const globalOnlyLocked = !canEdit || busy || branchMode;
  const previewLocked = busy || previewBusy;

  const openPreview = async (documentType) => {
    if (previewLocked) return;
    setPreviewBusy(true);
    setErr("");
    try {
      const blob = await api.previewDesignCetakan(documentType, branchMode ? scope : undefined);
      const url = URL.createObjectURL(blob);
      const opened = window.open(url, "_blank");
      setTimeout(() => {
        try {
          URL.revokeObjectURL(url);
        } catch (_) {
          /* ignore */
        }
      }, 5000);
      if (!opened) {
        setErr("Popup diblokir. Izinkan jendela baru untuk melihat preview.");
      }
    } catch (ex) {
      setErr(errDetail(ex));
    } finally {
      setPreviewBusy(false);
    }
  };

  const saveState = dirty ? "Perubahan belum disimpan" : "Tersimpan";

  return (
    <div className="settings-card dc-page" data-testid="design-cetakan-settings">
      <header className="dc-header">
        <div>
          <h2>Design Cetakan</h2>
          <p className="muted small">Atur identitas, elemen PDF, dan pengaturan per cabang.</p>
        </div>
        <span className={`dc-badge ${dirty ? "is-dirty" : "is-saved"}`} data-testid="design-cetakan-save-state" role="status">
          {dirty ? <AlertCircle size={14} aria-hidden="true" /> : <CheckCircle2 size={14} aria-hidden="true" />}
          {saveState}
        </span>
      </header>

      <div className="dc-info" data-testid="design-cetakan-info">
        <Info size={15} aria-hidden="true" />
        <p>
          Layout & judul dokumen tetap · Rekening hanya teks PDF · Preview menggunakan pengaturan yang sudah disimpan.
          Tanda tangan PO dan POD Surat Jalan tidak diubah di Design Cetakan.
        </p>
      </div>

      <div className="dc-command" data-testid="design-cetakan-command">
        <label className="dc-scope" data-testid="design-cetakan-scope-wrap">
          <span>Terapkan Untuk</span>
          <select
            data-testid="design-cetakan-scope"
            value={scope}
            onChange={(e) => requestScopeChange(e.target.value)}
          >
            <option value="global">Default Semua Cabang</option>
            {catalog.map((row) => (
              <option key={row.branch_id} value={String(row.branch_id)}>
                {row.name || `Cabang ${row.branch_id}`}
              </option>
            ))}
          </select>
          {catalog.length === 0 ? (
            <p className="muted small dc-empty" data-testid="design-cetakan-branch-empty">
              Belum ada cabang pada order. Default Semua Cabang tetap bisa diubah. Cabang muncul setelah ada order dengan branch_id.
            </p>
          ) : null}
        </label>
        <div className="dc-command-actions">
          <div className="dc-preview" ref={previewWrapRef}>
            <button
              type="button"
              className="secondary"
              data-testid="design-cetakan-preview-open"
              aria-haspopup="menu"
              aria-expanded={previewOpen}
              aria-controls="design-cetakan-preview-menu"
              disabled={previewLocked}
              onClick={() => setPreviewOpen((open) => !open)}
            >
              <Eye size={15} aria-hidden="true" />
              {previewBusy ? "Memuat…" : "Preview"}
            </button>
            {previewOpen ? (
              <div id="design-cetakan-preview-menu" className="dc-menu" role="menu" data-testid="design-cetakan-preview-menu">
                <button type="button" role="menuitem" className="dc-menu-item" data-testid="design-cetakan-preview-po" disabled={previewLocked} onClick={() => { setPreviewOpen(false); openPreview("po"); }}>
                  Preview PO
                </button>
                <button type="button" role="menuitem" className="dc-menu-item" data-testid="design-cetakan-preview-invoice" disabled={previewLocked} onClick={() => { setPreviewOpen(false); openPreview("invoice"); }}>
                  Preview Faktur
                </button>
                <button type="button" role="menuitem" className="dc-menu-item" data-testid="design-cetakan-preview-proforma" disabled={previewLocked} onClick={() => { setPreviewOpen(false); openPreview("proforma"); }}>
                  Preview Proforma
                </button>
                <button type="button" role="menuitem" className="dc-menu-item" data-testid="design-cetakan-preview-delivery" disabled={previewLocked} onClick={() => { setPreviewOpen(false); openPreview("delivery"); }}>
                  Preview Surat Jalan
                </button>
                <p className="muted small" data-testid="design-cetakan-preview-note">
                  Preview mengikuti pengaturan yang sudah disimpan.
                </p>
              </div>
            ) : null}
          </div>
          <button
            type="button"
            className="secondary"
            data-testid="design-cetakan-history-open"
            onClick={openHistory}
          >
            <History size={15} aria-hidden="true" />
            Riwayat
          </button>
          {canEdit ? (
            <button
              type="button"
              className="secondary"
              data-testid="design-cetakan-copy-open"
              onClick={openCopy}
            >
              <Copy size={15} aria-hidden="true" />
              Salin Pengaturan
            </button>
          ) : null}
        </div>
      </div>

      {err ? (
        <div className="signal-strip" role="alert" data-testid="design-cetakan-error" style={{ marginBottom: 16 }}>
          <AlertCircle size={16} />
          <span>{err}</span>
        </div>
      ) : null}

      <div className="dc-tabs" role="tablist" aria-label="Bagian Design Cetakan">
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

      <form className="dc-form" onSubmit={save}>
        <div className="dc-main">
        {tab === "umum" ? (
          <div data-testid="design-cetakan-umum">
            <section className="dc-section">
              <h3>Identitas Perusahaan</h3>
              <div className="dc-fields">
                <label>
                  <span className="dc-label-row">
                    Nama perusahaan
                    <OverrideBar
                      field="company_name"
                      branchMode={branchMode}
                      enabled={overrideOn.company_name}
                      canEdit={canEdit}
                      busy={busy}
                      onEnable={() => enableOverride("company_name")}
                      onFollow={() => followGlobal("company_name")}
                    />
                  </span>
                  <input
                    data-testid="design-cetakan-company-name"
                    value={draft.company_name}
                    disabled={fieldLocked("company_name")}
                    onChange={(e) => patch("company_name", e.target.value)}
                  />
                </label>
                <div className="dc-address">
                  <span className="dc-label-row">
                    Alamat
                    <OverrideBar
                      field="address_lines"
                      branchMode={branchMode}
                      enabled={overrideOn.address_lines}
                      canEdit={canEdit}
                      busy={busy}
                      onEnable={() => enableOverride("address_lines")}
                      onFollow={() => followGlobal("address_lines")}
                    />
                  </span>
                  {draft.address_lines.map((line, idx) => (
                    <label key={`addr-${idx}`}>
                      Alamat baris {idx + 1}
                      <input
                        data-testid={`design-cetakan-address-${idx}`}
                        value={line}
                        disabled={fieldLocked("address_lines")}
                        onChange={(e) => patch(`address_lines.${idx}`, e.target.value)}
                      />
                    </label>
                  ))}
                </div>
                <label>
                  <span className="dc-label-row">
                    Warna aksen
                    {branchMode ? <GlobalOnlyMark testid="design-cetakan-global-only-accent" /> : null}
                  </span>
                  <input
                    data-testid="design-cetakan-accent"
                    value={draft.accent_color}
                    disabled={globalOnlyLocked}
                    onChange={(e) => patch("accent_color", e.target.value)}
                    placeholder="#cc1f1a"
                  />
                </label>
              </div>
            </section>
            <section className="dc-section">
              <h3>Logo Perusahaan</h3>
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
                removeLabel={branchMode ? "Ikuti Global" : "Gunakan Logo Default"}
                uploadLabel={branchMode ? "Ganti khusus cabang" : "Ganti Logo"}
                statusCustom="Custom"
                statusDefault="Default (logo repo)"
                statusLabel={branchMode ? (overrideOn.company_logo ? "Khusus Cabang" : "Mengikuti Global") : undefined}
                allowRemove={branchMode ? Boolean(overrideOn.company_logo) : undefined}
                fileInputRef={logoInputRef}
                testIdPrefix="design-cetakan-logo"
              />
            </section>
          </div>
        ) : null}

        {tab === "po" ? (
          <div data-testid="design-cetakan-po">
            <section className="dc-section">
              <h3 className="dc-label-row">
                Tanda Tangan PO
                {branchMode ? <GlobalOnlyMark testid="design-cetakan-global-only-po" /> : null}
              </h3>
              <div className="dc-fields dc-fields-3">
                {draft.po.signature_labels.map((label, idx) => (
                  <label key={`po-sig-${idx}`}>
                    Label tanda tangan {idx + 1}
                    <input
                      data-testid={`design-cetakan-po-sig-${idx}`}
                      value={label}
                      disabled={globalOnlyLocked}
                      onChange={(e) => patch(`po.signature_labels.${idx}`, e.target.value)}
                    />
                  </label>
                ))}
                <label>
                  Label nama footer
                  <input
                    data-testid="design-cetakan-po-footer-name"
                    value={draft.po.footer_name_label}
                    disabled={globalOnlyLocked}
                    onChange={(e) => patch("po.footer_name_label", e.target.value)}
                  />
                </label>
              </div>
            </section>
          </div>
        ) : null}

        {tab === "invoice" ? (
          <div data-testid="design-cetakan-invoice">
            <section className="dc-section">
              <h3>Tanda Tangan</h3>
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
              removeLabel={branchMode ? "Ikuti Global" : "Hapus"}
              uploadLabel={branchMode ? "Ganti khusus cabang" : "Ganti"}
              statusCustom="Custom"
              statusDefault="Teks saja"
              statusLabel={branchMode ? (overrideOn.prepared_signature_image ? "Khusus Cabang" : "Mengikuti Global") : undefined}
              allowRemove={branchMode ? Boolean(overrideOn.prepared_signature_image) : undefined}
                fileInputRef={preparedInputRef}
                testIdPrefix="design-cetakan-prepared-sig"
                emptyLabel="Belum ada tanda tangan"
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
              removeLabel={branchMode ? "Ikuti Global" : "Hapus"}
              uploadLabel={branchMode ? "Ganti khusus cabang" : "Ganti"}
              statusCustom="Custom"
              statusDefault="Teks saja"
              statusLabel={branchMode ? (overrideOn.approved_signature_image ? "Khusus Cabang" : "Mengikuti Global") : undefined}
              allowRemove={branchMode ? Boolean(overrideOn.approved_signature_image) : undefined}
                fileInputRef={approvedInputRef}
                testIdPrefix="design-cetakan-approved-sig"
                emptyLabel="Belum ada tanda tangan"
              />
            </section>
            <section className="dc-section">
              <h3 className="dc-label-row">
                Rekening Bank
                <OverrideBar
                  field="bank_accounts"
                  branchMode={branchMode}
                  enabled={overrideOn.bank_accounts}
                  canEdit={canEdit}
                  busy={busy}
                  onEnable={() => enableOverride("bank_accounts")}
                  onFollow={() => followGlobal("bank_accounts")}
                />
              </h3>
            {draft.invoice.bank_accounts.map((row, idx) => (
              <div className="dc-bank" key={`bank-${idx}`}>
                <label>
                  Bank {idx + 1}
                  <input
                    data-testid={`design-cetakan-bank-label-${idx}`}
                    value={row.bank_label}
                    disabled={fieldLocked("bank_accounts")}
                    onChange={(e) => patch(`invoice.bank_accounts.${idx}.bank_label`, e.target.value)}
                  />
                </label>
                <label>
                  Nomor rekening {idx + 1}
                  <input
                    data-testid={`design-cetakan-bank-number-${idx}`}
                    value={row.account_number}
                    disabled={fieldLocked("bank_accounts")}
                    onChange={(e) => patch(`invoice.bank_accounts.${idx}.account_number`, e.target.value)}
                  />
                </label>
                <label>
                  A.N rekening {idx + 1}
                  <input
                    data-testid={`design-cetakan-bank-name-${idx}`}
                    value={row.account_name}
                    disabled={fieldLocked("bank_accounts")}
                    onChange={(e) => patch(`invoice.bank_accounts.${idx}.account_name`, e.target.value)}
                  />
                </label>
              </div>
            ))}
            </section>
            <section className="dc-section">
              <h3 className="dc-label-row">
                Teks Faktur / Proforma
                {branchMode ? <GlobalOnlyMark testid="design-cetakan-global-only-estimasi">Estimasi Mengikuti Global</GlobalOnlyMark> : null}
              </h3>
              <div className="dc-fields">
            <label>
              Judul estimasi
              <input
                data-testid="design-cetakan-estimasi-title"
                value={draft.invoice.estimasi_title}
                disabled={globalOnlyLocked}
                onChange={(e) => patch("invoice.estimasi_title", e.target.value)}
              />
            </label>
            {draft.invoice.estimasi_lines.map((line, idx) => (
              <label key={`est-${idx}`}>
                Estimasi baris {idx + 1}
                <input
                  data-testid={`design-cetakan-estimasi-line-${idx}`}
                  value={line}
                  disabled={globalOnlyLocked}
                  onChange={(e) => patch(`invoice.estimasi_lines.${idx}`, e.target.value)}
                />
              </label>
            ))}
            <div className="dc-fields dc-fields-2">
            <label>
              <span className="dc-label-row">
                Label Disiapkan
                <OverrideBar field="prepared_label" branchMode={branchMode} enabled={overrideOn.prepared_label} canEdit={canEdit} busy={busy} onEnable={() => enableOverride("prepared_label")} onFollow={() => followGlobal("prepared_label")} />
              </span>
              <input
                data-testid="design-cetakan-prepared-label"
                value={draft.invoice.prepared_label}
                disabled={fieldLocked("prepared_label")}
                onChange={(e) => patch("invoice.prepared_label", e.target.value)}
              />
            </label>
            <label>
              <span className="dc-label-row">
                Footer Disiapkan
                <OverrideBar field="prepared_footer" branchMode={branchMode} enabled={overrideOn.prepared_footer} canEdit={canEdit} busy={busy} onEnable={() => enableOverride("prepared_footer")} onFollow={() => followGlobal("prepared_footer")} />
              </span>
              <input
                data-testid="design-cetakan-prepared-footer"
                value={draft.invoice.prepared_footer}
                disabled={fieldLocked("prepared_footer")}
                onChange={(e) => patch("invoice.prepared_footer", e.target.value)}
              />
            </label>
            <label>
              <span className="dc-label-row">
                Label Disetujui
                <OverrideBar field="approved_label" branchMode={branchMode} enabled={overrideOn.approved_label} canEdit={canEdit} busy={busy} onEnable={() => enableOverride("approved_label")} onFollow={() => followGlobal("approved_label")} />
              </span>
              <input
                data-testid="design-cetakan-approved-label"
                value={draft.invoice.approved_label}
                disabled={fieldLocked("approved_label")}
                onChange={(e) => patch("invoice.approved_label", e.target.value)}
              />
            </label>
            <label>
              <span className="dc-label-row">
                Footer Disetujui
                <OverrideBar field="approved_footer" branchMode={branchMode} enabled={overrideOn.approved_footer} canEdit={canEdit} busy={busy} onEnable={() => enableOverride("approved_footer")} onFollow={() => followGlobal("approved_footer")} />
              </span>
              <input
                data-testid="design-cetakan-approved-footer"
                value={draft.invoice.approved_footer}
                disabled={fieldLocked("approved_footer")}
                onChange={(e) => patch("invoice.approved_footer", e.target.value)}
              />
            </label>
            </div>
              </div>
            </section>
          </div>
        ) : null}

        {tab === "sj" ? (
          <div data-testid="design-cetakan-sj">
            <section className="dc-section">
              <h3 className="dc-label-row">
                Label Tanda Tangan
                {branchMode ? <GlobalOnlyMark testid="design-cetakan-global-only-sj" /> : null}
              </h3>
              <div className="dc-fields dc-fields-3">
                {draft.surat_jalan.signature_labels.map((label, idx) => (
                  <label key={`sj-sig-${idx}`}>
                    Label tanda tangan {idx + 1}
                    <input
                      data-testid={`design-cetakan-sj-sig-${idx}`}
                      value={label}
                      disabled={globalOnlyLocked}
                      onChange={(e) => patch(`surat_jalan.signature_labels.${idx}`, e.target.value)}
                    />
                  </label>
                ))}
                <label>
                  Label tanggal
                  <input
                    data-testid="design-cetakan-sj-date-label"
                    value={draft.surat_jalan.date_label}
                    disabled={globalOnlyLocked}
                    onChange={(e) => patch("surat_jalan.date_label", e.target.value)}
                  />
                </label>
              </div>
            </section>
          </div>
        ) : null}
        </div>

        <div className="dc-savebar" data-testid="design-cetakan-savebar">
          <div className="dc-savebar-meta">
            <span className={`dc-badge ${dirty ? "is-dirty" : "is-saved"}`}>
              {dirty ? <AlertCircle size={14} aria-hidden="true" /> : <CheckCircle2 size={14} aria-hidden="true" />}
              {saveState}
            </span>
            {!canEdit ? (
              <span className="muted small">Anda hanya dapat melihat pengaturan ini.</span>
            ) : null}
            {meta.updated_at ? (
              <span className="muted small" data-testid="design-cetakan-updated">
                Terakhir diubah{updatedByLabel ? ` oleh ${updatedByLabel}` : ""}
              </span>
            ) : null}
          </div>
          <div className="dc-savebar-actions">
            {branchMode ? (
              <button
                type="button"
                className="secondary dc-reset"
                data-testid="design-cetakan-reset-branch"
                disabled={!canEdit || busy}
                onClick={resetBranch}
              >
                Gunakan Semua Pengaturan Global
              </button>
            ) : null}
            <button
              type="submit"
              className="primary"
              data-testid="design-cetakan-save"
              disabled={!canEdit || busy}
            >
              <Save size={16} aria-hidden="true" />
              {busy ? "Menyimpan…" : "Simpan Perubahan"}
            </button>
          </div>
        </div>
      </form>

      {historyOpen ? (
        <div
          className="modal modal-viewport-safe-wrap"
          data-testid="design-cetakan-history-modal"
          onClick={(e) => e.target === e.currentTarget && setHistoryOpen(false)}
        >
          <div className="modal-inner modal-viewport-safe dc-modal" onClick={(e) => e.stopPropagation()}>
            <header className="modal-head">
              <h3>Riwayat Perubahan</h3>
              <button type="button" className="icon-button" data-testid="design-cetakan-history-close" onClick={() => setHistoryOpen(false)}>
                <X size={16} />
              </button>
            </header>
            <div className="dc-history-filters">
              <label>
                Filter
                <select
                  data-testid="design-cetakan-history-scope"
                  value={historyScope}
                  disabled={historyBusy}
                  onChange={(e) => {
                    setHistoryScope(e.target.value);
                    if (e.target.value !== "branch") setHistoryBranchId("");
                  }}
                >
                  <option value="all">Semua</option>
                  <option value="global">Default Semua Cabang</option>
                  <option value="branch">Cabang</option>
                </select>
              </label>
              {historyScope === "branch" ? (
                <label>
                  Cabang
                  <select
                    data-testid="design-cetakan-history-branch"
                    value={historyBranchId}
                    disabled={historyBusy}
                    onChange={(e) => setHistoryBranchId(e.target.value)}
                  >
                    <option value="">Semua cabang</option>
                    {catalog.map((row) => (
                      <option key={row.branch_id} value={String(row.branch_id)}>
                        {row.name || `Cabang ${row.branch_id}`}
                      </option>
                    ))}
                  </select>
                </label>
              ) : null}
            </div>
            {historyErr ? (
              <div className="signal-strip" role="alert" data-testid="design-cetakan-history-error" style={{ marginBottom: 12 }}>
                <AlertCircle size={16} />
                <span>{historyErr}</span>
              </div>
            ) : null}
            {historyBusy && historyItems.length === 0 ? (
              <p className="muted small" data-testid="design-cetakan-history-loading">Memuat…</p>
            ) : null}
            {!historyBusy && !historyErr && historyItems.length === 0 ? (
              <p className="muted small" data-testid="design-cetakan-history-empty">Belum ada riwayat perubahan Design Cetakan.</p>
            ) : null}
            <div data-testid="design-cetakan-history-list">
              {historyItems.map((row) => (
                <div
                  key={row.id || `${row.at}-${row.action}`}
                  className="dc-history-row"
                  data-testid="design-cetakan-history-row"
                >
                  <p data-testid="design-cetakan-history-operation">{row.operation_label || row.summary}</p>
                  <p className="muted small dc-history-meta">
                    <span data-testid="design-cetakan-history-at">{formatHistoryAt(row.at)}</span>
                    <span data-testid="design-cetakan-history-actor">{row.actor_name || "-"}</span>
                    <span data-testid="design-cetakan-history-branch">{row.branch_name || "Global"}</span>
                  </p>
                  {Array.isArray(row.changed_fields) && row.changed_fields.length ? (
                    <p className="dc-chips" data-testid="design-cetakan-history-changes">
                      {row.changed_fields.map((field) => field.label || field.key).filter(Boolean).join(", ")}
                    </p>
                  ) : null}
                </div>
              ))}
            </div>
            {historyHasMore ? (
              <button
                type="button"
                className="secondary"
                data-testid="design-cetakan-history-more"
                disabled={historyBusy}
                onClick={loadMoreHistory}
              >
                {historyBusy ? "Memuat…" : "Muat lebih banyak"}
              </button>
            ) : null}
          </div>
        </div>
      ) : null}

      {copyOpen ? (
        <div
          className="modal modal-viewport-safe-wrap"
          data-testid="design-cetakan-copy-modal"
          onClick={(e) => e.target === e.currentTarget && !copyBusy && setCopyOpen(false)}
        >
          <div className="modal-inner modal-viewport-safe dc-modal" onClick={(e) => e.stopPropagation()}>
            <header className="modal-head">
              <h3>Salin Pengaturan Khusus</h3>
              <button type="button" className="icon-button" data-testid="design-cetakan-copy-close" onClick={() => !copyBusy && setCopyOpen(false)}>
                <X size={16} />
              </button>
            </header>
            <p className="dc-callout">
              Salin menggunakan pengaturan yang sudah disimpan. Nilai yang diwarisi dari Global tidak disalin.
            </p>
            <div className="dc-history-filters">
              <label>
                Sumber
                <select
                  data-testid="design-cetakan-copy-source"
                  value={copySource}
                  disabled={copyBusy}
                  onChange={(e) => setCopySource(e.target.value)}
                >
                  <option value="global">Default Semua Cabang</option>
                  {catalog.map((row) => (
                    <option key={`src-${row.branch_id}`} value={String(row.branch_id)}>
                      {row.name || `Cabang ${row.branch_id}`}
                    </option>
                  ))}
                </select>
              </label>
              <label>
                Tujuan
                <select
                  data-testid="design-cetakan-copy-target"
                  value={copyTarget}
                  disabled={copyBusy}
                  onChange={(e) => setCopyTarget(e.target.value)}
                >
                  <option value="">Pilih cabang</option>
                  {catalog.map((row) => (
                    <option key={`dst-${row.branch_id}`} value={String(row.branch_id)}>
                      {row.name || `Cabang ${row.branch_id}`}
                    </option>
                  ))}
                </select>
              </label>
            </div>
            {copySource === "global" ? (
              <div className="settings-info" data-testid="design-cetakan-copy-global-warning" style={{ marginBottom: 12 }}>
                <Info size={16} />
                <div>
                  <p>Nilai yang disalin menjadi pengaturan khusus cabang dan tidak lagi mengikuti perubahan Global.</p>
                  <p className="muted small" style={{ marginTop: 4 }}>
                    Mengikuti Global ≠ menyalin Global. Gunakan Semua Pengaturan Global menghapus override cabang agar kembali mewarisi Global.
                  </p>
                </div>
              </div>
            ) : (
              <p className="muted small" data-testid="design-cetakan-copy-branch-note" style={{ marginBottom: 12 }}>
                Hanya pengaturan khusus {catalog.find((row) => String(row.branch_id) === String(copySource))?.name || `Cabang ${copySource}`} yang akan disalin.
              </p>
            )}
            {copyEmptyBranch ? (
              <p className="muted small" data-testid="design-cetakan-copy-empty">Cabang ini belum memiliki pengaturan khusus.</p>
            ) : null}
            {copySame ? (
              <p className="muted small" data-testid="design-cetakan-copy-same">Sumber dan tujuan tidak boleh sama.</p>
            ) : null}
            {copySourceKeys.length ? (
              <div className="dc-summary" data-testid="design-cetakan-copy-summary">
                <p className="small"><strong>Akan disalin</strong></p>
                <p className="dc-chips" data-testid="design-cetakan-copy-fields">{copySourceKeys.map(copyFieldLabel).join(", ")}</p>
                {copyReplaceKeys.length ? (
                  <p className="dc-chips" data-testid="design-cetakan-copy-replaced">
                    Akan diganti di tujuan: {copyReplaceKeys.map(copyFieldLabel).join(", ")}
                  </p>
                ) : null}
                {copyDropKeys.length ? (
                  <p className="dc-chips" data-testid="design-cetakan-copy-dropped">
                    Akan berhenti di-override / kembali ke Global: {copyDropKeys.map(copyFieldLabel).join(", ")}
                  </p>
                ) : null}
              </div>
            ) : null}
            {copyErr ? (
              <div className="signal-strip" role="alert" data-testid="design-cetakan-copy-error" style={{ marginBottom: 12 }}>
                <AlertCircle size={16} />
                <span>{copyErr}</span>
              </div>
            ) : null}
            <div className="dc-modal-actions">
              <button type="button" className="secondary" disabled={copyBusy} onClick={() => setCopyOpen(false)}>Batal</button>
              <button
                type="button"
                className="primary"
                data-testid="design-cetakan-copy-confirm"
                disabled={copyBlocked}
                onClick={submitCopy}
              >
                {copyBusy ? "Menyalin…" : "Salin ke Cabang"}
              </button>
            </div>
          </div>
        </div>
      ) : null}
    </div>
  );
}
