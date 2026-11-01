import { useEffect, useRef, useState } from "react";

/**
 * SignaturePad — canvas-based signature capture.
 * - Touch + mouse pointer events.
 * - Optional `value` (PNG/JPEG data URL) restores a draft after the canvas is sized.
 * - `onRestoreState('ready'|'failed'|null)` reports draft paint outcome (null = no value).
 * - `onChange(dataUrl|null)` after stroke end, clear, or failed restore (null).
 */
export default function SignaturePad({
  height = 180,
  disabled = false,
  onChange,
  value = null,
  onRestoreState,
}) {
  const canvasRef = useRef(null);
  const drawingRef = useRef(false);
  const lastRef = useRef({ x: 0, y: 0 });
  const dirtyRef = useRef(false);
  const onChangeRef = useRef(onChange);
  const onRestoreStateRef = useRef(onRestoreState);
  const [empty, setEmpty] = useState(true);
  const [restoreFailed, setRestoreFailed] = useState(false);

  useEffect(() => {
    onChangeRef.current = onChange;
  }, [onChange]);

  useEffect(() => {
    onRestoreStateRef.current = onRestoreState;
  }, [onRestoreState]);

  // Size canvas for DPR, then paint optional draft `value` once ready.
  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return undefined;

    let cancelled = false;
    const dpr = window.devicePixelRatio || 1;
    const rect = canvas.getBoundingClientRect();
    const cssW = Math.max(1, Math.floor(rect.width));
    const cssH = Math.max(1, Math.floor(rect.height || height));
    canvas.width = Math.max(1, Math.floor(cssW * dpr));
    canvas.height = Math.max(1, Math.floor(cssH * dpr));
    const ctx = canvas.getContext("2d");
    ctx.setTransform(1, 0, 0, 1, 0, 0);
    ctx.scale(dpr, dpr);
    ctx.lineWidth = 2.2;
    ctx.lineCap = "round";
    ctx.lineJoin = "round";
    ctx.strokeStyle = "#111";
    ctx.clearRect(0, 0, cssW, cssH);

    dirtyRef.current = false;
    drawingRef.current = false;

    const want = typeof value === "string" && value.startsWith("data:image/") ? value : null;
    if (!want) {
      setEmpty(true);
      setRestoreFailed(false);
      if (onRestoreStateRef.current) onRestoreStateRef.current(null);
      return () => { cancelled = true; };
    }

    setRestoreFailed(false);
    if (onRestoreStateRef.current) onRestoreStateRef.current("restoring");

    const img = new Image();
    img.onload = () => {
      if (cancelled) return;
      try {
        ctx.setTransform(1, 0, 0, 1, 0, 0);
        ctx.scale(dpr, dpr);
        ctx.clearRect(0, 0, cssW, cssH);
        ctx.drawImage(img, 0, 0, cssW, cssH);
        ctx.lineWidth = 2.2;
        ctx.lineCap = "round";
        ctx.lineJoin = "round";
        ctx.strokeStyle = "#111";
        dirtyRef.current = true;
        setEmpty(false);
        setRestoreFailed(false);
        if (onRestoreStateRef.current) onRestoreStateRef.current("ready");
      } catch {
        if (cancelled) return;
        dirtyRef.current = false;
        setEmpty(true);
        setRestoreFailed(true);
        if (onRestoreStateRef.current) onRestoreStateRef.current("failed");
        if (onChangeRef.current) onChangeRef.current(null);
      }
    };
    img.onerror = () => {
      if (cancelled) return;
      dirtyRef.current = false;
      setEmpty(true);
      setRestoreFailed(true);
      if (onRestoreStateRef.current) onRestoreStateRef.current("failed");
      if (onChangeRef.current) onChangeRef.current(null);
    };
    img.src = want;

    return () => {
      cancelled = true;
      img.onload = null;
      img.onerror = null;
    };
  }, [value, height]);

  const pos = (e) => {
    const rect = canvasRef.current.getBoundingClientRect();
    const clientX = e.clientX ?? e.touches?.[0]?.clientX ?? 0;
    const clientY = e.clientY ?? e.touches?.[0]?.clientY ?? 0;
    return { x: clientX - rect.left, y: clientY - rect.top };
  };

  const start = (e) => {
    if (disabled) return;
    e.preventDefault();
    drawingRef.current = true;
    lastRef.current = pos(e);
  };

  const move = (e) => {
    if (!drawingRef.current) return;
    e.preventDefault();
    const p = pos(e);
    const ctx = canvasRef.current.getContext("2d");
    ctx.beginPath();
    ctx.moveTo(lastRef.current.x, lastRef.current.y);
    ctx.lineTo(p.x, p.y);
    ctx.stroke();
    lastRef.current = p;
    dirtyRef.current = true;
    if (empty) setEmpty(false);
    if (restoreFailed) setRestoreFailed(false);
  };

  const getDataUrl = () => {
    if (!canvasRef.current) return null;
    return canvasRef.current.toDataURL("image/png");
  };

  const end = () => {
    if (!drawingRef.current) return;
    drawingRef.current = false;
    if (dirtyRef.current && onChangeRef.current) {
      onChangeRef.current(getDataUrl());
    }
  };

  const clear = () => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const ctx = canvas.getContext("2d");
    ctx.clearRect(0, 0, canvas.width, canvas.height);
    // Re-apply stroke style after clear in device pixels — keep CSS-space transform.
    const dpr = window.devicePixelRatio || 1;
    const rect = canvas.getBoundingClientRect();
    ctx.setTransform(1, 0, 0, 1, 0, 0);
    ctx.scale(dpr, dpr);
    ctx.lineWidth = 2.2;
    ctx.lineCap = "round";
    ctx.lineJoin = "round";
    ctx.strokeStyle = "#111";
    ctx.clearRect(0, 0, Math.max(1, Math.floor(rect.width)), Math.max(1, Math.floor(rect.height || height)));
    dirtyRef.current = false;
    setEmpty(true);
    setRestoreFailed(false);
    if (onRestoreStateRef.current) onRestoreStateRef.current(null);
    if (onChangeRef.current) onChangeRef.current(null);
  };

  return (
    <div
      className="signature-pad"
      data-testid="signature-pad"
      data-restore={restoreFailed ? "failed" : (empty ? (value ? "pending" : "empty") : "ready")}
    >
      <div
        style={{
          border: "1px dashed #b7b1a4",
          borderRadius: 10,
          background: "#fdfaf3",
          position: "relative",
          height,
        }}
      >
        <canvas
          ref={canvasRef}
          style={{ width: "100%", height: "100%", touchAction: "none", display: "block", cursor: disabled ? "not-allowed" : "crosshair" }}
          onPointerDown={start}
          onPointerMove={move}
          onPointerUp={end}
          onPointerLeave={end}
          onPointerCancel={end}
          data-testid="signature-canvas"
        />
        {empty && (
          <div
            aria-hidden
            style={{
              position: "absolute",
              inset: 0,
              display: "flex",
              alignItems: "center",
              justifyContent: "center",
              color: "#a19881",
              fontSize: 13,
              pointerEvents: "none",
              userSelect: "none",
            }}
            data-testid="signature-empty-hint"
          >
            Tanda tangan di sini
          </div>
        )}
      </div>
      {restoreFailed ? (
        <p className="error small" data-testid="signature-restore-failed" style={{ margin: "6px 0 0" }}>
          Gagal memulihkan draft tanda tangan. Gambar ulang sebelum konfirmasi.
        </p>
      ) : null}
      <div style={{ marginTop: 6, display: "flex", justifyContent: "flex-end" }}>
        <button
          type="button"
          className="ghost sm"
          onClick={clear}
          disabled={disabled || empty}
          data-testid="signature-clear-button"
        >
          Hapus tanda tangan
        </button>
      </div>
    </div>
  );
}
