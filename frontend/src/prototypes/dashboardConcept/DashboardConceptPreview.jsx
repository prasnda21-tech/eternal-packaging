import { useCallback, useEffect, useId, useMemo, useRef, useState } from "react";
import {
  ArrowRight,
  BarChart3,
  Calculator,
  CalendarDays,
  CheckCircle2,
  ClipboardList,
  Clock3,
  FileText,
  LayoutDashboard,
  Package,
  RefreshCw,
  Target,
  Truck,
  Wallet,
  X,
} from "lucide-react";
import SalesDashboardDateInput from "../../components/widgetDashboard/SalesDashboardDateInput";
import {
  AS_OF_DATE,
  BRANCHES,
  CONCEPT_STAGES,
  DEFAULT_FILTERS,
  buildFinanceSnapshot,
  buildProductionSnapshot,
} from "./fixtures";
import {
  formatIdDate,
  formatPcs,
  formatPct,
  formatRpFull,
  formatRpJt,
} from "./format";
import "./DashboardConceptPreview.css";

/**
 * Isolated concept preview — Dashboard Keuangan & Produksi.
 * Route: /dev/dashboard-concept-preview (NODE_ENV !== production)
 * Local fixtures only; no business API.
 */

function DetailDrawer({ open, title, subtitle, filterText, onClose, children, returnFocusRef }) {
  const titleId = useId();
  const closeRef = useRef(null);
  const panelRef = useRef(null);

  useEffect(() => {
    if (!open) return undefined;
    const prev = returnFocusRef?.current;
    const t = window.setTimeout(() => closeRef.current?.focus(), 0);
    const onKey = (e) => {
      if (e.key === "Escape") {
        e.preventDefault();
        onClose();
      }
      if (e.key !== "Tab" || !panelRef.current) return;
      const focusables = panelRef.current.querySelectorAll(
        'button, [href], input, select, textarea, [tabindex]:not([tabindex="-1"])',
      );
      if (!focusables.length) return;
      const first = focusables[0];
      const last = focusables[focusables.length - 1];
      if (e.shiftKey && document.activeElement === first) {
        e.preventDefault();
        last.focus();
      } else if (!e.shiftKey && document.activeElement === last) {
        e.preventDefault();
        first.focus();
      }
    };
    document.addEventListener("keydown", onKey);
    return () => {
      window.clearTimeout(t);
      document.removeEventListener("keydown", onKey);
      if (prev && typeof prev.focus === "function") prev.focus();
    };
  }, [open, onClose, returnFocusRef]);

  if (!open) return null;
  return (
    <div className="dcp-drawer-backdrop" onClick={onClose} data-testid="dcp-drawer-backdrop">
      <aside
        className="dcp-drawer"
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        ref={panelRef}
        onClick={(e) => e.stopPropagation()}
        data-testid="dcp-drawer"
      >
        <div className="dcp-drawer-head">
          <div>
            <h3 id={titleId}>{title}</h3>
            {subtitle ? <p>{subtitle}</p> : null}
          </div>
          <button
            type="button"
            className="dcp-btn"
            ref={closeRef}
            onClick={onClose}
            aria-label="Tutup rincian"
            data-testid="dcp-drawer-close"
          >
            <X size={16} />
            Tutup
          </button>
        </div>
        <div className="dcp-drawer-body">
          {filterText ? (
            <div className="dcp-filter-strip" data-testid="dcp-drawer-filters">
              {filterText}
            </div>
          ) : null}
          {children}
        </div>
      </aside>
    </div>
  );
}

function KpiCard({ label, value, hint, icon, warn, onClick, testId }) {
  return (
    <button
      type="button"
      className="dcp-kpi"
      onClick={onClick}
      data-testid={testId}
    >
      <div className="dcp-kpi-top">
        <div className="dcp-kpi-label">{label}</div>
        <div className={`dcp-kpi-icon${warn ? " is-warn" : ""}`}>{icon}</div>
      </div>
      <div className={`dcp-kpi-value${warn ? " is-warn" : ""}`}>{value}</div>
      <div className="dcp-kpi-hint">{hint}</div>
    </button>
  );
}

function StatusPill({ kind, children }) {
  return <span className={`dcp-status is-${kind}`}>{children}</span>;
}

function FinanceView({ snap, onOpenKpi, onOpenInvoice, lastTriggerRef }) {
  if (snap.empty) {
    return (
      <div className="dcp-empty" data-testid="dcp-finance-empty">
        Tidak ada data keuangan untuk cabang/periode ini.
        <div>Coba periode Oktober atau September 2026.</div>
      </div>
    );
  }
  const { pl, ar, arTotal } = snap;
  const buckets = [
    { key: "current", label: "Belum jatuh tempo", amount: ar.current, color: "#1f7a4c" },
    { key: "d1_30", label: "1–30 hari", amount: ar.d1_30, color: "#0f766e" },
    { key: "d31_60", label: "31–60 hari", amount: ar.d31_60, color: "#c47d0e" },
    { key: "d60_plus", label: ">60 hari", amount: ar.d60_plus, color: "#c62828" },
  ];
  return (
    <>
      <div className="dcp-kpi-row" data-testid="dcp-finance-kpis">
        <KpiCard
          label="Omzet periode"
          value={formatRpJt(pl.omzet)}
          hint="Nilai faktur setelah diskon • mengikuti periode"
          icon={<BarChart3 size={15} />}
          testId="dcp-kpi-omzet"
          onClick={(e) => {
            lastTriggerRef.current = e.currentTarget;
            onOpenKpi("omzet");
          }}
        />
        <KpiCard
          label="Laba kotor"
          value={formatRpJt(pl.gross)}
          hint={`Margin ${formatPct(pl.marginPct)} • mengikuti periode`}
          icon={<Calculator size={15} />}
          testId="dcp-kpi-gross"
          onClick={(e) => {
            lastTriggerRef.current = e.currentTarget;
            onOpenKpi("gross");
          }}
        />
        <KpiCard
          label="Laba bersih"
          value={formatRpJt(pl.net)}
          hint="Setelah biaya operasional • mengikuti periode"
          icon={<FileText size={15} />}
          testId="dcp-kpi-net"
          onClick={(e) => {
            lastTriggerRef.current = e.currentTarget;
            onOpenKpi("net");
          }}
        />
        <KpiCard
          label="Kas & bank"
          value={formatRpJt(snap.cashBank)}
          hint={`Saldo saat ini • ${formatIdDate(snap.asOfDate)}`}
          icon={<Wallet size={15} />}
          testId="dcp-kpi-cash"
          onClick={(e) => {
            lastTriggerRef.current = e.currentTarget;
            onOpenKpi("cash");
          }}
        />
      </div>

      <div className="dcp-panels">
        <section className="dcp-panel" data-testid="dcp-pl-panel">
          <h2>Ringkasan laba rugi</h2>
          <p className="dcp-panel-note">Penyederhanaan prototype — bukan rumus akuntansi ERP final.</p>
          <div className="dcp-pl-row">
            <span>Omzet</span>
            <span className="dcp-pl-amt">{formatRpJt(pl.omzet)}</span>
          </div>
          <div className="dcp-pl-row">
            <span className="dcp-pl-minus">HPP</span>
            <span className="dcp-pl-amt">({formatRpJt(pl.hpp)})</span>
          </div>
          <div className="dcp-pl-row is-subtotal">
            <span>Laba kotor</span>
            <span className="dcp-pl-amt">{formatRpJt(pl.gross)}</span>
          </div>
          <div className="dcp-pl-row">
            <span className="dcp-pl-minus">Biaya operasional</span>
            <span className="dcp-pl-amt">({formatRpJt(pl.opex)})</span>
          </div>
          <div className="dcp-pl-row is-total">
            <span>Laba bersih</span>
            <span className="dcp-pl-amt">{formatRpJt(pl.net)}</span>
          </div>
        </section>

        <section className="dcp-panel" data-testid="dcp-ar-panel">
          <h2>Piutang saat ini</h2>
          <p className="dcp-panel-note">Saldo per {formatIdDate(snap.asOfDate)} — bukan akumulasi filter periode.</p>
          <div className="dcp-ar-total">
            <span>Total piutang</span>
            <strong>{formatRpJt(arTotal)}</strong>
          </div>
          {buckets.map((b) => {
            const pct = arTotal ? (b.amount / arTotal) * 100 : 0;
            return (
              <div className="dcp-ar-row" key={b.key}>
                <div>
                  {b.label}
                  <div className="dcp-cap-meta">{formatPct(pct, 0)}</div>
                </div>
                <div className="dcp-ar-bar" aria-hidden="true">
                  <div className="dcp-ar-fill" style={{ width: `${pct}%`, background: b.color }} />
                </div>
                <div className="dcp-ar-amt">{formatRpJt(b.amount)}</div>
              </div>
            );
          })}
        </section>
      </div>

      <section className="dcp-table-card" data-testid="dcp-followup-table">
        <h2>Perlu ditindaklanjuti</h2>
        <div className="dcp-table-scroll">
          <table className="dcp-table">
            <thead>
              <tr>
                <th>Pelanggan</th>
                <th>No. Faktur</th>
                <th>Jatuh tempo</th>
                <th>Sisa tagihan</th>
                <th>Status</th>
                <th>Tindakan</th>
              </tr>
            </thead>
            <tbody>
              {snap.followUps.map((row) => (
                <tr key={row.id}>
                  <td>{row.customer}</td>
                  <td>{row.invoiceNo}</td>
                  <td>{formatIdDate(row.dueDate)}</td>
                  <td className="num">{formatRpFull(row.remaining)}</td>
                  <td>
                    {row.status === "late" ? (
                      <StatusPill kind="late">Terlambat {row.lateDays} hari</StatusPill>
                    ) : (
                      <StatusPill kind="current">Belum jatuh tempo</StatusPill>
                    )}
                  </td>
                  <td>
                    <button
                      type="button"
                      className="dcp-link"
                      data-testid={`dcp-invoice-${row.id}`}
                      onClick={(e) => {
                        lastTriggerRef.current = e.currentTarget;
                        onOpenInvoice(row);
                      }}
                    >
                      Lihat rincian →
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>
    </>
  );
}

function ProductionView({ snap, onOpenKpi, onOpenPo, onOpenSchedule, lastTriggerRef }) {
  if (snap.empty) {
    return (
      <div className="dcp-empty" data-testid="dcp-prod-empty">
        Tidak ada data produksi untuk cabang/periode ini.
        <div>Coba periode Oktober atau September 2026.</div>
      </div>
    );
  }
  const stageIcons = {
    penjadwalan: <CalendarDays size={14} />,
    sablon: <Package size={14} />,
    qc: <ClipboardList size={14} />,
    loading: <Truck size={14} />,
    pengiriman: <CheckCircle2 size={14} />,
  };
  return (
    <>
      <div className="dcp-kpi-row" data-testid="dcp-prod-kpis">
        <KpiCard
          label="PO aktif"
          value={`${snap.kpis.activePoUnique} PO`}
          hint="Dalam antrean produksi • PO unik"
          icon={<FileText size={15} />}
          testId="dcp-kpi-po-active"
          onClick={(e) => {
            lastTriggerRef.current = e.currentTarget;
            onOpenKpi("poActive");
          }}
        />
        <KpiCard
          label="Terlambat"
          value={`${snap.kpis.latePoUnique} PO`}
          hint="Melewati tenggat produksi • PO unik"
          icon={<Clock3 size={15} />}
          warn
          testId="dcp-kpi-po-late"
          onClick={(e) => {
            lastTriggerRef.current = e.currentTarget;
            onOpenKpi("poLate");
          }}
        />
        <KpiCard
          label="Target hari ini"
          value={formatPcs(snap.kpis.targetPcs)}
          hint="Total rencana semua lini"
          icon={<Target size={15} />}
          testId="dcp-kpi-target"
          onClick={(e) => {
            lastTriggerRef.current = e.currentTarget;
            onOpenKpi("target");
          }}
        />
        <KpiCard
          label="Selesai hari ini"
          value={formatPcs(snap.kpis.donePcs)}
          hint={`${formatPct(snap.kpis.donePct)} dari target`}
          icon={<CheckCircle2 size={15} />}
          testId="dcp-kpi-done"
          onClick={(e) => {
            lastTriggerRef.current = e.currentTarget;
            onOpenKpi("done");
          }}
        />
      </div>

      <div className="dcp-panels">
        <section className="dcp-panel" data-testid="dcp-stage-panel">
          <h2>Antrean per tahap</h2>
          <p className="dcp-panel-note">
            Angka adalah jumlah pekerjaan per tahap, bukan total PO unik lintas tahap.
            Konsep tahap ini disederhanakan dari gambar; bukan peta 1:1 workflow ERP.
          </p>
          <div className="dcp-stage-flow">
            {snap.stageQueue.map((st, idx) => (
              <div key={st.key} style={{ display: "contents" }}>
                {idx > 0 ? (
                  <div className="dcp-stage-arrow" aria-hidden="true">
                    <ArrowRight size={14} />
                  </div>
                ) : null}
                <div className="dcp-stage">
                  <div className="dcp-stage-icon">{stageIcons[st.key]}</div>
                  <strong>{st.label}</strong>
                  <span>{st.jobCount} pekerjaan</span>
                  <span>{st.uniquePo} PO unik</span>
                </div>
              </div>
            ))}
          </div>
        </section>

        <section className="dcp-panel" data-testid="dcp-capacity-panel">
          <h2>Kapasitas hari ini</h2>
          <p className="dcp-panel-note">Rencana pcs vs kapasitas pcs — bukan output aktual.</p>
          {snap.capacity.map((c) => {
            const amber = c.utilPct < 70;
            return (
              <div className="dcp-cap-row" key={c.id}>
                <div>
                  <strong>{c.name}</strong>
                  <div className="dcp-cap-meta">
                    Rencana {formatPcs(c.planned)} / Kapasitas {formatPcs(c.capacity)}
                  </div>
                </div>
                <div className="dcp-ar-bar" aria-hidden="true">
                  <div
                    className="dcp-ar-fill"
                    style={{
                      width: `${Math.min(100, c.utilPct)}%`,
                      background: amber ? "#c47d0e" : "#1f7a4c",
                    }}
                  />
                </div>
                <div className={`dcp-cap-pct${amber ? " is-amber" : ""}`}>{formatPct(c.utilPct, 0)}</div>
              </div>
            );
          })}
        </section>
      </div>

      <section className="dcp-table-card" data-testid="dcp-priority-table">
        <div style={{ display: "flex", justifyContent: "space-between", gap: 12, alignItems: "center" }}>
          <h2 style={{ margin: 0 }}>Prioritas produksi</h2>
          <button
            type="button"
            className="dcp-btn"
            data-testid="dcp-open-schedule"
            onClick={(e) => {
              lastTriggerRef.current = e.currentTarget;
              onOpenSchedule();
            }}
          >
            <CalendarDays size={14} />
            Buka jadwal
          </button>
        </div>
        <div className="dcp-table-scroll" style={{ marginTop: 10 }}>
          <table className="dcp-table">
            <thead>
              <tr>
                <th>PO / Pelanggan</th>
                <th>Label</th>
                <th>Tahap</th>
                <th>Tenggat</th>
                <th>Sisa produksi</th>
                <th>Tindakan</th>
              </tr>
            </thead>
            <tbody>
              {snap.priorities.map((row) => {
                const stageLabel = CONCEPT_STAGES.find((s) => s.key === row.stage)?.label || row.stage;
                const labelKind = row.label === "urgent" ? "amber" : row.label === "buffer" ? "buffer" : "normal";
                const labelText = row.label === "urgent" ? "Urgent" : row.label === "buffer" ? "Buffer" : "Normal";
                return (
                  <tr key={row.id}>
                    <td>
                      <strong>{row.poNumber}</strong>
                      <div className="dcp-cap-meta">{row.customer}</div>
                    </td>
                    <td>
                      <StatusPill kind={labelKind}>{labelText}</StatusPill>
                    </td>
                    <td>{stageLabel}</td>
                    <td>{formatIdDate(row.dueDate)}</td>
                    <td className="num">{formatPcs(row.sisaProduksi)}</td>
                    <td>
                      <button
                        type="button"
                        className="dcp-link"
                        data-testid={`dcp-po-${row.id}`}
                        onClick={(e) => {
                          lastTriggerRef.current = e.currentTarget;
                          onOpenPo(row);
                        }}
                      >
                        Buka workflow →
                      </button>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </section>
    </>
  );
}

export default function DashboardConceptPreview() {
  const [view, setView] = useState("finance");
  const [branchId, setBranchId] = useState(DEFAULT_FILTERS.branchId);
  const [dateFrom, setDateFrom] = useState(DEFAULT_FILTERS.dateFrom);
  const [dateTo, setDateTo] = useState(DEFAULT_FILTERS.dateTo);
  const [refreshTick, setRefreshTick] = useState(0);
  const [drawer, setDrawer] = useState(null);
  const lastTriggerRef = useRef(null);

  const finance = useMemo(
    () => buildFinanceSnapshot({ branchId, dateFrom, dateTo }),
    [branchId, dateFrom, dateTo, refreshTick],
  );
  const production = useMemo(
    () => buildProductionSnapshot({ branchId, dateFrom, dateTo }),
    [branchId, dateFrom, dateTo, refreshTick],
  );

  const branchLabel = BRANCHES.find((b) => b.id === branchId)?.label || branchId;
  const filterText = `Filter aktif: ${branchLabel} · ${formatIdDate(dateFrom)} – ${formatIdDate(dateTo)}`;

  const closeDrawer = useCallback(() => setDrawer(null), []);

  const drawerBody = useMemo(() => {
    if (!drawer) return null;
    if (drawer.type === "finance-kpi") {
      const pl = finance.pl || {};
      const rows = {
        omzet: [
          ["Omzet periode", formatRpFull(pl.omzet)],
          ["Sumber", "Fixture lokal (nilai faktur setelah diskon)"],
          ["Cakupan", "Mengikuti periode terpilih"],
        ],
        gross: [
          ["Laba kotor", formatRpFull(pl.gross)],
          ["Margin", formatPct(pl.marginPct)],
          ["Omzet − HPP", `${formatRpFull(pl.omzet)} − ${formatRpFull(pl.hpp)}`],
        ],
        net: [
          ["Laba bersih", formatRpFull(pl.net)],
          ["Laba kotor", formatRpFull(pl.gross)],
          ["Biaya operasional", formatRpFull(pl.opex)],
        ],
        cash: [
          ["Kas & bank", formatRpFull(finance.cashBank)],
          ["Saldo per", formatIdDate(finance.asOfDate)],
          ["Catatan", "Tidak mengikuti filter periode P&L"],
        ],
      }[drawer.key] || [];
      return rows.map(([k, v]) => (
        <div className="dcp-kv" key={k}>
          <span>{k}</span>
          <strong>{v}</strong>
        </div>
      ));
    }
    if (drawer.type === "invoice") {
      const row = drawer.row;
      return (
        <>
          <div className="dcp-kv"><span>Pelanggan</span><strong>{row.customer}</strong></div>
          <div className="dcp-kv"><span>No. Faktur</span><strong>{row.invoiceNo}</strong></div>
          <div className="dcp-kv"><span>Jatuh tempo</span><strong>{formatIdDate(row.dueDate)}</strong></div>
          <div className="dcp-kv"><span>Sisa tagihan</span><strong>{formatRpFull(row.remaining)}</strong></div>
          <div className="dcp-kv">
            <span>Status</span>
            <strong>
              {row.status === "late" ? `Terlambat ${row.lateDays} hari` : "Belum jatuh tempo"}
            </strong>
          </div>
          <p className="dcp-foot-note">Prototype: tidak ada aksi pembayaran atau mutasi.</p>
        </>
      );
    }
    if (drawer.type === "prod-kpi") {
      const k = production.kpis || {};
      const rows = {
        poActive: [
          ["PO aktif (unik)", `${k.activePoUnique} PO`],
          ["Definisi", "PO unik dalam antrean fixture"],
        ],
        poLate: [
          ["PO terlambat (unik)", `${k.latePoUnique} PO`],
          ["Definisi", "PO unik melewati tenggat produksi"],
        ],
        target: [
          ["Target hari ini", formatPcs(k.targetPcs)],
          ["Sumber", "Jumlah rencana kapasitas lini"],
        ],
        done: [
          ["Selesai hari ini", formatPcs(k.donePcs)],
          ["% target", formatPct(k.donePct)],
        ],
      }[drawer.key] || [];
      return rows.map(([label, value]) => (
        <div className="dcp-kv" key={label}>
          <span>{label}</span>
          <strong>{value}</strong>
        </div>
      ));
    }
    if (drawer.type === "po") {
      const row = drawer.row;
      const stageLabel = CONCEPT_STAGES.find((s) => s.key === row.stage)?.label || row.stage;
      return (
        <>
          <div className="dcp-kv"><span>PO</span><strong>{row.poNumber}</strong></div>
          <div className="dcp-kv"><span>Pelanggan</span><strong>{row.customer}</strong></div>
          <div className="dcp-kv"><span>Tahap konsep</span><strong>{stageLabel}</strong></div>
          <div className="dcp-kv"><span>Tenggat</span><strong>{formatIdDate(row.dueDate)}</strong></div>
          <div className="dcp-kv"><span>Sisa produksi</span><strong>{formatPcs(row.sisaProduksi)}</strong></div>
          <div className="dcp-kv"><span>Sisa kirim</span><strong>{formatPcs(row.sisaKirim)}</strong></div>
          <p className="dcp-foot-note">
            Sisa produksi dan sisa kirim ditampilkan terpisah. PO di Loading/Pengiriman boleh punya sisa kirim
            dengan sisa produksi 0.
          </p>
        </>
      );
    }
    if (drawer.type === "schedule") {
      return (
        <>
          <div className="dcp-kv"><span>Tanggal jadwal</span><strong>{formatIdDate(AS_OF_DATE)}</strong></div>
          <div className="dcp-kv"><span>Mesin otomatis</span><strong>Shift A · 08:00–16:00</strong></div>
          <div className="dcp-kv"><span>Semi otomatis A</span><strong>Shift A · 08:00–15:00</strong></div>
          <div className="dcp-kv"><span>Semi otomatis B</span><strong>Shift B · 13:00–21:00</strong></div>
          <p className="dcp-foot-note">Jadwal dummy — tidak membuka halaman produksi nyata.</p>
        </>
      );
    }
    return null;
  }, [drawer, finance, production]);

  const drawerTitle = drawer?.title || "Rincian";
  const drawerSubtitle = drawer?.subtitle || "";

  return (
    <div className="dcp-root" data-testid="dcp-root">
      <aside className="dcp-sidebar" aria-label="Navigasi prototype">
        <div className="dcp-brand">
          <img src="/eternal-favicon.png" alt="" />
          <div>
            <strong>Eternal Packaging</strong>
            <span>Solusi Kemasan Plastik</span>
          </div>
        </div>
        <div className="dcp-nav-group">
          <div className="dcp-nav-label">Utama</div>
          <button type="button" className="dcp-nav-item is-active">
            <LayoutDashboard size={14} style={{ marginRight: 6, verticalAlign: -2 }} />
            Dashboard
          </button>
        </div>
        <div className="dcp-nav-group">
          <div className="dcp-nav-label">Prototype</div>
          <button
            type="button"
            className={`dcp-nav-item is-clickable${view === "finance" ? " is-active" : ""}`}
            onClick={() => setView("finance")}
            data-testid="dcp-nav-finance"
          >
            Keuangan
          </button>
          <button
            type="button"
            className={`dcp-nav-item is-clickable${view === "production" ? " is-active" : ""}`}
            onClick={() => setView("production")}
            data-testid="dcp-nav-production"
          >
            Produksi
          </button>
        </div>
        <div className="dcp-nav-group" style={{ marginTop: "auto" }}>
          <div className="dcp-nav-label">Catatan</div>
          <div className="dcp-nav-item" style={{ opacity: 0.7, fontSize: 11, lineHeight: 1.4 }}>
            Sidebar facsimile untuk tinjauan konsep. Tidak mengubah menu produksi.
          </div>
        </div>
      </aside>

      <div className="dcp-main">
        <header className="dcp-topbar">
          <div className="dcp-search">Cari menu…</div>
          <div className="dcp-topbar-meta">
            <span className="dcp-chip">{branchLabel}</span>
            <span className="dcp-chip">Direktur</span>
          </div>
        </header>

        <div className="dcp-workspace">
          <div className="dcp-head">
            <div>
              <h1>{view === "finance" ? "Dashboard Keuangan" : "Dashboard Produksi"}</h1>
              <p>
                {view === "finance"
                  ? "Laba, likuiditas, dan prioritas penagihan."
                  : "Prioritas hari ini dan kesiapan pengiriman."}
              </p>
            </div>
            <div className="dcp-proto-badge">Prototype • Data contoh</div>
          </div>

          <div className="dcp-tabs" role="tablist" aria-label="Jenis dashboard">
            <button
              type="button"
              role="tab"
              aria-selected={view === "finance"}
              className={`dcp-tab${view === "finance" ? " is-active" : ""}`}
              onClick={() => setView("finance")}
              data-testid="dcp-tab-finance"
            >
              Keuangan
            </button>
            <button
              type="button"
              role="tab"
              aria-selected={view === "production"}
              className={`dcp-tab${view === "production" ? " is-active" : ""}`}
              onClick={() => setView("production")}
              data-testid="dcp-tab-production"
            >
              Produksi
            </button>
          </div>

          <div className="dcp-toolbar" data-testid="dcp-toolbar">
            <div className="dcp-field">
              <label htmlFor="dcp-branch">Cabang</label>
              <select
                id="dcp-branch"
                value={branchId}
                onChange={(e) => setBranchId(e.target.value)}
                data-testid="dcp-branch"
              >
                {BRANCHES.map((b) => (
                  <option key={b.id} value={b.id}>{b.label}</option>
                ))}
              </select>
            </div>
            <div className="dcp-field">
              <label>Tanggal mulai</label>
              <SalesDashboardDateInput
                valueIso={dateFrom}
                onChange={setDateFrom}
                ariaLabel="Tanggal mulai"
                testId="dcp-date-from"
              />
            </div>
            <div className="dcp-field">
              <label>Tanggal akhir</label>
              <SalesDashboardDateInput
                valueIso={dateTo}
                onChange={setDateTo}
                ariaLabel="Tanggal akhir"
                testId="dcp-date-to"
              />
            </div>
            <div className="dcp-toolbar-actions">
              <button
                type="button"
                className="dcp-btn"
                data-testid="dcp-refresh"
                onClick={() => setRefreshTick((n) => n + 1)}
              >
                <RefreshCw size={14} />
                Refresh
              </button>
              {view === "production" ? (
                <button
                  type="button"
                  className="dcp-btn-primary dcp-btn"
                  data-testid="dcp-primary-schedule"
                  onClick={(e) => {
                    lastTriggerRef.current = e.currentTarget;
                    setDrawer({
                      type: "schedule",
                      title: "Jadwal produksi (dummy)",
                      subtitle: "Ringkasan shift konsep",
                    });
                  }}
                >
                  <CalendarDays size={14} />
                  Buka jadwal
                </button>
              ) : null}
            </div>
          </div>

          {view === "finance" ? (
            <FinanceView
              snap={finance}
              lastTriggerRef={lastTriggerRef}
              onOpenKpi={(key) => {
                const titles = {
                  omzet: "Rincian omzet periode",
                  gross: "Rincian laba kotor",
                  net: "Rincian laba bersih",
                  cash: "Rincian kas & bank",
                };
                setDrawer({ type: "finance-kpi", key, title: titles[key], subtitle: "Sumber fixture lokal" });
              }}
              onOpenInvoice={(row) => {
                setDrawer({
                  type: "invoice",
                  row,
                  title: "Detail faktur (dummy)",
                  subtitle: row.invoiceNo,
                });
              }}
            />
          ) : (
            <ProductionView
              snap={production}
              lastTriggerRef={lastTriggerRef}
              onOpenKpi={(key) => {
                const titles = {
                  poActive: "Rincian PO aktif",
                  poLate: "Rincian PO terlambat",
                  target: "Rincian target hari ini",
                  done: "Rincian selesai hari ini",
                };
                setDrawer({ type: "prod-kpi", key, title: titles[key], subtitle: "Sumber fixture lokal" });
              }}
              onOpenPo={(row) => {
                setDrawer({
                  type: "po",
                  row,
                  title: "Ringkasan workflow PO (dummy)",
                  subtitle: row.poNumber,
                });
              }}
              onOpenSchedule={() => {
                setDrawer({
                  type: "schedule",
                  title: "Jadwal produksi (dummy)",
                  subtitle: "Ringkasan shift konsep",
                });
              }}
            />
          )}

          <p className="dcp-foot-note">
            Prototype lokal/dummy. Belum menjamin seluruh fitur dashboard existing terwakili.
            Tidak terhubung ke database/API produksi.
          </p>
        </div>
      </div>

      <DetailDrawer
        open={Boolean(drawer)}
        title={drawerTitle}
        subtitle={drawerSubtitle}
        filterText={filterText}
        onClose={closeDrawer}
        returnFocusRef={lastTriggerRef}
      >
        {drawerBody}
      </DetailDrawer>
    </div>
  );
}
