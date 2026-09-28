import { useEffect, useRef, useState } from "react";
import { fmtQty } from "./qtyUi";
import api from "../lib/api";
import { formatRupiah } from "../lib/purchaseUi";

const ALERT_CODES = new Set([
  "DIFFERENCE",
  "EXPLAINED_DIFFERENCE",
  "INCOMPLETE",
  "BLOCKED",
  "MISSING_PHYSICAL",
  "UOM_MISMATCH",
  "UNIT_CONFLICT",
  "IN_PROGRESS",
  "RETRY_REQUIRED",
]);

const STATUS_TEXT = {
  RECONCILED: "Cocok",
  EXPLAINED_DIFFERENCE: "Selisih terjelaskan",
  DIFFERENCE: "Selisih",
  INCOMPLETE: "Belum lengkap",
  BLOCKED: "Terblokir",
  MATCHED: "Cocok",
  MISSING_PHYSICAL: "Qty fisik belum tersedia",
  UOM_MISMATCH: "Satuan tidak sama",
  UNIT_CONFLICT: "Satuan bentrok",
  IN_PROGRESS: "Masih berjalan",
  NO_ACTIVITY: "Belum ada aktivitas",
  HAS_ACTIVITY: "Ada aktivitas",
  STABLE: "Stabil",
  RETRY_REQUIRED: "Perlu diulang",
};

const PENDING_LABELS = {
  active_gr_candidates_without_journal: "Penerimaan aktif tanpa jurnal",
  pending_cogs: "HPP menunggu",
  waiting_earlier_issue: "Menunggu pengeluaran lebih awal",
  blocked_cogs: "HPP terblokir",
  pre_effective_legacy: "Sebelum tanggal efektif",
  excluded_service: "Jasa dikecualikan",
  accounting_recovery_required: "Perlu pemulihan akuntansi",
};

export function ledgerMoneyText(value) {
  if (value == null || value === "") return "Belum tersedia";
  const number = Number(value);
  if (!Number.isFinite(number)) return "Belum tersedia";
  return formatRupiah(number);
}

export function ledgerQtyText(value, uom) {
  if (value == null || value === "") return "Belum tersedia";
  const number = Number(value);
  if (!Number.isFinite(number)) return "Belum tersedia";
  const qty = fmtQty(number);
  return uom ? `${qty} ${uom}` : qty;
}

export function ledgerCountText(value) {
  if (value == null || value === "") return "Belum tersedia";
  const number = Number(value);
  if (!Number.isFinite(number)) return "Belum tersedia";
  return String(number);
}

export function ledgerAccountParams(accountId) {
  const id = String(accountId || "").trim();
  return id ? { account_id: id } : {};
}

export function ledgerItemParams(q, item) {
  const params = {};
  const query = String(q || "").trim();
  const itemId = String(item || "").trim();
  if (query) params.q = query;
  if (itemId) params.item = itemId;
  return params;
}

export function globalItemCountText(payload) {
  if (!payload || typeof payload !== "object") return "Belum tersedia";
  if (Object.prototype.hasOwnProperty.call(payload, "global_item_count")) {
    return ledgerCountText(payload.global_item_count);
  }
  if (Object.prototype.hasOwnProperty.call(payload, "item_count")) {
    return ledgerCountText(payload.item_count);
  }
  return "Belum tersedia";
}

export function globalAccountCountText(payload) {
  if (!payload || typeof payload !== "object") return "Belum tersedia";
  const box = Object.prototype.hasOwnProperty.call(payload, "global_account_count")
    ? payload.global_account_count
    : payload.account_count;
  if (!box || typeof box !== "object") return "Belum tersedia";
  return `persediaan ${ledgerCountText(box.inventory)} · HPP ${ledgerCountText(box.cogs)}`;
}

export function ledgerActivityIsEmpty(payload) {
  if (!payload) return false;
  if (payload.activity_state === "NO_ACTIVITY") return true;
  if (payload.has_activity === false) return true;
  return false;
}

export function ledgerStatusClass(code) {
  const status = String(code || "");
  if (status === "RECONCILED" || status === "MATCHED" || status === "STABLE") return "inventory-ledger-status is-ok";
  if (ALERT_CODES.has(status)) return "inventory-ledger-status is-alert";
  return "inventory-ledger-status";
}

function statusText(code) {
  if (code == null || code === "") return "Belum tersedia";
  const label = STATUS_TEXT[code];
  return label ? `${label} (${code})` : String(code);
}

function detailMessage(exception) {
  const detail = exception?.response?.data?.detail;
  if (typeof detail === "object" && detail) return detail.message || detail.code || "Gagal dimuat";
  return detail || "Gagal dimuat";
}

function Money({ value }) {
  return ledgerMoneyText(value);
}

function Triplet({ title, row, testId }) {
  if (!row) return null;
  return (
    <section data-testid={testId}>
      <h3>{title}</h3>
      <p className={ledgerStatusClass(row.value_status)} data-testid={`${testId}-status`}>
        {statusText(row.value_status)}
      </p>
      <div className="inventory-value-summary inventory-book-recon">
        <div className="inventory-value-summary-card">
          <span>Basis finansial</span>
          <strong>{Money({ value: row.financial_inventory_basis_value ?? row.financial_cogs_basis_value ?? row.financial_basis_value })}</strong>
        </div>
        <div className="inventory-value-summary-card">
          <span>Jurnal sumber</span>
          <strong>{Money({ value: row.inventory_source_journal_balance ?? row.cogs_source_journal_balance ?? row.source_journal_balance })}</strong>
        </div>
        <div className="inventory-value-summary-card">
          <span>GL</span>
          <strong>{Money({ value: row.inventory_gl_balance ?? row.cogs_gl_balance ?? row.gl_balance })}</strong>
        </div>
        <div className="inventory-value-summary-card">
          <span>Selisih basis dan jurnal sumber</span>
          <strong>{Money({ value: row.basis_vs_source_difference })}</strong>
        </div>
        <div className="inventory-value-summary-card">
          <span>Jurnal lain</span>
          <strong>{Money({ value: row.other_journal_balance })}</strong>
        </div>
        <div className="inventory-value-summary-card">
          <span>Selisih GL dan basis</span>
          <strong>{Money({ value: row.gl_vs_basis_difference })}</strong>
        </div>
      </div>
      <p className="muted small">Basis, jurnal sumber, dan GL ditampilkan terpisah.</p>
      <p className="muted small">
        Identitas selisih: {row.identity_holds == null ? "Belum tersedia" : (row.identity_holds ? "ya" : "tidak")}.
      </p>
    </section>
  );
}

function accountBasis(row) {
  if (!row) return null;
  if (row.classification === "cogs") return row.financial_cogs_basis_value ?? row.financial_basis_value;
  return row.financial_inventory_basis_value ?? row.financial_basis_value;
}

function accountSource(row) {
  if (!row) return null;
  if (row.classification === "cogs") return row.cogs_source_journal_balance ?? row.source_journal_balance;
  return row.inventory_source_journal_balance ?? row.source_journal_balance;
}

function accountGl(row) {
  if (!row) return null;
  if (row.classification === "cogs") return row.cogs_gl_balance ?? row.gl_balance;
  return row.inventory_gl_balance ?? row.gl_balance;
}

export default function InventoryLedgerReconciliation({ enabled, initialItemId = "" }) {
  const [view, setView] = useState("summary");
  const [accountDraft, setAccountDraft] = useState("");
  const [accountId, setAccountId] = useState("");
  const [qDraft, setQDraft] = useState("");
  const [query, setQuery] = useState("");
  const [itemDraft, setItemDraft] = useState(initialItemId || "");
  const [itemId, setItemId] = useState(initialItemId || "");
  const [reloadToken, setReloadToken] = useState(0);
  const [payload, setPayload] = useState(null);
  const [phase, setPhase] = useState(enabled ? "loading" : "denied");
  const [error, setError] = useState("");
  const requestRef = useRef(0);
  const itemSeed = useRef(initialItemId || "");

  useEffect(() => {
    const next = initialItemId || "";
    if (next === itemSeed.current) return;
    itemSeed.current = next;
    setItemDraft(next);
    setItemId(next);
  }, [initialItemId]);

  useEffect(() => {
    if (!enabled) {
      requestRef.current += 1;
      setPayload(null);
      setPhase("denied");
      setError("");
      return undefined;
    }
    const seq = ++requestRef.current;
    setPhase("loading");
    setError("");
    let cancelled = false;
    const run = async () => {
      try {
        let data;
        if (view === "accounts") {
          data = await api.ledgerInventoryReconciliationAccounts(ledgerAccountParams(accountId));
        } else if (view === "items") {
          data = await api.ledgerInventoryReconciliationItems(ledgerItemParams(query, itemId));
        } else {
          data = await api.ledgerInventoryReconciliationSummary();
        }
        if (cancelled || seq !== requestRef.current) return;
        setPayload(data);
        setPhase("ready");
      } catch (exception) {
        if (cancelled || seq !== requestRef.current) return;
        setPayload(null);
        if (exception?.response?.status === 403) {
          setPhase("denied");
          setError("");
          return;
        }
        setPhase("error");
        setError(detailMessage(exception));
      }
    };
    run();
    return () => { cancelled = true; };
  }, [enabled, view, accountId, query, itemId, reloadToken]);

  const applyFilters = (event) => {
    event.preventDefault();
    if (view === "accounts") setAccountId(accountDraft.trim());
    if (view === "items") {
      setQuery(qDraft.trim());
      setItemId(itemDraft.trim());
    }
  };

  const selectAccount = (id) => {
    const next = String(id || "").trim();
    setView("accounts");
    setAccountDraft(next);
    setAccountId(next);
  };

  const selectItem = (id) => {
    const next = String(id || "").trim();
    setView("items");
    setItemDraft(next);
    setItemId(next);
  };

  const accounts = [
    ...(payload?.inventory_accounts || []),
    ...(payload?.cogs_accounts || []),
  ];
  const items = payload?.items || [];
  const empty = phase === "ready" && ledgerActivityIsEmpty(payload);

  return (
    <section className="inventory-value-panel" data-testid="inventory-panel-ledger">
      <div className="inventory-panel-scroll">
        <h2>Rekonsiliasi Persediaan & HPP</h2>
        <p className="inventory-ledger-note" data-testid="ledger-current-note">
          Laporan menunjukkan kondisi tercatat saat ini. Bukan saldo historis.
          {payload && payload.historical_as_of != null ? ` historical_as_of: ${String(payload.historical_as_of)}.` : ""}
        </p>
        <div className="inventory-value-tabs" role="tablist" aria-label="Tampilan rekonsiliasi persediaan">
          {[
            ["summary", "Ringkasan"],
            ["accounts", "Per akun"],
            ["items", "Per barang"],
          ].map(([id, label]) => (
            <button
              key={id}
              type="button"
              role="tab"
              aria-selected={view === id}
              className={view === id ? "is-active" : ""}
              data-testid={`ledger-view-${id}`}
              onClick={() => setView(id)}
            >
              {label}
            </button>
          ))}
          <button
            type="button"
            className="ghost"
            data-testid="ledger-reload"
            onClick={() => setReloadToken((value) => value + 1)}
            disabled={!enabled || phase === "loading"}
          >
            Muat ulang
          </button>
        </div>

        {view === "accounts" && (
          <form className="inventory-ledger-toolbar" onSubmit={applyFilters} data-testid="ledger-account-filter">
            <input
              value={accountDraft}
              onChange={(event) => setAccountDraft(event.target.value)}
              aria-label="ID akun"
              placeholder="ID akun"
              data-testid="ledger-account-id"
            />
            <button type="submit" className="ghost">Terapkan</button>
          </form>
        )}
        {view === "items" && (
          <form className="inventory-ledger-toolbar" onSubmit={applyFilters} data-testid="ledger-item-filter">
            <input
              value={itemDraft}
              onChange={(event) => setItemDraft(event.target.value)}
              aria-label="ID barang"
              placeholder="ID barang"
              data-testid="ledger-item-id"
            />
            <input
              value={qDraft}
              onChange={(event) => setQDraft(event.target.value)}
              aria-label="Cari barang"
              placeholder="Cari"
              data-testid="ledger-item-q"
            />
            <button type="submit" className="ghost">Terapkan</button>
          </form>
        )}

        {phase === "denied" && (
          <p role="alert" data-testid="ledger-access-denied">Akses ditolak.</p>
        )}
        {phase === "loading" && (
          <p data-testid="ledger-loading">Memuat rekonsiliasi persediaan dan HPP…</p>
        )}
        {phase === "error" && (
          <p role="alert" data-testid="ledger-error">{error}. Gagal dimuat.</p>
        )}

        {phase === "ready" && payload && (
          <>
            <p data-testid="ledger-reconciliation-status" className={empty ? "inventory-ledger-status" : ledgerStatusClass(payload.reconciliation_status)}>
              Status server: {payload.reconciliation_status || "Belum tersedia"}
              {!empty && payload.reconciliation_status ? ` · ${statusText(payload.reconciliation_status)}` : ""}
            </p>
            <p data-testid="ledger-global-item-count">Barang keseluruhan {globalItemCountText(payload)}</p>
            <p data-testid="ledger-global-account-count">Akun keseluruhan {globalAccountCountText(payload)}</p>
            {payload.total != null && (
              <p data-testid="ledger-filtered-total">Baris tampilan {ledgerCountText(payload.total)}</p>
            )}
            {empty && (
              <p data-testid="ledger-empty">
                Belum ada aktivitas persediaan dan HPP yang tercatat.
              </p>
            )}
            {!empty && view === "items" && items.length === 0 && (
              <p data-testid="ledger-filter-empty">Tidak ada baris untuk filter ini. Total keseluruhan tidak dihitung dari hasil filter.</p>
            )}
            {!empty && view === "accounts" && accounts.length === 0 && (
              <p data-testid="ledger-filter-empty">Tidak ada baris untuk filter ini. Total keseluruhan tidak dihitung dari hasil filter.</p>
            )}

            {(payload.posting_flags || []).length > 0 && (
              <div data-testid="ledger-posting-flags">
                {(payload.posting_flags || []).map((flag) => (
                  <p key={flag} className="inventory-ledger-status is-alert">{flag}</p>
                ))}
              </div>
            )}
            {payload.pending?.blocked_cogs && (
              <p data-testid="ledger-blocked-cogs" className={payload.pending.blocked_cogs.count ? "inventory-ledger-status is-alert" : ""}>
                HPP terblokir: {ledgerCountText(payload.pending.blocked_cogs.count)}
              </p>
            )}
            {(payload.integrity_anomalies || []).map((row, index) => (
              <p key={`${row.code || "anomaly"}-${row.item_key || row.account_id || index}`} data-testid="ledger-anomaly" className="inventory-ledger-status is-alert">
                {row.code || "—"}: {row.message || "Belum tersedia"}
                {row.account_id ? ` Akun ${row.account_id}.` : ""}
                {row.item_key ? ` Barang ${row.item_key}.` : ""}
              </p>
            ))}
            {(payload.unresolved_accounts || []).map((row) => (
              <p key={row.account_id || "unresolved"} data-testid="ledger-unresolved-account">
                Akun belum terselesaikan {row.account_id || "—"}. Saldo {ledgerMoneyText(row.signed_balance)}.
              </p>
            ))}

            {view === "summary" && (
              <div data-testid="ledger-summary">
                <p className="muted small">
                  Cakupan {payload.basis_coverage_complete == null ? "Belum tersedia" : (payload.basis_coverage_complete ? "lengkap" : "belum lengkap")}.
                  Stabilitas {statusText(payload.read_stability)}. Aktivitas {statusText(payload.activity_state)}.
                </p>
                <Triplet title="Persediaan" row={payload.inventory} testId="ledger-inventory" />
                <Triplet title="HPP" row={payload.cogs} testId="ledger-cogs" />
                <div className="inventory-value-summary inventory-book-recon" data-testid="ledger-pending">
                  {Object.entries(PENDING_LABELS).map(([key, label]) => (
                    <div className="inventory-value-summary-card" key={key}>
                      <span>{label}</span>
                      <strong>{ledgerCountText(payload.pending?.[key]?.count)}</strong>
                    </div>
                  ))}
                </div>
              </div>
            )}

            {view === "accounts" && (
              <div className="prod-table-wrap">
                <table className="prod-table inventory-ledger-table" data-testid="ledger-accounts-table">
                  <thead>
                    <tr>
                      <th>Akun</th>
                      <th>Kelompok</th>
                      <th>Basis</th>
                      <th>Jurnal sumber</th>
                      <th>GL</th>
                      <th>Selisih basis</th>
                      <th>Jurnal lain</th>
                      <th>Selisih GL</th>
                      <th>Status</th>
                    </tr>
                  </thead>
                  <tbody>
                    {accounts.map((row) => (
                      <tr key={`${row.classification || "akun"}-${row.account_id}`} data-testid={`ledger-account-${row.account_id}`}>
                        <td>
                          <button type="button" className="ghost sm" onClick={() => selectAccount(row.account_id)}>
                            {row.code || "—"} {row.name || "—"}
                          </button>
                          <div className="muted small">ID {row.account_id || "—"}{row.coa_resolved === false ? " · belum terselesaikan" : ""}</div>
                        </td>
                        <td>{row.classification || "—"}</td>
                        <td>{ledgerMoneyText(accountBasis(row))}</td>
                        <td>{ledgerMoneyText(accountSource(row))}</td>
                        <td>{ledgerMoneyText(accountGl(row))}</td>
                        <td>{ledgerMoneyText(row.basis_vs_source_difference)}</td>
                        <td>{ledgerMoneyText(row.other_journal_balance)}</td>
                        <td>{ledgerMoneyText(row.gl_vs_basis_difference)}</td>
                        <td className={ledgerStatusClass(row.value_status)} data-testid={`ledger-account-status-${row.account_id}`}>
                          {statusText(row.value_status)}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}

            {view === "items" && (
              <div className="prod-table-wrap">
                <table className="prod-table inventory-ledger-table" data-testid="ledger-items-table">
                  <thead>
                    <tr>
                      <th>Barang</th>
                      <th>Qty fisik</th>
                      <th>Qty finansial</th>
                      <th>Selisih qty</th>
                      <th>Status</th>
                    </tr>
                  </thead>
                  <tbody>
                    {items.map((row) => {
                      const key = row.financial_inventory_item_key || row.master_item_id || "item";
                      const reasons = (payload.integrity_anomalies || []).filter((anomaly) => (
                        (row.financial_inventory_item_key && anomaly.item_key === row.financial_inventory_item_key)
                        || (row.master_item_id && anomaly.source_id === row.master_item_id)
                      ));
                      return (
                        <tr key={key} data-testid={`ledger-item-${row.master_item_id || key}`}>
                          <td>
                            <button type="button" className="ghost sm" onClick={() => selectItem(row.master_item_id || row.financial_inventory_item_key)}>
                              {row.master_item_name || "—"}
                            </button>
                            <div className="muted small">
                              ID {row.master_item_id || "—"} · kunci {row.financial_inventory_item_key || "—"}
                              {row.master_missing ? " · master tidak ditemukan" : ""}
                            </div>
                            {reasons.map((anomaly, index) => (
                              <div key={`${anomaly.code || "reason"}-${index}`} data-testid="ledger-item-reason">{anomaly.code}: {anomaly.message}</div>
                            ))}
                          </td>
                          <td>{ledgerQtyText(row.physical_qty, row.base_uom)}</td>
                          <td>{ledgerQtyText(row.financial_qty, row.base_uom)}</td>
                          <td>{row.quantity_comparable ? ledgerQtyText(row.qty_difference, row.base_uom) : "Belum tersedia"}</td>
                          <td className={ledgerStatusClass(row.quantity_status)} data-testid={`ledger-item-status-${row.master_item_id || key}`}>
                            {statusText(row.quantity_status)}
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            )}
          </>
        )}
      </div>
    </section>
  );
}
