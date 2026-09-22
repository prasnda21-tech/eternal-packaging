import { useEffect, useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import { ClipboardPlus, Plus, Save, Trash2 } from "lucide-react";
import api from "../lib/api";
import { useAuth } from "../lib/auth";
import { PRIORITY_LABELS, TYPE_LABELS, formatSpkError, weightTotal } from "../lib/spk";
import "./spk.css";

function blankMilestone(weight = "100") {
  return {
    key: `${Date.now()}-${Math.random().toString(36).slice(2, 7)}`,
    title: "",
    type: "QUANTITY",
    weight,
    target_value: "",
    unit: "",
    requires_evidence: true,
  };
}

export default function SpkCreate() {
  const navigate = useNavigate();
  const { hasPerm } = useAuth();
  const canCreate = hasPerm("spk.create") || hasPerm("spk.manage");
  const [context, setContext] = useState(null);
  const [title, setTitle] = useState("");
  const [priority, setPriority] = useState("NORMAL");
  const [targetDivision, setTargetDivision] = useState("penjualan");
  const [picIds, setPicIds] = useState([]);
  const [startDate, setStartDate] = useState("");
  const [deadline, setDeadline] = useState("");
  const [instruction, setInstruction] = useState("");
  const [milestones, setMilestones] = useState([blankMilestone("100")]);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (!canCreate) navigate("/spk", { replace: true });
  }, [canCreate, navigate]);

  useEffect(() => {
    api.spkContext().then((data) => setContext(data.context || null)).catch((err) => setError(formatSpkError(err)));
  }, []);

  const pics = useMemo(() => {
    const all = context?.pics || [];
    const matched = all.filter((pic) => (pic.divisions || []).includes(targetDivision));
    return matched.length ? matched : all;
  }, [context, targetDivision]);

  const total = weightTotal(milestones);
  const weightOk = total === 100;

  const patchMilestone = (key, patch) => {
    setMilestones((rows) => rows.map((row) => (row.key === key ? { ...row, ...patch } : row)));
  };

  const submit = async (publish) => {
    setError("");
    setBusy(true);
    try {
      const payload = {
        title,
        priority,
        target_division: targetDivision,
        assigned_user_ids: picIds,
        start_date: startDate,
        deadline,
        instruction,
        publish,
        milestones: milestones.map((row) => ({
          title: row.title,
          type: row.type,
          weight: row.weight,
          target_value: row.type === "QUANTITY" ? row.target_value : 1,
          unit: row.type === "QUANTITY" ? row.unit : "",
          requires_evidence: !!row.requires_evidence,
        })),
      };
      const data = await api.createSpk(payload);
      navigate(`/spk/${data.order.id}`);
    } catch (err) {
      setError(formatSpkError(err));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="spk-page" data-testid="spk-create-page">
      <header className="spk-head">
        <div>
          <div className="spk-kicker">Buat SPK</div>
          <h1>Buat SPK</h1>
        </div>
        <div className="spk-actions">
          <button type="button" className="spk-btn spk-btn-draft" data-testid="spk-save-draft" disabled={busy || !title} onClick={() => submit(false)}>
            <Save size={14} /> Simpan Draft
          </button>
          <button type="button" className="spk-btn spk-btn-create" data-testid="spk-publish" disabled={busy || !weightOk} onClick={() => submit(true)}>
            <ClipboardPlus size={14} /> Terbitkan SPK
          </button>
        </div>
      </header>
      {error && <div className="spk-msg is-error" data-testid="spk-create-error">{error}</div>}
      <div className="spk-row spk-row-2">
        <div className="spk-field">
          <label htmlFor="spk-title">Judul</label>
          <input id="spk-title" data-testid="spk-title" value={title} onChange={(e) => setTitle(e.target.value)} />
        </div>
        <div className="spk-field">
          <label htmlFor="spk-priority">Prioritas</label>
          <select id="spk-priority" data-testid="spk-priority" value={priority} onChange={(e) => setPriority(e.target.value)}>
            {Object.entries(PRIORITY_LABELS).map(([key, label]) => <option key={key} value={key}>{label}</option>)}
          </select>
        </div>
      </div>
      <div className="spk-row spk-row-3">
        <div className="spk-field">
          <label htmlFor="spk-division">Divisi Tujuan</label>
          <select id="spk-division" data-testid="spk-target-division" value={targetDivision} onChange={(e) => { setTargetDivision(e.target.value); setPicIds([]); }}>
            {(context?.divisions || [{ id: "penjualan", label: "Penjualan" }]).map((item) => (
              <option key={item.id} value={item.id}>{item.label}</option>
            ))}
          </select>
        </div>
        <div className="spk-field">
          <label htmlFor="spk-pic">PIC</label>
          <select
            id="spk-pic"
            data-testid="spk-pic"
            value={picIds[0] || ""}
            onChange={(e) => setPicIds(e.target.value ? [e.target.value] : [])}
          >
            <option value="">Pilih PIC</option>
            {pics.map((pic) => <option key={pic.id} value={pic.id}>{pic.name}</option>)}
          </select>
        </div>
        <div className="spk-field">
          <label>Cabang</label>
          <div className="spk-readonly" data-testid="spk-branch">{context?.branch_name || "—"}</div>
        </div>
      </div>
      <div className="spk-row spk-row-2">
        <div className="spk-field">
          <label htmlFor="spk-start">Tanggal Mulai</label>
          <input id="spk-start" data-testid="spk-start" type="date" value={startDate} onChange={(e) => setStartDate(e.target.value)} />
        </div>
        <div className="spk-field">
          <label htmlFor="spk-deadline">Deadline</label>
          <input id="spk-deadline" data-testid="spk-deadline" type="date" value={deadline} onChange={(e) => setDeadline(e.target.value)} />
        </div>
      </div>
      <div className="spk-field">
        <label htmlFor="spk-instruction">Instruksi</label>
        <textarea id="spk-instruction" data-testid="spk-instruction" value={instruction} onChange={(e) => setInstruction(e.target.value)} />
      </div>
      <div className="spk-section-label" style={{ display: "flex", justifyContent: "space-between" }}>
        <span>Milestone</span>
        <span className={weightOk ? "spk-weight-ok" : "spk-weight-bad"} data-testid="spk-weight-total">Total bobot {total} / 100</span>
      </div>
      <div className="spk-milestone-scroll">
        <table className="spk-table spk-ms-table" data-testid="spk-milestone-builder">
          <thead>
            <tr>
              <th>Target</th>
              <th>Tipe</th>
              <th>Bobot</th>
              <th>Target Nilai</th>
              <th>Unit</th>
              <th>Bukti Wajib</th>
              <th>Aksi</th>
            </tr>
          </thead>
          <tbody>
            {milestones.map((row) => (
              <tr key={row.key} data-testid="spk-milestone-row">
                <td><input aria-label="Target" data-testid="spk-ms-title" value={row.title} onChange={(e) => patchMilestone(row.key, { title: e.target.value })} /></td>
                <td>
                  <select aria-label="Tipe" data-testid="spk-ms-type" value={row.type} onChange={(e) => patchMilestone(row.key, { type: e.target.value })}>
                    {Object.entries(TYPE_LABELS).map(([key, label]) => <option key={key} value={key}>{label}</option>)}
                  </select>
                </td>
                <td><input aria-label="Bobot" data-testid="spk-ms-weight" value={row.weight} onChange={(e) => patchMilestone(row.key, { weight: e.target.value })} /></td>
                <td>
                  <input
                    aria-label="Target Nilai"
                    data-testid="spk-ms-target"
                    disabled={row.type !== "QUANTITY"}
                    value={row.type === "QUANTITY" ? row.target_value : "1"}
                    onChange={(e) => patchMilestone(row.key, { target_value: e.target.value })}
                  />
                </td>
                <td>
                  <input
                    aria-label="Unit"
                    data-testid="spk-ms-unit"
                    disabled={row.type !== "QUANTITY"}
                    value={row.unit}
                    onChange={(e) => patchMilestone(row.key, { unit: e.target.value })}
                  />
                </td>
                <td className="spk-check">
                  <input
                    type="checkbox"
                    aria-label="Bukti wajib"
                    data-testid="spk-ms-evidence"
                    checked={!!row.requires_evidence}
                    onChange={(e) => patchMilestone(row.key, { requires_evidence: e.target.checked })}
                  />
                </td>
                <td>
                  <button
                    type="button"
                    className="spk-btn spk-btn-ghost"
                    aria-label="Hapus milestone"
                    disabled={milestones.length < 2}
                    onClick={() => setMilestones((rows) => rows.filter((item) => item.key !== row.key))}
                  >
                    <Trash2 size={14} /> Hapus
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <div>
        <button
          type="button"
          className="spk-btn spk-btn-progress"
          data-testid="spk-add-milestone"
          onClick={() => setMilestones((rows) => [...rows, blankMilestone("")])}
        >
          <Plus size={14} /> Tambah Milestone
        </button>
      </div>
    </div>
  );
}
