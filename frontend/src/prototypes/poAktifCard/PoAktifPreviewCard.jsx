import { useEffect, useId, useRef, useState } from "react";
import { ArrowUpRight, Layers, MoreHorizontal, X } from "lucide-react";
import {
  PO_AKTIF_CAPTION,
  PO_AKTIF_DRILL_PATH,
  PO_AKTIF_INFO,
  PO_AKTIF_META,
  formatPoCount,
} from "./poAktifPreviewShared";

/**
 * Preview-only PO Aktif card. Does not replace the operational widget.
 */
export default function PoAktifPreviewCard({
  variantId,
  title = "PO Aktif",
  count = 0,
  status = "complete", // complete | zero | loading | error | forbidden
  size = "compact", // compact 3x2 | large 4x3
  drillLocked = false,
  editing = false,
  showExtraHint = false,
  onOpenRincian,
  onRetry,
  onRemove,
  onNudge,
}) {
  const [menuOpen, setMenuOpen] = useState(false);
  const [infoOpen, setInfoOpen] = useState(false);
  const menuBtnRef = useRef(null);
  const infoCloseRef = useRef(null);
  const infoTitleId = useId();
  const canDrill = !editing && !drillLocked && (status === "complete" || status === "zero");
  const showCount = status === "complete" || status === "zero";

  useEffect(() => {
    if (!menuOpen && !infoOpen) return undefined;
    const onKey = (e) => {
      if (e.key !== "Escape") return;
      e.preventDefault();
      if (infoOpen) {
        setInfoOpen(false);
        window.setTimeout(() => menuBtnRef.current?.focus(), 0);
        return;
      }
      if (menuOpen) {
        setMenuOpen(false);
        window.setTimeout(() => menuBtnRef.current?.focus(), 0);
      }
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [menuOpen, infoOpen]);

  useEffect(() => {
    if (infoOpen) {
      window.setTimeout(() => infoCloseRef.current?.focus(), 0);
    }
  }, [infoOpen]);

  const openRincian = () => {
    if (!canDrill) return;
    onOpenRincian?.(PO_AKTIF_DRILL_PATH, variantId);
  };

  const stopBubble = (e) => {
    e.stopPropagation();
  };

  return (
    <article
      className={[
        "pac-card",
        size === "large" ? "is-large" : "is-compact",
        editing ? "is-edit" : "",
        canDrill ? "is-drillable" : "",
        drillLocked && showCount ? "is-drill-locked" : "",
        `is-${status}`,
      ].filter(Boolean).join(" ")}
      data-testid={`pac-card-${variantId}`}
      data-status={status}
      data-drill={canDrill ? "ready" : (drillLocked && showCount ? "locked" : undefined)}
    >
      <header className="pac-card-head" onClick={stopBubble} onKeyDown={stopBubble}>
        <span className="pac-glyph" aria-hidden="true">
          <Layers size={16} strokeWidth={2} />
        </span>
        <div className="pac-card-title">
          <h2>{title}</h2>
        </div>
        <div className="pac-card-tools">
          <button
            type="button"
            className="pac-icon-btn"
            ref={menuBtnRef}
            aria-label={`Menu ${title}`}
            aria-expanded={menuOpen}
            data-testid={`pac-menu-${variantId}`}
            onClick={() => setMenuOpen((v) => !v)}
          >
            <MoreHorizontal size={16} aria-hidden="true" />
          </button>
          {menuOpen ? (
            <div className="pac-pop" role="menu" data-testid={`pac-menu-panel-${variantId}`}>
              <button
                type="button"
                role="menuitem"
                data-testid={`pac-info-${variantId}`}
                onClick={() => {
                  setInfoOpen(true);
                  setMenuOpen(false);
                }}
              >
                Info Widget
              </button>
              {onRemove ? (
                <button
                  type="button"
                  role="menuitem"
                  data-testid={`pac-remove-${variantId}`}
                  onClick={() => {
                    setMenuOpen(false);
                    onRemove(variantId);
                  }}
                >
                  Hapus dari Dashboard
                </button>
              ) : null}
              {editing && onNudge ? (
                <div className="pac-nudge" role="group" aria-label={`Papan ketik ${title}`}>
                  <button type="button" onClick={() => onNudge(variantId, -1, 0)}>Kiri</button>
                  <button type="button" onClick={() => onNudge(variantId, 1, 0)}>Kanan</button>
                  <button type="button" onClick={() => onNudge(variantId, 0, -1)}>Atas</button>
                  <button type="button" onClick={() => onNudge(variantId, 0, 1)}>Bawah</button>
                </div>
              ) : null}
            </div>
          ) : null}
        </div>
      </header>

      <div
        className="pac-body"
        role={canDrill ? "link" : undefined}
        tabIndex={canDrill ? 0 : undefined}
        aria-label={canDrill ? `Buka daftar ${title}` : undefined}
        data-testid={`pac-body-${variantId}`}
        onClick={openRincian}
        onKeyDown={(e) => {
          if (!canDrill) return;
          if (e.key === "Enter" || e.key === " ") {
            e.preventDefault();
            openRincian();
          }
        }}
      >
        {status === "loading" ? (
          <div className="pac-skel" aria-busy="true" aria-label="Memuat" data-testid={`pac-loading-${variantId}`}>
            <span />
            <span />
            <span />
          </div>
        ) : null}

        {status === "error" ? (
          <div className="pac-quiet" data-testid={`pac-error-${variantId}`}>
            <strong>Gagal memuat</strong>
            <p>Muat ulang untuk mencoba lagi.</p>
            {onRetry ? (
              <button
                type="button"
                className="pac-btn"
                data-testid={`pac-retry-${variantId}`}
                onClick={(e) => {
                  stopBubble(e);
                  onRetry(variantId);
                }}
              >
                Coba lagi
              </button>
            ) : null}
          </div>
        ) : null}

        {status === "forbidden" ? (
          <div className="pac-quiet" data-testid={`pac-forbidden-${variantId}`}>
            <strong>Tidak ada izin</strong>
            <p>Kartu ini tidak termasuk akses Anda.</p>
          </div>
        ) : null}

        {showCount ? (
          <div className="pac-kpi">
            <div className="pac-kpi-main">
              <p className="pac-metric" data-testid={`pac-metric-${variantId}`}>
                <span className="pac-metric-num">{formatPoCount(count)}</span>
                <span className="pac-metric-unit"> PO</span>
              </p>
              <p className="pac-caption">
                {status === "zero" ? "Tidak ada PO aktif" : PO_AKTIF_CAPTION}
              </p>
              <p className="pac-meta">
                {showExtraHint ? "PO unik · Snapshot saat ini" : PO_AKTIF_META}
              </p>
            </div>
            {drillLocked ? (
              <p className="pac-foot pac-locked" data-testid={`pac-locked-${variantId}`}>
                Akses rincian terbatas
              </p>
            ) : null}
            {canDrill ? (
              <button
                type="button"
                className="pac-foot pac-drill"
                data-testid={`pac-drill-${variantId}`}
                onClick={(e) => {
                  stopBubble(e);
                  openRincian();
                }}
              >
                Lihat rincian
                <ArrowUpRight size={13} aria-hidden="true" />
              </button>
            ) : null}
          </div>
        ) : null}
      </div>

      {infoOpen ? (
        <div
          className="pac-info-backdrop"
          data-testid={`pac-info-backdrop-${variantId}`}
          onClick={() => {
            setInfoOpen(false);
            window.setTimeout(() => menuBtnRef.current?.focus(), 0);
          }}
        >
          <div
            className="pac-info-panel"
            role="dialog"
            aria-modal="true"
            aria-labelledby={infoTitleId}
            data-testid={`pac-info-panel-${variantId}`}
            onClick={stopBubble}
          >
            <div className="pac-info-head">
              <h3 id={infoTitleId}>Info Widget</h3>
              <button
                type="button"
                className="pac-icon-btn"
                ref={infoCloseRef}
                aria-label="Tutup info"
                data-testid={`pac-info-close-${variantId}`}
                onClick={() => {
                  setInfoOpen(false);
                  window.setTimeout(() => menuBtnRef.current?.focus(), 0);
                }}
              >
                <X size={16} />
              </button>
            </div>
            <p>{PO_AKTIF_INFO}</p>
          </div>
        </div>
      ) : null}
    </article>
  );
}
