import { productionSheetColumnStyle } from "../../lib/productionScheduleTable";
import { mobileDeliverySheetColumnStyle } from "../../lib/mobileDeliverySchedule";

function colStyle(col, mode) {
  return mode === "delivery" ? mobileDeliverySheetColumnStyle(col) : productionSheetColumnStyle(col);
}

/** Two-row operational header: identity columns + optional Status per Role / Notifikasi groups. */
export default function OpsScheduleThead({
  cols = [],
  statusKeys = [],
  notifKeys = [],
  notifLabel = "Notifikasi",
  testid,
  mode = "production",
  renderSort,
}) {
  const statusSet = new Set(statusKeys);
  const notifSet = new Set(notifKeys);
  const lead = cols.filter((c) => !statusSet.has(c.key) && !notifSet.has(c.key));
  const status = cols.filter((c) => statusSet.has(c.key));
  const notif = cols.filter((c) => notifSet.has(c.key));
  const hasSub = status.length + notif.length > 0;
  return (
    <thead className="production-sheet-header mop-ops-thead" data-testid={testid}>
      <tr>
        {lead.map((col) => (
          <th
            key={col.key}
            scope="col"
            rowSpan={hasSub ? 2 : 1}
            className={col.num || col.align === "right" || col.type === "qty" ? "num" : ""}
            style={colStyle(col, mode)}
          >
            {renderSort ? renderSort(col) : col.label}
          </th>
        ))}
        {status.length ? (
          <th
            className="mop-ops-status-group"
            colSpan={status.length}
            scope="colgroup"
          >
            Status per Role
          </th>
        ) : null}
        {notif.length ? (
          <th
            className="mop-ops-status-group mop-ops-notif-group"
            colSpan={notif.length}
            scope="colgroup"
            data-testid={mode === "delivery" ? "mds-notif-group" : undefined}
          >
            {notifLabel}
          </th>
        ) : null}
      </tr>
      {hasSub ? (
        <tr>
          {status.map((col) => (
            <th
              key={col.key}
              scope="col"
              className={`mop-ops-status-sub${col.num ? " num" : ""}`}
              style={colStyle(col, mode)}
            >
              {renderSort ? renderSort(col) : col.label}
            </th>
          ))}
          {notif.map((col) => (
            <th
              key={col.key}
              scope="col"
              className={`mop-ops-status-sub mop-ops-notif-sub${col.num ? " num" : ""}`}
              style={colStyle(col, mode)}
            >
              {renderSort ? renderSort(col) : col.label}
            </th>
          ))}
        </tr>
      ) : null}
    </thead>
  );
}
