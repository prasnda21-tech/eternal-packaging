import { useCallback, useMemo, useState } from "react";
import PoPerluProduksiPreviewCard from "./PoPerluProduksiPreviewCard";
import {
  PO_PERLU_DRILL_ANY_OF,
  PO_PERLU_DRILL_PATH,
} from "./poPerluProduksiPreviewShared";
import "./PoPerluProduksiCardPreview.css";

/**
 * Dev-only visual preview for PO Perlu Produksi card.
 * Route: /dev/po-perlu-produksi-card-preview
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

export default function PoPerluProduksiCardPreview() {
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
      label: "Normal 3×2 · 36 PO",
      w: 3,
      props: { count: 36, status: "complete", size: "compact" },
    },
    {
      id: "large",
      label: "Diperbesar 4×3",
      w: 4,
      props: {
        count: 36,
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
        count: errorRecovered ? 36 : 0,
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
        count: 36,
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
        count: 36,
        status: "complete",
        size: "compact",
      },
    },
  ]), [errorRecovered]);

  return (
    <div className="ppp-root" data-testid="ppp-root">
      <p className="ppp-banner">
        Preview visual · Data contoh · Bukan widget operasional Dashboard Produksi
      </p>

      <div className="ppp-head">
        <div>
          <h1>Preview Kartu PO Perlu Produksi</h1>
          <p>
            Konsep visual berdasarkan audit. Navigasi rincian disimulasikan saja.
            Grain kartu = PO unik; daftar operasional tetap grain line.
            Mismatch field rincian (order_number / item / qty_production_remaining /
            effective_delivery_date) tidak diperbaiki di batch visual ini.
          </p>
        </div>
        <div className="ppp-toolbar">
          <button
            type="button"
            className={`ppp-btn${editing ? " is-primary" : ""}`}
            data-testid="ppp-edit-toggle"
            onClick={() => setEditing((v) => !v)}
          >
            {editing ? "Selesai edit" : "Mode edit lokal"}
          </button>
          <button
            type="button"
            className="ppp-btn"
            data-testid="ppp-reset"
            onClick={resetPreview}
          >
            Reset preview
          </button>
        </div>
      </div>

      <section className="ppp-sim" data-testid="ppp-sim-panel" aria-live="polite">
        <h2>Simulasi navigasi rincian</h2>
        <div className="ppp-sim-empty">
          Izin drill (fixture audit):{" "}
          <code>{PO_PERLU_DRILL_ANY_OF.join(" OR ")}</code>
        </div>
        {simPath ? (
          <>
            <div className="ppp-sim-empty">
              Dipicu dari kartu <strong>{simFrom}</strong> (body atau Lihat rincian).
            </div>
            <code data-testid="ppp-sim-path">{simPath}</code>
          </>
        ) : (
          <div className="ppp-sim-empty">
            Belum ada navigasi. Target existing bila dibuka:
            {" "}
            <code>{PO_PERLU_DRILL_PATH}</code>
          </div>
        )}
      </section>

      <div className="ppp-grid" data-testid="ppp-grid">
        {slots.map((slot) => {
          if (!visible[slot.id]) return null;
          const offset = offsets[slot.id] || { x: 0, y: 0 };
          return (
            <div
              key={slot.id}
              className={`ppp-slot is-w${slot.w}`}
              style={offset.x || offset.y ? {
                transform: `translate(${offset.x * 12}px, ${offset.y * 12}px)`,
              } : undefined}
              data-testid={`ppp-slot-${slot.id}`}
            >
              <p className="ppp-slot-label">{slot.label}</p>
              <PoPerluProduksiPreviewCard
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

      <p className="ppp-note">
        Soft-delete dan mapping kolom rincian tidak disentuh. Widget operasional,
        prototype PO Aktif, dan prototype gabungan tidak diubah lewat halaman ini.
      </p>
    </div>
  );
}
