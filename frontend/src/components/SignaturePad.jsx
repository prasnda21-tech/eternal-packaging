import { useEffect, useRef, useState } from "react";

/**
 * SignaturePad — canvas-based signature capture untuk customer (publik).
 * - Bekerja di touchscreen (pointer events), tablet, dan mouse desktop.
 * - Export ke PNG data URL via getDataUrl().
 * - Panggil clear() dari parent lewat ref, atau tombol internal "Hapus".
 * - `value` (data URL) opsional untuk pre-fill (biasanya kosong).
 * - Callback `onChange(dataUrl)` dipanggil setelah pengguna selesai satu stroke.
 */
export default function SignaturePad({ height = 180, disabled = false, onChange }) {
  const canvasRef = useRef(null);
  const drawingRef = useRef(false);
  const lastRef = useRef({ x: 0, y: 0 });
  const dirtyRef = useRef(false);
  const [empty, setEmpty] = useState(true);

  // Setup canvas untuk high-DPI displays.
  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const dpr = window.devicePixelRatio || 1;
    const rect = canvas.getBoundingClientRect();
    canvas.width = Math.max(1, Math.floor(rect.width * dpr));
    canvas.height = Math.max(1, Math.floor(rect.height * dpr));
    const ctx = canvas.getContext("2d");
    ctx.scale(dpr, dpr);
    ctx.lineWidth = 2.2;
    ctx.lineCap = "round";
    ctx.lineJoin = "round";
    ctx.strokeStyle = "#111";
  }, []);

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
  };

  const end = () => {
    if (!drawingRef.current) return;
    drawingRef.current = false;
    if (dirtyRef.current && onChange) {
      onChange(getDataUrl());
    }
  };

  const getDataUrl = () => {
    if (!canvasRef.current) return null;
    return canvasRef.current.toDataURL("image/png");
  };

  const clear = () => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const ctx = canvas.getContext("2d");
    ctx.clearRect(0, 0, canvas.width, canvas.height);
    dirtyRef.current = false;
    setEmpty(true);
    if (onChange) onChange(null);
  };

  return (
    <div className="signature-pad" data-testid="signature-pad">
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
          >
            Tanda tangan di sini
          </div>
        )}
      </div>
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
