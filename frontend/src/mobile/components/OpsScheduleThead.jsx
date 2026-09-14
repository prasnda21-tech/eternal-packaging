import { productionSheetColumnStyle } from "../../lib/productionScheduleTable";
import { mobileDeliverySheetColumnStyle } from "../../lib/mobileDeliverySchedule";

function colStyle(col, mode) {
  return mode === "delivery" ? mobileDeliverySheetColumnStyle(col) : productionSheetColumnStyle(col);
}

/** Two-row operational header: identity columns + Status per Role group. */
export default function OpsScheduleThead({
  cols = [],
  statusKeys = [],
  testid,
  mode = "production",
  renderSort,
}) {
  const statusSet = new Set(statusKeys);
  const lead = cols.filter((c) => !statusSet.has(c.key));
  const status = cols.filter((c) => statusSet.has(c.key));
  return (
    <thead className="production-sheet-header mop-ops-thead" data-testid={testid}>
      <tr>
        {lead.map((col) => (
          <th
            key={col.key}
            scope="col"
            rowSpan={status.length ? 2 : 1}
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
      </tr>
      {status.length ? (
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
        </tr>
      ) : null}
    </thead>
  );
}
