import { Link } from "react-router-dom";
import { XCircle } from "lucide-react";
import { formatDate, formatDisplayDate } from "../lib/constants";
import { formatIdQty, operatorPrintableItems } from "../lib/productionScheduleQty";
import {
  PRODUCTION_SCHEDULE_TABLE_COLS,
  productionSheetCellClassName,
} from "../lib/productionScheduleTable";
import { isProductionScheduleH1, isProductionScheduleUrgent } from "../lib/productionSchedulePriority";

function DeadlineShipCell({ row, variant }) {
  if (!row.delivery_deadline_effective) {
    return <span className="muted small">Menunggu approval</span>;
  }
  const sourceTestId = variant === "desktop" ? `prod-deadline-source-${row.po_number}` : undefined;
  const statusTestId = variant === "desktop" ? `prod-deadline-status-${row.po_number}` : undefined;
  return (
    <>
      {formatDate(row.delivery_deadline_effective)}
      {" "}
      <span
        className="pill sm"
        title={row.delivery_deadline_source === "manual" ? "Manual override" : "Otomatis"}
        style={{
          background: row.delivery_deadline_source === "manual" ? "#ffe9b8" : "#e6ecf3",
          color: row.delivery_deadline_source === "manual" ? "#7a5a1e" : "#3a4b60",
        }}
        data-testid={sourceTestId}
      >
        {row.delivery_deadline_source === "manual" ? "Manual" : "Otomatis"}
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
  return items.map((item) => (
    <div
      key={item.key}
      className="prod-schedule-item-line"
      data-testid={variant === "desktop"
        ? `prod-item-line-${row.po_number}-${item.key}`
        : `mobile-schedule-item-line-${row.po_number}-${item.key}`}
    >
      {item.name}
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

function StatusCell({ row }) {
  if (row.cancelled) {
    return <span className="pill sm pill-cancel"><XCircle size={10} /> Cancelled</span>;
  }
  if (row.status_final === "HOLD") return <span className="pill sm pill-hold">HOLD</span>;
  if (row.status_final === "Terkirim") return <span className="pill sm pill-ok">Terkirim</span>;
  return <span className="pill sm">{row.status_final}</span>;
}

function cellContent(col, { row, variant, qtyProg, cetakProg, isToday, poHref }) {
  switch (col.key) {
    case "po":
      return variant === "desktop" && poHref ? (
        <Link to={poHref} data-testid={`prod-po-link-${row.po_number}`}>{row.po_number}</Link>
      ) : row.po_number;
    case "customer":
      return <strong>{row.customer || "-"}</strong>;
    case "outlet":
      return row.outlet || "-";
    case "deadline":
      return formatDate(row.deadline);
    case "deadline_ship":
      return <DeadlineShipCell row={row} variant={variant} />;
    case "work_date":
      return <WorkDateCell row={row} variant={variant} />;
    case "prio":
      return isProductionScheduleUrgent(row) ? <span className="urgent-badge">URGENT</span> : <span className="muted small">Normal</span>;
    case "buffer":
      return row.buffer_stock
        ? <span className="pill sm" style={{ background: "#e6ecf3", color: "#3a4b60" }}>Buffer</span>
        : <span className="muted small">—</span>;
    case "item":
      return <ItemCell row={row} variant={variant} />;
    case "qty":
      return <TotalQtyCell qtyProg={qtyProg} />;
    case "screen":
      return row.screen_qty ?? "-";
    case "qty_cetak":
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
    case "helper":
      return row.helper_required != null ? (
        <span className={row.helper_shortage_status === "Barang Kurang" ? "prod-helper-warn" : ""}>
          {row.helper_prepared ?? 0}/{row.helper_required} {row.helper_shortage ? `(-${row.helper_shortage})` : ""}
        </span>
      ) : <span className="muted">-</span>;
    case "packing":
      return row.packing_status === "Sudah" ? <span className="pill sm pill-ok">Sudah</span> :
        row.packing_status === "Berjalan" ? <span className="pill sm">Berjalan</span> :
        <span className="pill sm">Belum</span>;
    case "sj":
      return row.delivery_note_number || "-";
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
    extras.push(isToday ? "prod-cell-today" : "");
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
}) {
  const ctx = { row, variant, qtyProg, cetakProg, isToday, poHref };
  return PRODUCTION_SCHEDULE_TABLE_COLS.map((col) => {
    const { classExtra, testids } = cellExtras(col, ctx);
    const todayStyle = col.key === "customer" && isToday
      ? { backgroundColor: "#fef08a", color: "#713f12" }
      : undefined;
    const title = col.key === "qty" ? qtyProg.title : col.key === "qty_cetak" ? cetakProg.title : undefined;
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
