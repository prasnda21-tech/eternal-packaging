import { useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { useNavigate, useParams } from "react-router-dom";
import { ChevronDown, ClipboardPlus, Plus, Save, Trash2 } from "lucide-react";
import api from "../lib/api";
import { useAuth } from "../lib/auth";
import { PRIORITY_LABELS, TYPE_LABELS, formatSpkError, newIdempotencyKey, weightTotal } from "../lib/spk";
import "./spk.css";

const PIC_LIMIT = 12;
const EMPTY_PICS = [];

function uniqueIds(ids) {
  const seen = [];
  (ids || []).forEach((id) => {
    if (!seen.includes(id)) seen.push(id);
  });
  return seen;
}

function picInTargetDivision(pic, division) {
  if (!division || !Array.isArray(pic?.divisions)) return false;
  return pic.divisions.some((item) => String(item) === String(division));
}

function assignedIdsForDivision(ids, catalog, division) {
  const allowed = new Set(
    (catalog || []).filter((pic) => picInTargetDivision(pic, division)).map((pic) => pic.id),
  );
  return uniqueIds(ids).filter((id) => allowed.has(id));
}

function PicMultiSelect({ pics, catalog, selectedIds, onToggle, division, disabled }) {
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const [box, setBox] = useState(null);
  const rootRef = useRef(null);
  const panelRef = useRef(null);
  const triggerRef = useRef(null);
  const names = useMemo(() => {
    const map = new Map();
    (catalog || []).forEach((pic) => map.set(pic.id, pic.name || ""));
    (pics || []).forEach((pic) => map.set(pic.id, pic.name || ""));
    return map;
  }, [catalog, pics]);
  const summary = selectedIds.length === 0
    ? "Pilih PIC"
    : selectedIds.length === 1
      ? (names.get(selectedIds[0]) || "Pilih PIC")
      : `${selectedIds.length} PIC dipilih`;
  const visible = useMemo(() => {
    const needle = query.trim().toLowerCase();
    if (!needle) return pics;
    return pics.filter((pic) => String(pic.name || "").toLowerCase().includes(needle));
  }, [pics, query]);
  const atMax = selectedIds.length >= PIC_LIMIT;

  useLayoutEffect(() => {
    if (!open) {
      setBox(null);
      return undefined;
    }
    const update = () => {
      const trigger = triggerRef.current;
      if (!trigger) return;
      const rect = trigger.getBoundingClientRect();
      const margin = 8;
      const width = Math.min(rect.width, window.innerWidth - margin * 2);
      let left = rect.left;
      if (left + width > window.innerWidth - margin) left = window.innerWidth - width - margin;
      if (left < margin) left = margin;
      const maxPanel = 300;
      let top = rect.bottom + 4;
      if (top + maxPanel > window.innerHeight - margin) {
        top = Math.max(margin, window.innerHeight - margin - maxPanel);
      }
      setBox({ top, left, width });
    };
    update();
    const onPointer = (event) => {
      const target = event.target;
      if (rootRef.current?.contains(target) || panelRef.current?.contains(target)) return;
      setOpen(false);
    };
    const onKey = (event) => {
      if (event.key !== "Escape") return;
      setOpen(false);
      triggerRef.current?.focus();
    };
    const onScroll = (event) => {
      if (panelRef.current?.contains(event.target)) return;
      setOpen(false);
    };
    const onResize = () => setOpen(false);
    document.addEventListener("mousedown", onPointer);
    document.addEventListener("keydown", onKey);
    window.addEventListener("resize", onResize);
    window.addEventListener("scroll", onScroll, true);
    return () => {
      document.removeEventListener("mousedown", onPointer);
      document.removeEventListener("keydown", onKey);
      window.removeEventListener("resize", onResize);
      window.removeEventListener("scroll", onScroll, true);
    };
  }, [open]);

  useEffect(() => {
    setOpen(false);
    setQuery("");
  }, [division]);

  useEffect(() => {
    if (!open) setQuery("");
  }, [open]);

  const panel = open && box ? (
    <div
      id="spk-pic-panel"
      ref={panelRef}
      className="spk-pic-panel"
      role="group"
      aria-labelledby="spk-pic-label"
      data-testid="spk-pic-panel"
      style={{ top: box.top, left: box.left, width: box.width }}
    >
      <input
        type="search"
        className="spk-pic-search"
        placeholder="Cari PIC"
        aria-label="Cari PIC"
        data-testid="spk-pic-search"
        value={query}
        onChange={(event) => setQuery(event.target.value)}
      />
      <div className="spk-pic-list">
        {visible.map((pic) => {
          const checked = selectedIds.includes(pic.id);
          const disabled = !checked && atMax;
          return (
            <label
              key={pic.id}
              className={disabled ? "spk-pic-option is-disabled" : "spk-pic-option"}
              data-testid="spk-pic-option"
            >
              <input
                type="checkbox"
                checked={checked}
                disabled={disabled}
                onChange={() => onToggle(pic.id)}
              />
              {pic.name}
            </label>
          );
        })}
        {visible.length === 0 && (
          <div className="spk-empty" data-testid="spk-pic-empty">
            {query.trim() || pics.length > 0 ? "PIC tidak ditemukan" : "Tidak ada PIC yang dapat menerima tugas."}
          </div>
        )}
      </div>
      {atMax && <div className="spk-pic-max" data-testid="spk-pic-max">Maksimal 12 PIC</div>}
    </div>
  ) : null;

  return (
    <div className="spk-pic-field" ref={rootRef} id="spk-pic" data-testid="spk-pic">
      <button
        type="button"
        id="spk-pic-trigger"
        ref={triggerRef}
        className="spk-pic-trigger"
        data-testid="spk-pic-trigger"
        aria-haspopup="dialog"
        aria-expanded={open}
        aria-controls="spk-pic-panel"
        disabled={disabled}
        onClick={() => { if (!disabled) setOpen((value) => !value); }}
      >
        <span data-testid="spk-pic-summary">{summary}</span>
        <ChevronDown size={14} aria-hidden="true" />
      </button>
      {selectedIds.length > 0 && (
        <div className="spk-pic-chips" data-testid="spk-pic-chips">
          {selectedIds.map((id) => {
            const name = names.get(id) || "";
            if (!name) return null;
            return (
              <span key={id} className="spk-pic-chip" data-testid="spk-pic-chip">
                <span>{name}</span>
                <button type="button" aria-label={`Hapus ${name}`} onClick={() => onToggle(id)}>×</button>
              </span>
            );
          })}
        </div>
      )}
      {disabled && <div className="spk-empty" data-testid="spk-pic-need-division">Pilih divisi tujuan terlebih dahulu</div>}
      {panel && createPortal(panel, document.body)}
    </div>
  );
}

function blankMilestone(weight = "100") {
  return {
    key: `${Date.now()}-${Math.random().toString(36).slice(2, 7)}`,
    id: "",
    title: "",
    type: "QUANTITY",
    weight,
    target_value: "",
    unit: "",
    requires_evidence: true,
  };
}

function milestonePayload(rows) {
  return rows.map((row) => ({
    id: row.id || undefined,
    title: row.title,
    type: row.type,
    weight: row.weight,
    target_value: row.type === "QUANTITY" ? row.target_value : 1,
    unit: row.type === "QUANTITY" ? row.unit : "",
    requires_evidence: !!row.requires_evidence,
  }));
}

export default function SpkCreate({ mode } = {}) {
  const navigate = useNavigate();
  const params = useParams();
  const editId = mode === "edit" ? params.id : "";
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
  const [attachments, setAttachments] = useState([]);
  const [docVersion, setDocVersion] = useState(null);
  const [status, setStatus] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [confirmPublish, setConfirmPublish] = useState(false);
  const lock = useRef(false);
  const keySlot = useRef(null);

  useEffect(() => {
    if (!canCreate) navigate("/spk", { replace: true });
  }, [canCreate, navigate]);

  useEffect(() => {
    api.spkContext().then((data) => setContext(data.context || null)).catch((err) => setError(formatSpkError(err)));
  }, []);

  useEffect(() => {
    if (!editId) return undefined;
    let cancelled = false;
    api.getSpk(editId).then((data) => {
      if (cancelled) return;
      const order = data.order;
      if (!order || (order.workflow_status !== "DRAFT" && order.workflow_status !== "RETURNED")) {
        navigate(`/spk/${editId}`, { replace: true });
        return;
      }
      setTitle(order.title || "");
      setPriority(order.priority || "NORMAL");
      setTargetDivision(order.target_division || "penjualan");
      setPicIds(order.assigned_user_ids || []);
      setStartDate(order.start_date || "");
      setDeadline(order.deadline || "");
      setInstruction(order.instruction || "");
      setDocVersion(order.doc_version);
      setStatus(order.workflow_status);
      setAttachments(order.attachments || []);
      const rows = (order.milestones || []).map((row) => ({
        key: row.id,
        id: row.id,
        title: row.title || "",
        type: row.type || "QUANTITY",
        weight: row.weight || "",
        target_value: row.target_value || "",
        unit: row.unit || "",
        requires_evidence: !!row.requires_evidence,
      }));
      setMilestones(rows.length ? rows : [blankMilestone("100")]);
    }).catch((err) => { if (!cancelled) setError(formatSpkError(err)); });
    return () => { cancelled = true; };
  }, [editId, navigate]);

  const picCatalog = context?.pics || EMPTY_PICS;
  const pics = useMemo(() => {
    const divisionPics = picCatalog.filter((pic) => picInTargetDivision(pic, targetDivision));
    return [...divisionPics].sort((a, b) => String(a.name || "").localeCompare(String(b.name || "")));
  }, [picCatalog, targetDivision]);

  const total = weightTotal(milestones);
  const weightOk = total === 100;

  const patchMilestone = (key, patch) => {
    setMilestones((rows) => rows.map((row) => (row.key === key ? { ...row, ...patch } : row)));
  };

  const changeDivision = (value) => {
    setTargetDivision(value);
    if (value !== targetDivision) setPicIds([]);
  };

  const togglePic = (id) => {
    setPicIds((current) => {
      const unique = uniqueIds(current);
      if (unique.includes(id)) return unique.filter((item) => item !== id);
      if (!pics.some((pic) => pic.id === id)) return unique;
      if (unique.length >= PIC_LIMIT) return unique;
      return [...unique, id];
    });
  };

  const submit = async (publish) => {
    if (lock.current) return;
    const selected = uniqueIds(picIds);
    const assignedUserIds = assignedIdsForDivision(selected, picCatalog, targetDivision);
    if (selected.length !== assignedUserIds.length) {
      setPicIds(assignedUserIds);
      setError("PIC tidak sesuai divisi tujuan.");
      return;
    }
    if (assignedUserIds.length > PIC_LIMIT) {
      setError("Maksimal 12 PIC");
      return;
    }
    lock.current = true;
    setBusy(true);
    setError("");
    try {
      const fields = {
        title,
        priority,
        target_division: targetDivision,
        assigned_user_ids: assignedUserIds,
        start_date: startDate,
        deadline,
        instruction,
        milestones: milestonePayload(milestones),
      };
      const serial = JSON.stringify({ ...fields, publish, editId });
      if (!keySlot.current || keySlot.current.serial !== serial) {
        keySlot.current = { serial, key: newIdempotencyKey() };
      }
      let data;
      if (editId) {
        data = await api.updateSpkDraft(editId, { ...fields, doc_version: docVersion });
        if (publish) {
          data = await api.publishSpk(editId, { doc_version: data.order.doc_version, idempotency_key: keySlot.current.key });
        }
      } else {
        data = await api.createSpk({ ...fields, publish, idempotency_key: keySlot.current.key });
      }
      const published = publish || data.order?.workflow_status === "PUBLISHED";
      navigate(`/spk/${data.order.id}`, {
        state: { notice: published ? (status === "RETURNED" ? "SPK diterbitkan ulang." : "SPK diterbitkan.") : "Draft tersimpan." },
      });
    } catch (err) {
      setError(formatSpkError(err));
    } finally {
      lock.current = false;
      setBusy(false);
      setConfirmPublish(false);
    }
  };

  return (
    <div className="spk-page spk-create-page" data-testid="spk-create-page">
      <header className="spk-head">
        <div>
          <div className="spk-kicker">{editId ? "Ubah SPK" : "Buat SPK"}</div>
          <h1>{editId ? (status === "RETURNED" ? "Perbaiki SPK" : "Edit Draft") : "Buat SPK"}</h1>
        </div>
        <div className="spk-actions">
          <button type="button" className="spk-btn spk-btn-draft" data-testid="spk-save-draft" disabled={busy || !title} onClick={() => submit(false)}>
            <Save size={14} /> Simpan Draft
          </button>
          <button type="button" className="spk-btn spk-btn-create" data-testid="spk-publish" disabled={busy || !weightOk} onClick={() => { if (!lock.current) setConfirmPublish(true); }}>
            <ClipboardPlus size={14} /> {status === "RETURNED" ? "Terbitkan ulang" : "Terbitkan SPK"}
          </button>
        </div>
      </header>
      {error && <div className="spk-msg is-error" data-testid="spk-create-error">{error}</div>}
      {confirmPublish && (
        <form className="spk-msg" data-testid="spk-publish-confirm" onSubmit={(e) => { e.preventDefault(); submit(true); }}>
          <p>{status === "RETURNED" ? "Terbitkan ulang memakai dokumen dan nomor SPK yang sama." : "Terbitkan SPK ini sekarang?"}</p>
          <button type="submit" className="spk-btn spk-btn-create" data-testid="spk-publish-confirm-submit" disabled={busy}>Ya, terbitkan</button>
          <button type="button" className="spk-btn spk-btn-ghost" disabled={busy} onClick={() => setConfirmPublish(false)}>Batal</button>
        </form>
      )}
      <div className="spk-create-scroll">
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
          <select id="spk-division" data-testid="spk-target-division" value={targetDivision} onChange={(e) => changeDivision(e.target.value)}>
            {(context?.divisions || [{ id: "penjualan", label: "Penjualan" }]).map((item) => (
              <option key={item.id} value={item.id}>{item.label}</option>
            ))}
          </select>
        </div>
        <div className="spk-field">
          <label id="spk-pic-label" className="spk-section-label" htmlFor="spk-pic-trigger">PIC (maksimal 12)</label>
          <PicMultiSelect
            pics={pics}
            catalog={picCatalog}
            selectedIds={picIds}
            onToggle={togglePic}
            division={targetDivision}
            disabled={!targetDivision}
          />
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
      {attachments.length > 0 && (
        <div data-testid="spk-existing-files">
          Lampiran tersimpan: {attachments.map((file) => file.name || file.label).filter(Boolean).join(", ")}
        </div>
      )}
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
    </div>
  );
}
