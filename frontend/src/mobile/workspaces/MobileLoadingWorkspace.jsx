import { useState } from "react";
import api from "../../lib/api";
import { formatApiError } from "../../lib/errors";
import { newActionId } from "../../lib/workflowEntries";
import { loadingPoLines, sjFilesForLoading, validateLoadingDraftLines } from "../../lib/loadingConfirm";
import { SJ_VIEW_FILE_PERM, suratJalanFileUrl } from "../../lib/suratJalan";
import { loadingAvailability, mobileWorkStatus } from "../../lib/mobileWorkspaces";
import { useAuth } from "../../lib/auth";
import MobileMetricStrip from "../components/MobileMetricStrip";
import MobileQtyStepper from "../components/MobileQtyStepper";
import MobileStatusBadge from "../components/MobileStatusBadge";

export default function MobileLoadingWorkspace({ order, canEdit, onUpdated }) {
  const { hasPerm } = useAuth();
  const sjFiles = sjFilesForLoading(order);
  const poLines = loadingPoLines(order, sjFiles[0]?.id);
  const avail = loadingAvailability(order, poLines, sjFiles);
  const status = mobileWorkStatus(avail.loaded, avail.packed);
  const [drafts, setDrafts] = useState({});
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  const canViewSj = hasPerm(SJ_VIEW_FILE_PERM);

  const saveLine = async (line) => {
    const qty = drafts[line.line_idx];
    const check = validateLoadingDraftLines([{ line_idx: line.line_idx, qty }], poLines);
    if (!check.ok) {
      setErr(check.error);
      return;
    }
    if (!sjFiles[0]?.id) {
      setErr("Surat Jalan belum tersedia.");
      return;
    }
    setBusy(true);
    setErr("");
    try {
      const res = await api.confirmLoading(order.id, {
        surat_jalan_id: sjFiles[0].id,
        lines: check.lines,
        action_id: newActionId(),
      });
      onUpdated(res.order);
      setDrafts((d) => ({ ...d, [line.line_idx]: "" }));
    } catch (e) {
      setErr(formatApiError(e, "Gagal menyimpan loading."));
    } finally {
      setBusy(false);
    }
  };

  return (
    <section data-testid="mobile-ws-loading">
      <div className="mop-ws-header"><h2>LOADING</h2></div>
      <MobileStatusBadge status={status} />
      <MobileMetricStrip items={[
        { label: "Packed Qty", value: avail.packed.toLocaleString("id-ID") },
        { label: "Loaded Qty", value: avail.loaded.toLocaleString("id-ID") },
        { label: "Remaining Qty", value: avail.remaining.toLocaleString("id-ID") },
      ]} />
      {avail.blocked && <div className="mop-error">{avail.reason}</div>}
      {err && <div className="mop-error">{err}</div>}
      {sjFiles[0] && canViewSj && (
        <a className="mop-btn-ghost" style={{ display: "flex", alignItems: "center", justifyContent: "center", marginBottom: 10 }} href={suratJalanFileUrl(order.id, sjFiles[0].id)} target="_blank" rel="noreferrer">
          Buka Surat Jalan
        </a>
      )}
      {poLines.map((line) => (
        <div className="mop-line-block" key={line.line_idx} data-testid={`mobile-load-line-${line.line_idx}`}>
          <strong>{line.name}</strong>
          <p className="mop-card-meta">
            <span>Line {line.line_idx + 1}</span>
            <span>Sisa {Number(line.available || 0).toLocaleString("id-ID")}</span>
          </p>
          <MobileQtyStepper
            id={`load-${line.line_idx}`}
            label="Qty"
            value={drafts[line.line_idx] || ""}
            onChange={(v) => setDrafts((d) => ({ ...d, [line.line_idx]: v }))}
            disabled={!canEdit || busy || avail.blocked}
            max={line.available}
          />
          <button type="button" className="mop-btn-primary" disabled={!canEdit || busy || avail.blocked} onClick={() => saveLine(line)}>
            Simpan Loading
          </button>
        </div>
      ))}
    </section>
  );
}
