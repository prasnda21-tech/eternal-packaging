import { useCallback, useMemo, useState } from "react";
import PoAktifPreviewCard from "./PoAktifPreviewCard";
import { PO_AKTIF_DRILL_PATH } from "./poAktifPreviewShared";
import "./PoAktifCardPreview.css";

/**
 * Dev-only visual preview for PO Aktif card concepts.
 * Route: /dev/po-aktif-card-preview
 * Fixture only — does not replace operational Production Dashboard widget.
 */

const INITIAL_VISIBLE = {
  normal: true,
  large: true,
  zero: true,
  loading: true,
  error: true,
  forbidden: true,
  locked: true,
  long: true,
  editable: true,
};

export default function PoAktifCardPreview() {
  const [editing, setEditing] = useState(false);
  const [visible, setVisible] = useState(INITIAL_VISIBLE);
  const [offsets, setOffsets] = useState({});
  const [simPath, setSimPath] = useState("");
  const [simFrom, setSimFrom] = useState("");
  const [errorRecovered, setErrorRecovered] = useState(false);

  const resetPreview = useCallback(() => {
    setVisible({ ...INITIAL_VISIBLE });
    setOffsets({});
    setEditing(false);
    setSimPath("");
    setSimFrom("");
    setErrorRecovered(false);
  }, []);

  const openRincian = useCallback((path, variantId) => {
    setSimPath(path);
    setSimFrom(variantId);
  }, []);

  const removeCard = useCallback((variantId) => {
    setVisible((prev) => ({ ...prev, [variantId]: false }));
  }, []);

  const nudge = useCallback((variantId, dx, dy) => {
    setOffsets((prev) => {
      const cur = prev[variantId] || { x: 0, y: 0 };
      return {
        ...prev,
        [variantId]: { x: cur.x + dx, y: cur.y + dy },
      };
    });
  }, []);

  const slots = useMemo(() => ([
    {
      id: "normal",
      label: "Normal 3×2 · 128 PO",
      w: 3,
      props: { count: 128, status: "complete", size: "compact" },
    },
    {
      id: "large",
      label: "Diperbesar 4×3",
      w: 4,
      props: {
        count: 128,
        status: "complete",
        size: "large",
        showExtraHint: true,
      },
    },
    {
      id: "zero",
      label: "Nol",
      w: 3,
      props: { count: 0, status: "zero", size: "compact" },
    },
    {
      id: "loading",
      label: "Loading",
      w: 3,
      props: { status: "loading", size: "compact" },
    },
    {
      id: "error",
      label: "Error",
      w: 3,
      props: {
        status: errorRecovered ? "complete" : "error",
        count: errorRecovered ? 128 : 0,
        size: "compact",
      },
    },
    {
      id: "forbidden",
      label: "Tanpa izin widget",
      w: 3,
      props: { status: "forbidden", size: "compact" },
    },
    {
      id: "locked",
      label: "KPI terlihat · rincian terkunci",
      w: 3,
      props: {
        count: 128,
        status: "complete",
        size: "compact",
        drillLocked: true,
      },
    },
    {
      id: "long",
      label: "Angka panjang 12.345 PO",
      w: 3,
      props: { count: 12345, status: "complete", size: "compact" },
    },
    {
      id: "editable",
      label: "Instance edit lokal",
      w: 3,
      props: {
        count: 128,
        status: "complete",
        size: "compact",
      },
    },
  ]), [errorRecovered]);

  return (
    <div className="pac-root" data-testid="pac-root">
      <p className="pac-banner">
        Preview visual · Data contoh · Bukan widget operasional Dashboard Produksi
      </p>

      <div className="pac-head">
        <div>
          <h1>Preview Kartu PO Aktif</h1>
          <p>
            Konsep visual satu widget berdasarkan audit existing. Navigasi rincian
            hanya disimulasikan di panel di bawah — tidak membuka data operasional.
          </p>
        </div>
        <div className="pac-toolbar">
          <button
            type="button"
            className={`pac-btn${editing ? " is-primary" : ""}`}
            data-testid="pac-edit-toggle"
            onClick={() => setEditing((v) => !v)}
          >
            {editing ? "Selesai edit" : "Mode edit lokal"}
          </button>
          <button
            type="button"
            className="pac-btn"
            data-testid="pac-reset"
            onClick={resetPreview}
          >
            Reset preview
          </button>
        </div>
      </div>

      <section className="pac-sim" data-testid="pac-sim-panel" aria-live="polite">
        <h2>Simulasi navigasi rincian</h2>
        {simPath ? (
          <>
            <div className="pac-sim-empty">
              Dipicu dari kartu <strong>{simFrom}</strong> (body atau Lihat rincian).
            </div>
            <code data-testid="pac-sim-path">{simPath}</code>
          </>
        ) : (
          <div className="pac-sim-empty">
            Belum ada navigasi. Target existing bila dibuka:
            {" "}
            <code>{PO_AKTIF_DRILL_PATH}</code>
          </div>
        )}
      </section>

      <div className="pac-grid" data-testid="pac-grid">
        {slots.map((slot) => {
          if (!visible[slot.id]) return null;
          const offset = offsets[slot.id] || { x: 0, y: 0 };
          return (
            <div
              key={slot.id}
              className={`pac-slot is-w${slot.w}`}
              style={offset.x || offset.y ? {
                transform: `translate(${offset.x * 12}px, ${offset.y * 12}px)`,
              } : undefined}
              data-testid={`pac-slot-${slot.id}`}
            >
              <p className="pac-slot-label">{slot.label}</p>
              <PoAktifPreviewCard
                variantId={slot.id}
                {...slot.props}
                editing={editing}
                onOpenRincian={openRincian}
                onRemove={removeCard}
                onNudge={nudge}
                onRetry={slot.id === "error" ? () => setErrorRecovered(true) : undefined}
              />
            </div>
          );
        })}
      </div>

      <p className="pac-note">
        Soft-delete dan field Sisa Qty/Deadline rincian tidak disentuh di batch visual ini.
        Widget operasional Production Dashboard dan prototype gabungan tidak diubah lewat halaman ini.
      </p>
    </div>
  );
}
