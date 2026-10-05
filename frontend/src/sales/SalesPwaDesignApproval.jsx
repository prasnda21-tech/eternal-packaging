import { useState } from "react";
import { approvalActionPath } from "../lib/customerTracking";

export function salesDesignApprovalPath(row) {
  return approvalActionPath({ status: row?.status, path: row?.path });
}

export function absoluteDesignApprovalUrl(path, origin) {
  const clean = salesDesignApprovalPath({ status: "active", path });
  const base = String(origin ?? (typeof window !== "undefined" ? window.location.origin : "")).replace(/\/$/, "");
  if (!clean || !base || base === "null") return "";
  return `${base}${clean}`;
}

async function writeClipboard(text) {
  if (!text) return false;
  try {
    if (navigator.clipboard?.writeText) {
      await navigator.clipboard.writeText(text);
      return true;
    }
  } catch {
    /* fall through to the textarea path */
  }
  try {
    const area = document.createElement("textarea");
    area.value = text;
    area.setAttribute("readonly", "");
    area.style.position = "fixed";
    area.style.left = "-9999px";
    document.body.appendChild(area);
    area.select();
    const ok = document.execCommand("copy");
    area.remove();
    return Boolean(ok);
  } catch {
    return false;
  }
}

function CopyApprovalLink({ path, testId }) {
  const [note, setNote] = useState("");
  const [failed, setFailed] = useState(false);
  const onCopy = async () => {
    const url = absoluteDesignApprovalUrl(path);
    const ok = await writeClipboard(url);
    setFailed(!ok);
    setNote(ok ? "Link disalin" : "Gagal menyalin link");
  };
  return (
    <>
      <button type="button" className="spwa-approval-copy" data-testid={testId} onClick={onCopy}>
        Salin Link
      </button>
      {note ? (
        <small className={`spwa-approval-feedback${failed ? " is-error" : ""}`} role="status" data-testid={`${testId}-status`}>
          {note}
        </small>
      ) : null}
    </>
  );
}

export function designApprovalIdentity(row) {
  const design = String(row?.design_name || "").trim();
  const items = Array.isArray(row?.items)
    ? row.items.map((item) => String(item?.label || "").trim()).filter(Boolean)
    : [];
  return [design, ...items].filter(Boolean).join(" · ");
}

export function orderExpectsDesignLink(order) {
  if (order?.design_need?.code) return true;
  return String(order?.status || "").trim() === "Approval Design";
}

export default function SalesPwaDesignApproval({ order, expectMissing = false }) {
  const rows = Array.isArray(order?.design_approvals) ? order.design_approvals : [];
  if (!rows.length) {
    if (!expectMissing) return null;
    return (
      <p className="spwa-approval-missing" data-testid={`sales-approval-missing-${order?.id || "order"}`}>
        Link approval belum tersedia
      </p>
    );
  }
  return (
    <div className="spwa-approval-list" data-testid={`sales-approval-list-${order?.id || "order"}`}>
      {rows.map((row, index) => {
        const path = salesDesignApprovalPath(row);
        const identity = designApprovalIdentity(row);
        const key = String(row?.id || identity || index);
        return (
          <div key={key} className="spwa-approval-row" data-testid={`sales-approval-row-${key}`}>
            {identity ? <small className="spwa-approval-identity">{identity}</small> : null}
            {row?.status_label ? <small className="spwa-approval-status">{row.status_label}</small> : null}
            {path ? (
              <div className="spwa-approval-actions">
                <a
                  href={path}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="spwa-approval-open"
                  data-testid={`sales-approval-open-${key}`}
                >
                  Buka Approval Desain
                </a>
                <CopyApprovalLink path={path} testId={`sales-approval-copy-${key}`} />
              </div>
            ) : (
              <p className="spwa-approval-missing" data-testid={`sales-approval-missing-${key}`}>
                Link approval belum tersedia
              </p>
            )}
          </div>
        );
      })}
    </div>
  );
}
