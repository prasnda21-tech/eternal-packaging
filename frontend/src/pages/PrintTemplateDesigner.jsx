import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import api from "../lib/api";
import { useAuth } from "../lib/auth";
import InAppDocumentViewer from "../components/InAppDocumentViewer";
import {
  PX_PER_MM,
  elementOutOfBand,
  fitZoom,
  innerWidthMm,
  isTypingTarget,
  moveElement,
  paperPixelSize,
  pointerDeltaToMm,
  resizeElement,
  roundMm,
  snapMm,
} from "../lib/printTemplateGeometry";
import "./printTemplateDesigner.css";

const BANDS = [
  ["title", "Title"],
  ["page_header", "Page Header"],
  ["detail", "Detail"],
  ["summary", "Summary"],
  ["page_footer", "Page Footer"],
];

function clone(value) {
  return JSON.parse(JSON.stringify(value));
}

function findElement(design, id) {
  for (const [band] of BANDS) {
    const found = (design.bands?.[band]?.elements || []).find((el) => el.id === id);
    if (found) return { band, element: found };
  }
  return null;
}

export default function PrintTemplateDesigner() {
  const { templateId } = useParams();
  const navigate = useNavigate();
  const { hasPerm, user } = useAuth();
  const canManage = user?.role === "admin" || user?.is_super_admin || hasPerm("print_template.manage");
  const [template, setTemplate] = useState(null);
  const [design, setDesign] = useState(null);
  const [revision, setRevision] = useState(0);
  const [catalog, setCatalog] = useState([]);
  const [selectedId, setSelectedId] = useState("");
  const [zoom, setZoom] = useState(1);
  const [tab, setTab] = useState("canvas");
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");
  const [serverErrors, setServerErrors] = useState([]);
  const [busy, setBusy] = useState("");
  const [previewKey, setPreviewKey] = useState("");
  const [previewError, setPreviewError] = useState("");
  const history = useRef({ past: [], future: [] });
  const [, setTick] = useState(0);
  const drag = useRef(null);
  const previewSeq = useRef(0);
  const canvasRef = useRef(null);
  const designRef = useRef(null);
  designRef.current = design;

  const load = useCallback(async () => {
    const res = await api.getPrintTemplate(templateId);
    const row = res.template;
    setTemplate(row);
    setDesign(clone(row.design));
    setRevision(row.revision);
    history.current = { past: [], future: [] };
    const cat = await api.printTemplateCatalog(row.document_type);
    setCatalog(cat.fields || []);
  }, [templateId]);

  useEffect(() => {
    load().catch((err) => setError(err.response?.data?.detail || "Desain tidak dapat dibuka."));
  }, [load]);

  const commit = useCallback((next) => {
    const current = designRef.current;
    if (!current) return;
    history.current.past = [...history.current.past, clone(current)].slice(-50);
    history.current.future = [];
    setDesign(clone(next));
    setTick((n) => n + 1);
  }, []);

  const undo = useCallback(() => {
    const past = history.current.past;
    if (!past.length || !designRef.current) return;
    history.current.future = [clone(designRef.current), ...history.current.future];
    setDesign(past.pop());
    setTick((n) => n + 1);
  }, []);
  const redo = useCallback(() => {
    const future = history.current.future;
    if (!future.length || !designRef.current) return;
    history.current.past.push(clone(designRef.current));
    setDesign(future.shift());
    setTick((n) => n + 1);
  }, []);

  const selected = design && selectedId ? findElement(design, selectedId) : null;
  const paperSize = paperPixelSize(design || { paper: {} }, zoom);
  const scale = paperSize.scale;
  const inner = design ? innerWidthMm(design) : 180;

  const localErrors = useMemo(() => {
    if (!design) return [];
    const rows = [];
    for (const [band, label] of BANDS) {
      const height = Number(design.bands?.[band]?.height_mm || 0);
      for (const el of design.bands?.[band]?.elements || []) {
        if (elementOutOfBand(el, height, inner)) {
          rows.push({ element_id: el.id, message: `Elemen berada di luar band ${label}.` });
        }
      }
    }
    return rows;
  }, [design, inner]);
  const errorIds = new Set([...localErrors, ...serverErrors].map((row) => row.element_id).filter(Boolean));

  useEffect(() => {
    if (!design || !template) return undefined;
    const timer = window.setTimeout(() => {
      api.validatePrintTemplate(template.document_type, design)
        .then((res) => setServerErrors(res.errors || []))
        .catch(() => {});
    }, 400);
    return () => window.clearTimeout(timer);
  }, [design, template]);

  useEffect(() => {
    function onKey(event) {
      if (isTypingTarget(event.target)) return;
      if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "z") {
        event.preventDefault();
        if (event.shiftKey) redo();
        else undo();
        return;
      }
      if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "y") {
        event.preventDefault();
        redo();
        return;
      }
      if (!selected) return;
      const snap = design.grid?.snap !== false;
      const step = snap ? Number(design.grid?.snap_mm || 1) : 1;
      if (event.key === "Delete" || event.key === "Backspace") {
        event.preventDefault();
        removeSelected();
      } else if (["ArrowLeft", "ArrowRight", "ArrowUp", "ArrowDown"].includes(event.key)) {
        event.preventDefault();
        const dx = event.key === "ArrowLeft" ? -step : event.key === "ArrowRight" ? step : 0;
        const dy = event.key === "ArrowUp" ? -step : event.key === "ArrowDown" ? step : 0;
        const bandHeight = Number(design.bands[selected.band].height_mm);
        const next = moveElement(selected.element, dx, dy, bandHeight, inner, step, snap);
        updateElement(selected.element.id, next, true);
      }
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  });

  function updateElement(id, next, recordHistory) {
    const base = designRef.current;
    const copy = clone(base);
    for (const [band] of BANDS) {
      const list = copy.bands[band].elements;
      const index = list.findIndex((el) => el.id === id);
      if (index >= 0) {
        list[index] = next;
        break;
      }
    }
    if (recordHistory) commit(copy);
    else {
      designRef.current = copy;
      setDesign(copy);
    }
  }

  function removeSelected() {
    if (!selected || selected.element.required || selected.element.type === "table") {
      setError("Field wajib dan tabel rincian tidak dapat dihapus.");
      return;
    }
    const copy = clone(designRef.current);
    copy.bands[selected.band].elements = copy.bands[selected.band].elements.filter((el) => el.id !== selected.element.id);
    setSelectedId("");
    commit(copy);
  }

  function addElement(partial) {
    const copy = clone(designRef.current);
    const band = partial.band || "page_header";
    const el = {
      id: `el_${Math.random().toString(16).slice(2, 10)}`,
      type: partial.type,
      band,
      x_mm: partial.x_mm ?? 4,
      y_mm: partial.y_mm ?? 4,
      w_mm: partial.w_mm ?? 40,
      h_mm: partial.h_mm ?? 8,
      z: 5,
      required: false,
      props: partial.props || {},
    };
    copy.bands[band].elements.push(el);
    setSelectedId(el.id);
    setTab("props");
    commit(copy);
  }

  function onPointerDown(event, element, mode) {
    event.stopPropagation();
    setSelectedId(element.id);
    drag.current = {
      id: element.id,
      mode,
      x: event.clientX,
      y: event.clientY,
      origin: clone(element),
      snapshot: clone(designRef.current),
    };
    event.currentTarget.setPointerCapture?.(event.pointerId);
  }

  function onPointerMove(event) {
    const state = drag.current;
    if (!state || !designRef.current) return;
    const found = findElement(designRef.current, state.id);
    if (!found) return;
    const delta = pointerDeltaToMm(event.clientX - state.x, event.clientY - state.y, zoom, PX_PER_MM);
    const bandHeight = Number(designRef.current.bands[found.band].height_mm);
    const snap = designRef.current.grid?.snap !== false;
    const step = Number(designRef.current.grid?.snap_mm || 1);
    const next = state.mode === "resize"
      ? resizeElement(state.origin, delta.dx, delta.dy, bandHeight, inner, step, snap)
      : moveElement(state.origin, delta.dx, delta.dy, bandHeight, inner, step, snap);
    next.band = found.band;
    updateElement(state.id, next, false);
  }

  function onPointerUp() {
    if (!drag.current) return;
    history.current.past = [...history.current.past, drag.current.snapshot].slice(-50);
    history.current.future = [];
    drag.current = null;
    setTick((n) => n + 1);
  }

  async function saveDraft() {
    setBusy("save");
    setError("");
    try {
      const res = await api.savePrintTemplateDraft(template.id, designRef.current, revision);
      setRevision(res.template.revision);
      setTemplate(res.template);
      setServerErrors(res.template.errors || []);
      setMessage("Draf tersimpan. Cetakan aktif tidak berubah.");
    } catch (err) {
      const detail = err.response?.data?.detail;
      if (err.response?.status === 409) {
        setError(detail?.message || "Desain ini sudah diubah di tempat lain. Isi editor Anda tetap dipertahankan.");
      } else {
        setError(typeof detail === "string" ? detail : detail?.message || "Draf gagal disimpan.");
      }
    } finally {
      setBusy("");
    }
  }

  async function activate() {
    setBusy("activate");
    setError("");
    try {
      const saved = await api.savePrintTemplateDraft(template.id, designRef.current, revision);
      setRevision(saved.template.revision);
      const res = await api.activatePrintTemplate(template.id, saved.template.revision);
      setTemplate(res.template);
      setRevision(res.template.revision);
      setMessage("Template aktif. Cetakan berikutnya memakai desain ini.");
    } catch (err) {
      const detail = err.response?.data?.detail;
      setServerErrors(detail?.errors || []);
      setError(typeof detail === "string" ? detail : detail?.message || "Aktivasi ditolak.");
    } finally {
      setBusy("");
    }
  }

  async function preview() {
    setPreviewError("");
    const seq = ++previewSeq.current;
    setBusy("preview");
    try {
      const blob = await api.previewPrintTemplate(template.id, designRef.current);
      if (seq !== previewSeq.current) return;
      setPreviewKey(`${template.id}-${seq}`);
      previewBlob.current = blob;
    } catch (err) {
      if (seq !== previewSeq.current) return;
      const detail = err.detail || err.response?.data?.detail;
      setPreviewError(typeof detail === "string" ? detail : detail?.message || "Pratinjau gagal.");
      setServerErrors(detail?.errors || serverErrors);
    } finally {
      if (seq === previewSeq.current) setBusy("");
    }
  }
  const previewBlob = useRef(null);

  function fit() {
    const node = canvasRef.current;
    if (!node || !design) return;
    const paper = design.paper || {};
    const width = paper.orientation === "landscape" ? paper.height_mm : paper.width_mm;
    setZoom(fitZoom(node.clientWidth, width));
  }

  if (!design || !template) {
    return <div className="print-designer-page"><p className="pt-note">{error || "Memuat desainer…"}</p></div>;
  }

  const props = selected?.element?.props || {};
  return (
    <div className="print-designer-page" data-testid="print-designer-page">
      <div className="pt-toolbar" data-testid="print-designer-toolbar">
        <button type="button" onClick={() => navigate("/settings/desain-cetakan")}>Kembali</button>
        <button type="button" data-testid="print-undo" onClick={undo} disabled={!history.current.past.length}>Undo</button>
        <button type="button" data-testid="print-redo" onClick={redo} disabled={!history.current.future.length}>Redo</button>
        <button type="button" onClick={() => setZoom((z) => roundMm(Math.max(0.4, z - 0.1)))}>-</button>
        <span className="pt-zoom" data-testid="print-zoom">{Math.round(zoom * 100)}%</span>
        <button type="button" onClick={() => setZoom((z) => roundMm(Math.min(2.5, z + 0.1)))}>+</button>
        <button type="button" onClick={fit}>Fit to width</button>
        <button type="button" data-testid="print-preview" onClick={preview} disabled={busy === "preview"}>Pratinjau PDF</button>
        <button type="button" className="pt-btn" data-testid="print-save-draft" onClick={saveDraft} disabled={!canManage || busy === "save"}>Simpan Draf</button>
        <button type="button" className="pt-btn good" data-testid="print-activate" onClick={activate} disabled={!canManage || template.is_builtin || busy === "activate"}>Aktifkan</button>
        <label><input type="checkbox" checked={design.grid?.enabled !== false} onChange={(e) => commit({ ...design, grid: { ...design.grid, enabled: e.target.checked } })} /> Grid</label>
        <label><input type="checkbox" checked={design.grid?.snap !== false} onChange={(e) => commit({ ...design, grid: { ...design.grid, snap: e.target.checked } })} /> Snap</label>
      </div>
      <div className="pt-tabs">
        {[["elements", "Elemen"], ["canvas", "Kanvas"], ["props", "Properti"]].map(([id, label]) => (
          <button type="button" key={id} className={tab === id ? "is-on" : ""} onClick={() => setTab(id)}>{label}</button>
        ))}
      </div>
      {template.is_builtin && <p className="pt-note">Ini desain bawaan. Simpan draf tidak mengubah cetakan. Salin template untuk mengaktifkan desain kustom.</p>}
      {message && <p className="pt-note" data-testid="print-designer-message">{message}</p>}
      {(error || previewError) && <p className="pt-error" data-testid="print-designer-error">{error || previewError}</p>}
      <div className="pt-body" data-tab={tab}>
        <aside className="pt-side" data-testid="print-element-panel">
          <strong>Elemen</strong>
          <button type="button" className="pt-add-btn" onClick={() => addElement({ type: "text", props: { text: "Label", font: "Helvetica", font_size: 9, bold: true } })}>Label / teks</button>
          <button type="button" className="pt-add-btn" onClick={() => addElement({ type: "line", h_mm: 2, w_mm: 80, props: { color: "#0284c7", stroke: 1 } })}>Garis</button>
          <button type="button" className="pt-add-btn" onClick={() => addElement({ type: "box", w_mm: 40, h_mm: 16, props: { border_color: "#cbd5e1" } })}>Kotak</button>
          <button type="button" className="pt-add-btn" onClick={() => addElement({ type: "qr", w_mm: 22, h_mm: 22, props: { source: "tracking" } })}>QR tracking</button>
          <button type="button" className="pt-add-btn" onClick={() => addElement({ type: "signature", w_mm: 50, h_mm: 24, props: { label: "Hormat kami", name: "", title: "" } })}>Tanda tangan</button>
          <label className="pt-add-btn">Logo
            <input type="file" accept="image/png,image/jpeg" onChange={async (event) => {
              const file = event.target.files?.[0];
              if (!file) return;
              try {
                const res = await api.uploadPrintTemplateAsset(file);
                addElement({ type: "image", w_mm: 28, h_mm: 16, props: { role: "logo", asset_id: res.asset.id } });
              } catch (err) {
                setError(err.response?.data?.detail || "Logo gagal diunggah.");
              }
            }} />
          </label>
          <strong>Field</strong>
          {catalog.map((field) => (
            <button
              type="button"
              key={field.key}
              className="pt-field-btn"
              disabled={!field.enabled || field.kind === "line"}
              title={field.reason || ""}
              onClick={() => addElement({ type: "field", props: { field: field.key, font: "Helvetica", font_size: 8.5, number_format: field.number_format === "rupiah" ? "rupiah" : "" } })}
            >
              {field.label}{field.required ? " *" : ""}{!field.enabled ? ` — ${field.reason}` : ""}
            </button>
          ))}
          <p className="pt-note">Kolom barang diatur pada tabel di band Detail. DATA CONTOH hanya untuk kanvas.</p>
        </aside>
        <div className="pt-canvas-scroll" ref={canvasRef} data-testid="print-canvas" onPointerMove={onPointerMove} onPointerUp={onPointerUp}>
          <div className="pt-paper" style={{ width: paperSize.width, height: paperSize.height }} data-zoom={zoom}>
            {BANDS.map(([band, label]) => {
              const height = Number(design.bands[band].height_mm) * scale;
              return (
                <section key={band} className="pt-band" style={{ height }} data-band={band}>
                  <span className="pt-band-label">{label}</span>
                  {(design.bands[band].elements || []).map((el) => (
                    <div
                      key={el.id}
                      data-testid={`print-el-${el.id}`}
                      className={`pt-el${selectedId === el.id ? " is-selected" : ""}${errorIds.has(el.id) ? " is-error" : ""}`}
                      style={{ left: el.x_mm * scale, top: el.y_mm * scale, width: el.w_mm * scale, height: el.h_mm * scale, zIndex: el.z || 1 }}
                      onPointerDown={(event) => onPointerDown(event, el, "move")}
                    >
                      {el.type === "field" ? (el.props.field || "field") : el.type === "text" ? el.props.text : el.type}
                      {selectedId === el.id && (
                        <span className="pt-handle" data-testid="print-resize-handle" onPointerDown={(event) => onPointerDown(event, el, "resize")} />
                      )}
                    </div>
                  ))}
                </section>
              );
            })}
          </div>
        </div>
        <aside className="pt-props" data-testid="print-props-panel">
          <strong>Properti</strong>
          <div className="pt-grid-2">
            <label>Kertas
              <select value={design.paper.preset} onChange={(e) => {
                const preset = e.target.value;
                const sizes = { A4: [210, 297], A5: [148, 210], F4: [210, 330] };
                const next = clone(design);
                next.paper.preset = preset;
                if (sizes[preset]) {
                  next.paper.width_mm = sizes[preset][0];
                  next.paper.height_mm = sizes[preset][1];
                }
                commit(next);
              }}>
                <option value="A4">A4 (210 × 297 mm)</option>
                <option value="A5">A5 (148 × 210 mm)</option>
                <option value="F4">F4 (210 × 330 mm)</option>
                <option value="custom">Custom</option>
              </select>
            </label>
            <label>Orientasi
              <select value={design.paper.orientation} onChange={(e) => commit({ ...design, paper: { ...design.paper, orientation: e.target.value } })}>
                <option value="portrait">Portrait</option>
                <option value="landscape">Landscape</option>
              </select>
            </label>
          </div>
          {design.paper.preset === "custom" && (
            <div className="pt-grid-2">
              <label>Lebar mm<input type="number" value={design.paper.width_mm} onChange={(e) => commit({ ...design, paper: { ...design.paper, width_mm: Number(e.target.value) } })} /></label>
              <label>Tinggi mm<input type="number" value={design.paper.height_mm} onChange={(e) => commit({ ...design, paper: { ...design.paper, height_mm: Number(e.target.value) } })} /></label>
            </div>
          )}
          <p className="pt-note">F4 adalah 210 × 330 mm. Zoom tidak mengubah ukuran cetak.</p>
          {!selected && <p>Pilih elemen pada kanvas. DATA CONTOH ditampilkan saat pratinjau.</p>}
          {selected && (
            <>
              <label>Band
                <select value={selected.band} onChange={(e) => {
                  const band = e.target.value;
                  if (selected.element.type === "table" && band !== "detail") {
                    setError("Tabel rincian hanya boleh di band Detail.");
                    return;
                  }
                  const height = Number(design.bands[band].height_mm);
                  if (selected.element.y_mm + selected.element.h_mm > height || selected.element.x_mm + selected.element.w_mm > inner) {
                    setError("Posisi baru tidak muat di band tujuan.");
                    return;
                  }
                  const copy = clone(design);
                  copy.bands[selected.band].elements = copy.bands[selected.band].elements.filter((el) => el.id !== selected.element.id);
                  copy.bands[band].elements.push({ ...selected.element, band });
                  commit(copy);
                }}>
                  {BANDS.map(([id, label]) => <option key={id} value={id}>{label}</option>)}
                </select>
              </label>
              <div className="pt-grid-2">
                {["x_mm", "y_mm", "w_mm", "h_mm"].map((key) => (
                  <label key={key}>{key.replace("_mm", "").toUpperCase()} mm
                    <input data-testid={`print-prop-${key}`} type="number" value={selected.element[key]} onChange={(e) => {
                      const next = { ...selected.element, [key]: Number(e.target.value) };
                      updateElement(selected.element.id, next, true);
                    }} />
                  </label>
                ))}
              </div>
              {(selected.element.type === "text" || selected.element.type === "field") && (
                <>
                  {selected.element.type === "text" && (
                    <label>Teks
                      <textarea value={props.text || ""} onChange={(e) => updateElement(selected.element.id, { ...selected.element, props: { ...props, text: e.target.value } }, true)} />
                    </label>
                  )}
                  <div className="pt-grid-2">
                    <label>Font
                      <select value={props.font || "Helvetica"} onChange={(e) => updateElement(selected.element.id, { ...selected.element, props: { ...props, font: e.target.value } }, true)}>
                        <option>Helvetica</option>
                        <option>Times</option>
                        <option>Courier</option>
                      </select>
                    </label>
                    <label>Ukuran
                      <input type="number" value={props.font_size || 9} onChange={(e) => updateElement(selected.element.id, { ...selected.element, props: { ...props, font_size: Number(e.target.value) } }, true)} />
                    </label>
                  </div>
                  <label><input type="checkbox" checked={!!props.bold} onChange={(e) => updateElement(selected.element.id, { ...selected.element, props: { ...props, bold: e.target.checked } }, true)} /> Bold</label>
                  <label><input type="checkbox" checked={!!props.italic} onChange={(e) => updateElement(selected.element.id, { ...selected.element, props: { ...props, italic: e.target.checked } }, true)} /> Italic</label>
                  <label>Rata
                    <select value={props.align || "left"} onChange={(e) => updateElement(selected.element.id, { ...selected.element, props: { ...props, align: e.target.value } }, true)}>
                      <option value="left">Kiri</option>
                      <option value="center">Tengah</option>
                      <option value="right">Kanan</option>
                    </select>
                  </label>
                  <label>Warna teks<input type="color" value={props.color || "#0f172a"} onChange={(e) => updateElement(selected.element.id, { ...selected.element, props: { ...props, color: e.target.value } }, true)} /></label>
                </>
              )}
              {selected.element.type === "signature" && (
                <>
                  <label>Label<input value={props.label || ""} onChange={(e) => updateElement(selected.element.id, { ...selected.element, props: { ...props, label: e.target.value } }, true)} /></label>
                  <label>Nama<input value={props.name || ""} onChange={(e) => updateElement(selected.element.id, { ...selected.element, props: { ...props, name: e.target.value } }, true)} /></label>
                  <label>Jabatan<input value={props.title || ""} onChange={(e) => updateElement(selected.element.id, { ...selected.element, props: { ...props, title: e.target.value } }, true)} /></label>
                </>
              )}
              <div className="pt-actions">
                <button type="button" onClick={() => {
                  const copyEl = { ...clone(selected.element), id: `el_${Math.random().toString(16).slice(2, 10)}`, required: false, x_mm: snapMm(selected.element.x_mm + 2, 1, true), y_mm: snapMm(selected.element.y_mm + 2, 1, true) };
                  const copy = clone(design);
                  copy.bands[selected.band].elements.push(copyEl);
                  setSelectedId(copyEl.id);
                  commit(copy);
                }}>Salin</button>
                <button type="button" onClick={removeSelected}>Hapus</button>
                <button type="button" onClick={() => updateElement(selected.element.id, { ...selected.element, z: (selected.element.z || 1) + 1 }, true)}>Depan</button>
                <button type="button" onClick={() => updateElement(selected.element.id, { ...selected.element, z: Math.max(0, (selected.element.z || 1) - 1) }, true)}>Belakang</button>
              </div>
            </>
          )}
          <strong>Tinggi band</strong>
          {BANDS.map(([band, label]) => (
            <label key={band}>{label}
              <input type="number" value={design.bands[band].height_mm} onChange={(e) => {
                const copy = clone(design);
                copy.bands[band].height_mm = Number(e.target.value);
                commit(copy);
              }} />
            </label>
          ))}
        </aside>
      </div>
      {previewKey && (
        <InAppDocumentViewer
          documentKey={previewKey}
          title="Pratinjau PDF — DATA CONTOH"
          fileName="pratinjau-desain.pdf"
          contentType="application/pdf"
          loadFile={async () => previewBlob.current}
          onClose={() => setPreviewKey("")}
          testId="print-preview-viewer"
        />
      )}
    </div>
  );
}
