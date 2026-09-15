import { Link } from "react-router-dom";
import { XCircle } from "lucide-react";
import { formatDate, formatDisplayDate } from "../lib/constants";
import { formatIdQty, operatorPrintableItems } from "../lib/productionScheduleQty";
import { screenSizeLabel } from "../lib/screenSize";
import {
  PRODUCTION_SCHEDULE_TABLE_COLS,
  productionSheetCellClassName,
} from "../lib/productionScheduleTable";
import { isProductionScheduleH1, isProductionScheduleUrgent } from "../lib/productionSchedulePriority";
import { cupMarkTone, prodMarkTone, screenMarkTone, packMarkTone, loadMarkTone, kirimMarkTone } from "../lib/mobileOpsStatusMark";

function MobileRoleMark({ tone, title, testid }) {
  const kind = tone === "done" || tone === "pending" ? tone : "idle";
  const label = kind === "done" ? "selesai" : kind === "pending" ? "berjalan" : "tidak tersedia";
  return (
    <span className={`mop-ops-mark is-${kind}`} title={title || label} data-testid={testid} aria-label={label}>
      {kind === "done" ? "✓" : kind === "idle" ? "—" : ""}
    </span>
  );
}

function DeadlineShipCell({ row, variant }) {
  const tanggal =
    row.tanggal_pengiriman
    || row.effective_delivery_date
    || row.delivery_deadline_effective;
  if (!tanggal) {
    return <span className="muted small">Menunggu approval</span>;
  }
  const isManual = row.delivery_deadline_source === "manual" || row.tanggal_pengiriman_source === "manual";
  const sourceTestId = variant === "desktop" ? `prod-deadline-source-${row.po_number}` : undefined;
  const statusTestId = variant === "desktop" ? `prod-deadline-status-${row.po_number}` : undefined;
  return (
    <>
      {formatDate(tanggal)}
      {" "}
      <span
        className="pill sm"
        title={isManual ? "Manual" : "Otomatis"}
        style={{
          background: isManual ? "#ffe9b8" : "#e6ecf3",
          color: isManual ? "#7a5a1e" : "#3a4b60",
        }}
        data-testid={sourceTestId}
      >
        {isManual ? "Manual" : "Otomatis"}
      </span>
      {row.delivery_deadline_status === "overdue" && (
        <span className="urgent-badge" data-testid={statusTestId}>OVERDUE</span>
      )}
      {row.delivery_deadline_status === "h6" && (
        <span className="urgent-badge" data-testid={statusTestId}>≤6 JAM</span>
      )}
      {isProductionScheduleH1(row) && (
        <span className="standard-badge prod-h1-badge" data-testid={statusTestId}>H-1</span>
      )}
    </>
  );
}

/** Display-only Tanggal Pengerjaan (dd/mm/yy). Empty/invalid keeps the existing cell empty token. */
function formatWorkDateDisplay(value) {
  if (value == null || String(value).trim() === "") return "";
  const formatted = formatDisplayDate(value);
  if (!formatted || formatted === "-") return "";
  return formatted;
}

function WorkDateCell({ row, variant }) {
  const displayDate = row.displayOperatorKey
    ? (row.displayWorkDate || row.work_date)
    : null;
  if (row.displayOperatorKey) {
    const formatted = formatWorkDateDisplay(displayDate);
    if (formatted) {
      return (
        <span data-testid={variant === "desktop" ? `prod-order-work-date-${row.po_number}` : undefined}>
          {formatted}
        </span>
      );
    }
    return <span className="muted small">Belum Dijadwalkan</span>;
  }
  if ((row.operator_work_dates || []).length > 0) {
    return (row.operator_work_dates || []).map((entry, i) => (
      <div
        key={entry.split_id || i}
        className="prod-date-line"
        data-testid={variant === "desktop" ? `prod-op-work-date-${row.po_number}-${i}` : undefined}
      >
        {formatWorkDateDisplay(entry.work_date) || "—"}
      </div>
    ));
  }
  const formatted = formatWorkDateDisplay(row.work_date);
  if (formatted) {
    return (
      <span data-testid={variant === "desktop" ? `prod-order-work-date-${row.po_number}` : undefined}>
        {formatted}
      </span>
    );
  }
  return <span className="muted small">Belum Dijadwalkan</span>;
}

function TotalQtyCell({ qtyProg }) {
  return qtyProg?.text ?? "0";
}

function ItemCell({ row, variant }) {
  const items = operatorPrintableItems(row.displayOperator?.splits || []);
  if (!items.length) return <span className="muted small">—</span>;
  return items.map((item) => {
    const qtyPresent = item.quantity !== undefined && item.quantity !== null && item.quantity !== "";
    return (
      <div
        key={item.key}
        className={`prod-schedule-item-line${variant === "mobile" ? " mop-ops-item-line" : ""}`}
        title={item.name}
        data-testid={variant === "desktop"
          ? `prod-item-line-${row.po_number}-${item.key}`
          : `mobile-schedule-item-line-${row.po_number}-${item.key}`}
      >
        <span className="mop-ops-item-name">{item.name}</span>
        {variant === "mobile" && qtyPresent ? (
          <span className="mop-ops-item-qty">{formatIdQty(item.quantity)}</span>
        ) : null}
      </div>
    );
  });
}

function ScreenSizeCell({ row, variant }) {
  const items = operatorPrintableItems(row.displayOperator?.splits || []);
  if (!items.length) return <span className="muted small">—</span>;
  return items.map((item) => (
    <div
      key={item.key}
      className="prod-schedule-item-line"
      data-testid={variant === "desktop"
        ? `prod-screen-size-${row.po_number}-${item.key}`
        : `mobile-schedule-screen-size-${row.po_number}-${item.key}`}
    >
      {screenSizeLabel(item.screen_size)}
    </div>
  ));
}

function OperatorCell({ row, qtyProg, variant }) {
  if (row.displayOperatorKey) {
    const op = row.displayOperator;
    if (!op) return <span className="muted small">Belum ditentukan</span>;
    return (
      <div className="prod-op-line" data-testid={variant === "desktop" ? `prod-ops-line-${row.po_number}` : undefined}>
        <span className="prod-op-name">{op.operator_name || "Belum ditentukan"}</span>
        {op.quantity != null && op.quantity !== "" ? (
          <span className="prod-op-qty muted small mono">{formatIdQty(op.quantity)}</span>
        ) : null}
      </div>
    );
  }
  if (qtyProg.groups?.length) {
    return qtyProg.groups.map((g) => (
      <div
        key={g.key}
        className="prod-line-ops-block"
        style={{ minHeight: `${Math.max((g.operators || []).length, 1) * 1.35}em` }}
        data-testid={variant === "desktop" ? `prod-ops-line-${row.po_number}-${g.key}` : undefined}
      >
        {(g.operators || []).map((op, i) => (
          <div key={`${op.operator_email || op.operator_user_id || i}`} className="prod-op-line">
            <span className="prod-op-name">{op.operator_name || "-"}</span>
            <span className="prod-op-qty muted small mono">{formatIdQty(op.quantity)}</span>
          </div>
        ))}
      </div>
    ));
  }
  if (!(row.operators || []).length) return <span className="muted small">Belum ditentukan</span>;
  return (row.operators || []).map((op, i) => (
    <div key={i} className="prod-op-line">
      <span className="prod-op-name">{op.operator_name || "-"}</span>
    </div>
  ));
}

function statusFinalChipClass(row) {
  if (row.cancelled || row.status_final === "Cancelled") return "pill-cancel";
  if (row.status_final === "HOLD") return "pill-hold";
  if (row.status_final === "Terkirim") return "pill-ok";
  const label = String(row.status_final || "");
  if (/^(Packing|Cetak|Operator|Loading|Pengiriman|Screener|Persiapan Helper)$/i.test(label)) return "pill-progress";
  if (/^(Order Masuk|Design|Approval Design|Payment|Approval Payment|Penjadwalan|Surat Jalan)$/i.test(label)) return "pill-warn";
  return "pill-muted";
}

function StatusCell({ row }) {
  if (row.cancelled) {
    return <span className="pill sm pill-cancel prod-status-akhir"><XCircle size={10} /> Cancelled</span>;
  }
  if (row.status_final === "HOLD") return <span className="pill sm pill-hold prod-status-akhir">HOLD</span>;
  if (row.status_final === "Terkirim") return <span className="pill sm pill-ok prod-status-akhir">Terkirim</span>;
  return <span className={`pill sm prod-status-akhir ${statusFinalChipClass(row)}`}>{row.status_final}</span>;
}

function cellContent(col, ctx) {
  const { row, variant, qtyProg, cetakProg, isToday, poHref } = ctx;
  switch (col.key) {
    case "po":
      return variant === "desktop" && poHref ? (
        <Link to={poHref} data-testid={`prod-po-link-${row.po_number}`}>{row.po_number}</Link>
      ) : row.po_number;
    case "customer":
      if (variant === "mobile") {
        return (
          <div className="mop-ops-identity" data-testid={`mobile-schedule-identity-${row.po_number}`}>
            <strong className="mop-ops-customer">{row.customer || "-"}</strong>
            <span className="mop-ops-po">{row.po_number || ""}</span>
          </div>
        );
      }
      return <strong>{row.customer || "-"}</strong>;
    case "outlet":
      return row.outlet || "-";
    case "deadline":
      return formatDate(
        row.tanggal_pengiriman
        || row.effective_delivery_date
        || row.delivery_deadline_effective
        || row.scheduled_delivery_date
        || row.delivery_deadline_manual
      );
    case "deadline_ship":
      return <DeadlineShipCell row={row} variant={variant} />;
    case "work_date":
      return <WorkDateCell row={row} variant={variant} />;
    case "prio":
      if (variant === "mobile" && ctx.opsBadge) {
        return (
          <div className="mop-ops-prio">
            <span className={`mop-ops-badge mop-ops-badge-${ctx.opsBadge.tone}`}>{ctx.opsBadge.label}</span>
            {ctx.opsBadge.caption ? (
              <span className={`mop-ops-prio-caption is-${ctx.opsBadge.captionTone || ctx.opsBadge.tone}`}>
                {ctx.opsBadge.caption}
              </span>
            ) : null}
          </div>
        );
      }
      if (ctx.opsBadge) {
        return <span className={`mop-ops-badge mop-ops-badge-${ctx.opsBadge.tone}`}>{ctx.opsBadge.label}</span>;
      }
      return isProductionScheduleUrgent(row) ? <span className="urgent-badge">URGENT</span> : <span className="muted small">Normal</span>;
    case "buffer":
      return row.buffer_stock
        ? <span className="pill sm" style={{ background: "#e6ecf3", color: "#3a4b60" }}>Buffer</span>
        : <span className="muted small">—</span>;
    case "item":
      return <ItemCell row={row} variant={variant} />;
    case "qty":
      if (variant === "mobile") {
        return <strong className="mop-ops-qty-total">{qtyProg?.text ?? "0"}</strong>;
      }
      return <TotalQtyCell qtyProg={qtyProg} />;
    case "screen_size":
      return <ScreenSizeCell row={row} variant={variant} />;
    case "screen": {
      if (variant === "mobile") {
        return (
          <MobileRoleMark
            tone={screenMarkTone(row)}
            title={row.screen_qty == null || String(row.screen_qty).trim() === "" ? "Screen belum" : `Screen ${formatIdQty(Number(row.screen_qty) || 0)}`}
            testid={`prod-screen-qty-${row.po_number}`}
          />
        );
      }
      if (row.screen_qty == null || String(row.screen_qty).trim() === "") return "-";
      const done = Number(row.screen_qty) || 0;
      return (
        <span className="mop-qty-progress" data-testid={`prod-screen-qty-${row.po_number}`} title={`Screen ${formatIdQty(done)}`}>
          <span>{formatIdQty(done)}</span>
        </span>
      );
    }
    case "qty_cetak":
      if (variant === "mobile") {
        return (
          <MobileRoleMark
            tone={prodMarkTone(row)}
            title={cetakProg?.text || "Produksi"}
            testid={`prod-qty-cetak-${row.po_number}`}
          />
        );
      }
      return cetakProg.text;
    case "operator":
      return <OperatorCell row={row} qtyProg={qtyProg} variant={variant} />;
    case "design":
      return (
        <>
          <span className={"pill sm " + ((row.design_status === "Approved") ? "pill-ok" : "pill-warn")}>{row.design_status || "-"}</span>
          {row.design_bypassed && <span className="pill sm pill-bypass" title="Bypass">BYPASS</span>}
        </>
      );
    case "payment":
      return (
        <>
          <span className={"pill sm " + ((row.payment_status === "Approved") ? "pill-ok" : "pill-warn")}>{row.payment_status || "-"}</span>
          {row.payment_bypassed && <span className="pill sm pill-bypass" title="Bypass">BYPASS</span>}
        </>
      );
    case "hold":
      return row.hold
        ? <span className="pill sm pill-hold" title={(row.hold_reasons || []).join("; ")}>HOLD</span>
        : <span className="muted small">-</span>;
    case "helper": {
      if (variant === "mobile") {
        return (
          <MobileRoleMark
            tone={cupMarkTone(row)}
            title={row.helper_required == null ? "Cup tidak tersedia" : `${formatIdQty(row.helper_prepared ?? 0)} / ${formatIdQty(row.helper_required)}`}
            testid={`prod-helper-${row.po_number}`}
          />
        );
      }
      if (row.helper_required == null) return <span className="muted">-</span>;
      const done = Number(row.helper_prepared ?? 0);
      const target = Number(row.helper_required) || 0;
      const pct = target > 0 ? Math.min(100, Math.round((done / target) * 100)) : 0;
      return (
        <span className={`mop-qty-progress${row.helper_shortage_status === "Barang Kurang" ? " prod-helper-warn" : ""}`}>
          <span>
            {formatIdQty(done)} / {formatIdQty(target)}
            {row.helper_shortage ? ` (-${formatIdQty(row.helper_shortage)})` : ""}
          </span>
          {target > 0 ? (
            <span className="mop-qty-progress-bar" aria-hidden>
              <span className="mop-qty-progress-fill" style={{ width: `${pct}%` }} />
            </span>
          ) : null}
        </span>
      );
    }
    case "packing":
      if (variant === "mobile") {
        return (
          <MobileRoleMark
            tone={packMarkTone(row)}
            title={row.packing_status || "Packing"}
            testid={`prod-packing-${row.po_number}`}
          />
        );
      }
      return row.packing_status === "Sudah" ? <span className="pill sm pill-ok">Sudah</span> :
        row.packing_status === "Berjalan" ? <span className="pill sm pill-progress">Berjalan</span> :
        <span className="pill sm pill-muted">Belum</span>;
    case "loading":
      if (variant === "mobile") {
        return (
          <MobileRoleMark
            tone={loadMarkTone()}
            title="Loading tidak tersedia dari payload jadwal produksi"
            testid={`prod-loading-${row.po_number}`}
          />
        );
      }
      return null;
    case "shipped":
      if (variant === "mobile") {
        return (
          <MobileRoleMark
            tone={kirimMarkTone(row)}
            title="Pengiriman"
            testid={`prod-shipped-${row.po_number}`}
          />
        );
      }
      return null;
    case "sj":
      return (
        <span data-testid={variant === "mobile" ? `mobile-schedule-sj-${row.po_number}` : undefined}>
          {row.delivery_note_number || "-"}
        </span>
      );
    case "driver":
      return row.driver_name || "-";
    case "status":
      return <StatusCell row={row} />;
    default:
      return null;
  }
}

function cellExtras(col, { row, variant, cetakProg, isToday }) {
  const extras = [];
  const testids = {};
  if (col.key === "customer") {
    extras.push(variant !== "mobile" && isToday ? "prod-cell-today" : "");
    if (variant === "desktop") testids["data-testid"] = `prod-customer-${row.po_number}`;
  }
  if (col.key === "deadline_ship") {
    extras.push("prod-cell-stack");
    if (variant === "desktop") testids["data-testid"] = `prod-deadline-effective-${row.po_number}`;
  }
  if (col.key === "work_date") {
    extras.push("prod-cell-stack");
    if (variant === "desktop") testids["data-testid"] = `prod-work-date-${row.po_number}`;
  }
  if (col.key === "buffer" && variant === "desktop") {
    testids["data-testid"] = `prod-buffer-${row.po_number}`;
  }
  if (col.key === "item") {
    extras.push("prod-schedule-item-cell");
    if (variant === "desktop") testids["data-testid"] = `prod-item-${row.po_number}`;
    else testids["data-testid"] = `mobile-schedule-item-${row.po_number}`;
  }
  if (col.key === "screen_size") {
    extras.push("prod-schedule-item-cell");
    if (variant === "desktop") testids["data-testid"] = `prod-screen-size-${row.po_number}`;
    else testids["data-testid"] = `mobile-schedule-screen-size-${row.po_number}`;
  }
  if (col.key === "qty") {
    extras.push("prod-qty-cell");
    if (variant === "desktop") testids["data-testid"] = `prod-qty-${row.po_number}`;
  }
  if (col.key === "qty_cetak") {
    extras.push(cetakProg.over ? "prod-qty-over" : "");
    if (variant === "desktop") testids["data-testid"] = `prod-qty-cetak-${row.po_number}`;
  }
  if (col.key === "operator") extras.push("prod-ops");
  if (col.key === "packing" && variant === "desktop") {
    testids["data-testid"] = `prod-packing-${row.po_number}`;
  }
  if (col.key === "status" && variant === "desktop") {
    extras.push("prod-status-akhir-cell");
    testids["data-testid"] = `prod-status-${row.po_number}`;
  }
  if (col.key === "deadline" || col.key === "deadline_ship" || col.key === "work_date" || col.key === "po" || col.key === "sj") {
    extras.push("mono");
  }
  if (col.key === "deadline" || col.key === "deadline_ship" || col.key === "work_date" || col.key === "helper" || col.key === "driver") {
    extras.push("small");
  }
  if (col.key === "outlet") extras.push("muted", "small");
  return { classExtra: extras.filter(Boolean).join(" "), testids };
}

export default function ProductionScheduleSheetCells({
  row,
  variant,
  qtyProg,
  cetakProg,
  isToday,
  poHref,
  opsBadge,
  columns,
}) {
  const ctx = { row, variant, qtyProg, cetakProg, isToday, poHref, opsBadge };
  return (Array.isArray(columns) && columns.length ? columns : PRODUCTION_SCHEDULE_TABLE_COLS).map((col) => {
    const { classExtra, testids } = cellExtras(col, ctx);
    const todayStyle = col.key === "customer" && variant !== "mobile" && isToday
      ? { backgroundColor: "#fef08a", color: "#713f12" }
      : undefined;
    const itemTitle = col.key === "item"
      ? operatorPrintableItems(row.displayOperator?.splits || []).map((item) => item.name).filter(Boolean).join(" · ")
      : undefined;
    const title = col.key === "qty" ? qtyProg.title : col.key === "qty_cetak" ? cetakProg.title : itemTitle || undefined;
    return (
      <td
        key={col.key}
        className={productionSheetCellClassName(col, classExtra)}
        style={todayStyle}
        title={title}
        {...testids}
      >
        {cellContent(col, ctx)}
      </td>
    );
  });
}
