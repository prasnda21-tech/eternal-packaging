import { useCallback, useEffect, useMemo, useState } from "react";
import { useLocation, useNavigate, useSearchParams } from "react-router-dom";
import api from "../lib/api";
import { useAuth } from "../lib/auth";
import { formatDate } from "../lib/constants";
import {
  SALES_SETTINGS_AUDIT_ACTIONS,
  SALES_SETTINGS_SECTIONS,
  SALES_SETTINGS_TAB,
  filterSalesSettingsAuditLogs,
  resolveSalesSettingsSection,
  sectionAllowed,
} from "../lib/salesSettingsHub";
import SalesMasterSettings from "../components/SalesMasterSettings";
import "./SalesSettingsHub.css";

function historyDetail(row) {
  if (row?.note) return row.note;
  const before = row?.before || {};
  const after = row?.after || {};
  const keys = Object.keys(after).filter((k) => JSON.stringify(before[k]) !== JSON.stringify(after[k]));
  if (keys.length) return keys.map((k) => `${k}: ${JSON.stringify(after[k])}`).join(" · ");
  return "—";
}

function RiwayatPanel({ canViewAudit }) {
  const [logs, setLogs] = useState([]);
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState("");

  const load = useCallback(async () => {
    if (!canViewAudit) return;
    setLoading(true);
    setErr("");
    try {
      const data = await api.adminAuditLogs({ limit: 300 });
      setLogs(filterSalesSettingsAuditLogs(data.logs || []));
    } catch (e) {
      setErr(e?.response?.data?.detail || e?.message || "Gagal memuat audit");
      setLogs([]);
    } finally {
      setLoading(false);
    }
  }, [canViewAudit]);

  useEffect(() => { load(); }, [load]);

  if (!canViewAudit) {
    return (
      <div className="ps-history-denied" data-testid="ps-riwayat-denied">
        Riwayat Pengaturan Sales membutuhkan permission <code>audit.view</code>.
        Event yang dicatat sistem (bila ada): {SALES_SETTINGS_AUDIT_ACTIONS.join(", ")}.
      </div>
    );
  }

  if (loading) {
    return <p className="ps-history-loading" data-testid="ps-riwayat-loading">Memuat riwayat…</p>;
  }
  if (err) {
    return <p className="ps-denied" data-testid="ps-riwayat-error" role="alert">{err}</p>;
  }
  if (!logs.length) {
    return (
      <div className="ps-history-empty" data-testid="ps-riwayat-empty">
        Belum ada audit log Pengaturan Sales pada sampel terbaru.
        Hanya menampilkan: {SALES_SETTINGS_AUDIT_ACTIONS.join(", ")}.
      </div>
    );
  }

  return (
    <div className="ps-history-list" data-testid="ps-riwayat-list">
      {logs.map((row, idx) => (
        <article
          key={row.id || `${row.action}-${row.at}-${idx}`}
          className="ps-history-row"
          data-testid="ps-riwayat-row"
        >
          <strong data-testid="ps-riwayat-action">{row.action}</strong>
          <div className="ps-history-meta">
            <span>{formatDate(row.at)}</span>
            <span>{row.actor_name || row.actor_email || "—"}</span>
          </div>
          <p className="muted small" style={{ margin: 0 }}>{historyDetail(row)}</p>
        </article>
      ))}
    </div>
  );
}

function VerifikasiPanel({ canEdit, notify, onOpenMaster }) {
  const [items, setItems] = useState([]);
  const [masters, setMasters] = useState([]);
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState("");
  const [busyId, setBusyId] = useState("");
  const [linkChoice, setLinkChoice] = useState({});

  const load = useCallback(async () => {
    setLoading(true);
    setErr("");
    try {
      const [candRes, masterRes] = await Promise.all([
        api.salesCandidatesList({ open_only: true }),
        api.salesMasterList().catch(() => ({ items: [] })),
      ]);
      setItems(candRes.items || []);
      setMasters((masterRes.items || []).filter((m) => m.active !== false));
    } catch (e) {
      setErr(e?.response?.data?.detail || e?.message || "Gagal memuat kandidat");
      setItems([]);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { load(); }, [load]);

  const approveNew = async (row) => {
    if (!canEdit) return;
    setBusyId(row.id);
    try {
      const res = await api.salesCandidateApproveNew(row.id, {
        sales_name: row.exact_label,
        active: true,
      });
      notify?.(res.message || "Sales baru didaftarkan");
      if (res.master?.id) onOpenMaster?.(res.master.id);
      await load();
    } catch (e) {
      notify?.(e?.response?.data?.detail || e?.message || "Gagal approve", "error");
    } finally {
      setBusyId("");
    }
  };

  const linkExisting = async (row) => {
    if (!canEdit) return;
    const mid = String(linkChoice[row.id] || "").trim();
    if (!mid) {
      notify?.("Pilih Master Sales tujuan", "error");
      return;
    }
    setBusyId(row.id);
    try {
      const res = await api.salesCandidateLink(row.id, { sales_master_id: mid });
      notify?.(res.message || "Label dihubungkan");
      await load();
    } catch (e) {
      notify?.(e?.response?.data?.detail || e?.message || "Gagal menghubungkan", "error");
    } finally {
      setBusyId("");
    }
  };

  if (loading) {
    return <p className="ps-history-loading" data-testid="ps-verifikasi-loading">Memuat kandidat…</p>;
  }
  if (err) {
    return <p className="ps-denied" data-testid="ps-verifikasi-error" role="alert">{err}</p>;
  }

  return (
    <div data-testid="ps-verifikasi-panel">
      <p className="ps-summary-note" data-testid="ps-verifikasi-note">
        Label Accurate yang belum terhubung ke Master Sales. Verifikasi sekali — tidak mengubah
        <code> sales_user_mapping </code>, snapshot invoice/PO/SJ, atau atribusi laporan.
        Approve/link alias saja tidak membuka PWA; tautkan akun Eternal di editor Master agar
        impor baru tanpa mapping bisa memakai fallback ownership.
      </p>
      {!canEdit ? (
        <p className="muted small" data-testid="ps-verifikasi-readonly">
          Mode lihat saja — butuh <code>settings.sales_master.manage</code> untuk approve/link.
        </p>
      ) : null}
      <div className="ps-summary-table-wrap">
        <table className="ship-table" data-testid="ps-verifikasi-table">
          <thead>
            <tr>
              <th>Label Accurate</th>
              <th>Status</th>
              <th>Terlihat</th>
              <th>Aksi</th>
            </tr>
          </thead>
          <tbody>
            {items.length === 0 ? (
              <tr>
                <td colSpan={4}>
                  <span className="muted small" data-testid="ps-verifikasi-empty">
                    Tidak ada label yang perlu diverifikasi.
                  </span>
                </td>
              </tr>
            ) : items.map((row) => (
              <tr key={row.id} data-testid={`ps-verifikasi-row-${row.id}`}>
                <td>
                  <strong data-testid="ps-cand-label">{row.exact_label}</strong>
                  {row.review_reason ? (
                    <div className="muted small">Alasan tinjau: {row.review_reason}</div>
                  ) : null}
                </td>
                <td>
                  <span className="pill pill-warn">{row.status}</span>
                  <div className="muted small">×{row.seen_count || 1}</div>
                </td>
                <td className="muted small">{row.last_seen_at ? formatDate(row.last_seen_at) : "—"}</td>
                <td>
                  <div className="ps-verifikasi-actions">
                    <button
                      type="button"
                      className="primary sm"
                      disabled={!canEdit || busyId === row.id}
                      data-testid={`ps-cand-approve-${row.id}`}
                      onClick={() => approveNew(row)}
                    >
                      Daftarkan sebagai Sales baru
                    </button>
                    <div className="ps-verifikasi-link">
                      <select
                        value={linkChoice[row.id] || ""}
                        disabled={!canEdit || busyId === row.id}
                        data-testid={`ps-cand-link-select-${row.id}`}
                        onChange={(e) => setLinkChoice((prev) => ({ ...prev, [row.id]: e.target.value }))}
                      >
                        <option value="">— pilih Master existing —</option>
                        {masters.map((m) => (
                          <option key={m.id} value={m.id}>{m.sales_name}</option>
                        ))}
                      </select>
                      <button
                        type="button"
                        className="ghost sm"
                        disabled={!canEdit || busyId === row.id}
                        data-testid={`ps-cand-link-${row.id}`}
                        onClick={() => linkExisting(row)}
                      >
                        Hubungkan
                      </button>
                    </div>
                  </div>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

export default function SalesSettingsHub({
  notify,
  canViewMaster,
  canEditMaster,
  canViewMapping,
  canEditMapping,
}) {
  const { hasPerm } = useAuth() || {};
  const canViewAudit = Boolean(hasPerm?.("audit.view"));
  const navigate = useNavigate();
  const location = useLocation();
  const [searchParams] = useSearchParams();
  const access = useMemo(
    () => ({ canViewMaster: Boolean(canViewMaster), canViewMapping: Boolean(canViewMapping) }),
    [canViewMaster, canViewMapping],
  );

  const section = useMemo(
    () => resolveSalesSettingsSection(searchParams.get("section"), access),
    [searchParams, access],
  );
  const focusId = String(searchParams.get("edit") || "").trim();

  const writeUrl = useCallback((nextSection, editId, { replace = false } = {}) => {
    const params = new URLSearchParams(location.search);
    params.set("section", nextSection);
    if (editId) params.set("edit", editId);
    else params.delete("edit");
    const qs = params.toString();
    navigate(`/settings/${SALES_SETTINGS_TAB}${qs ? `?${qs}` : ""}`, { replace });
  }, [location.search, navigate]);

  useEffect(() => {
    const raw = String(searchParams.get("section") || "").trim().toLowerCase();
    // Accept legacy section ids without forcing a loop when already normalized.
    const normalized = section;
    if (raw === normalized) return;
    if (!raw && normalized) {
      writeUrl(normalized, focusId || "", { replace: true });
      return;
    }
    if (raw && raw !== normalized) {
      writeUrl(normalized, focusId || "", { replace: true });
    }
  }, [section, focusId, searchParams, writeUrl]);

  const selectSection = (id) => {
    if (!sectionAllowed(id, access)) return;
    writeUrl(id, "");
  };

  const openMasterEditor = (salesId) => {
    writeUrl("semua", salesId || "");
  };

  const clearFocus = useCallback(() => {
    if (!focusId) return;
    writeUrl("semua", "", { replace: true });
  }, [focusId, writeUrl]);

  const visibleSections = SALES_SETTINGS_SECTIONS.filter((item) => sectionAllowed(item.id, access));

  if (!canViewMaster && !canViewMapping) {
    return (
      <p className="ps-denied" data-testid="ps-hub-denied">
        Anda tidak memiliki akses Pengaturan Sales.
      </p>
    );
  }

  return (
    <div className="ps-hub" data-testid="pengaturan-sales-hub">
      <header className="ps-hub-head">
        <h2>Pengaturan Sales</h2>
        <p className="ps-hub-subhead">
          Satu tempat untuk Master Sales, alias Accurate, dan verifikasi label impor.
        </p>
      </header>

      <p className="ps-hub-contract" data-testid="ps-hub-contract">
        Master Sales = identitas operasional (<code>sales_master.id</code> / <code>sales_name</code>).
        Alias Accurate = label exact → Master. Akun Eternal = login PWA opsional.
        Verifikasi alias tidak otomatis memberi akses PWA; ownership PO baru memakai mapping
        (prioritas) atau fallback Master.user_id yang valid. Mapping tidak diubah saat verifikasi.
      </p>

      <nav className="ps-section-nav" aria-label="Bagian Pengaturan Sales" data-testid="ps-section-nav">
        {visibleSections.map((item) => (
          <button
            key={item.id}
            type="button"
            className={section === item.id ? "is-active" : ""}
            data-testid={item.testid}
            aria-current={section === item.id ? "page" : undefined}
            onClick={() => selectSection(item.id)}
          >
            {item.label}
          </button>
        ))}
      </nav>

      <div className="ps-hub-panel" data-testid={`ps-panel-${section}`}>
        {section === "semua" && canViewMaster ? (
          <SalesMasterSettings
            notify={notify}
            canEdit={canEditMaster}
            canViewMapping={canViewMapping}
            canEditMapping={canEditMapping}
            focusSalesId={focusId}
            onFocusConsumed={clearFocus}
            compact
          />
        ) : null}
        {section === "semua" && !canViewMaster ? (
          <p className="ps-denied" data-testid="ps-master-denied">Tidak ada akses Master Sales.</p>
        ) : null}

        {section === "verifikasi" && canViewMaster ? (
          <VerifikasiPanel
            canEdit={canEditMaster}
            notify={notify}
            onOpenMaster={openMasterEditor}
          />
        ) : null}
        {section === "verifikasi" && !canViewMaster ? (
          <p className="ps-denied" data-testid="ps-verifikasi-denied">Tidak ada akses Master Sales.</p>
        ) : null}

        {section === "riwayat" ? (
          <RiwayatPanel canViewAudit={canViewAudit} />
        ) : null}
      </div>
    </div>
  );
}
