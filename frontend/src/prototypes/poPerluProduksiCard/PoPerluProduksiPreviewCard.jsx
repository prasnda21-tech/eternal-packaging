import { useEffect, useId, useRef, useState } from "react";
import { ArrowUpRight, MoreHorizontal, Printer, X } from "lucide-react";
import {
  PO_PERLU_CAPTION,
  PO_PERLU_DRILL_PATH,
  PO_PERLU_INFO,
  PO_PERLU_META,
  PO_PERLU_META_LARGE,
  PO_PERLU_ZERO_CAPTION,
  formatPoCount,
} from "./poPerluProduksiPreviewShared";

/**
 * Preview-only PO Perlu Produksi card. Does not replace the operational widget.
 */
export default function PoPerluProduksiPreviewCard({
  variantId,
  title = "PO Perlu Produksi",
  count = 0,
  status = "complete",
  size = "compact",
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
    onOpenRincian?.(PO_PERLU_DRILL_PATH, variantId);
  };

  const stopBubble = (e) => {
    e.stopPropagation();
  };

  return (
    <article
      className={[
        "ppp-card",
        size === "large" ? "is-large" : "is-compact",
        editing ? "is-edit" : "",
        canDrill ? "is-drillable" : "",
        drillLocked && showCount ? "is-drill-locked" : "",
        `is-${status}`,
      ].filter(Boolean).join(" ")}
      data-testid={`ppp-card-${variantId}`}
      data-status={status}
      data-drill={canDrill ? "ready" : (drillLocked && showCount ? "locked" : undefined)}
    >
      <header className="ppp-card-head" onClick={stopBubble} onKeyDown={stopBubble}>
        <span className="ppp-glyph" aria-hidden="true">
          <Printer size={15} strokeWidth={2} />
        </span>
        <div className="ppp-card-title">
          <h2 title={title}>{title}</h2>
        </div>
        <div className="ppp-card-tools">
          <button
            type="button"
            className="ppp-icon-btn"
            ref={menuBtnRef}
            aria-label={`Menu ${title}`}
            aria-expanded={menuOpen}
            data-testid={`ppp-menu-${variantId}`}
            onClick={() => setMenuOpen((v) => !v)}
          >
            <MoreHorizontal size={16} aria-hidden="true" />
          </button>
          {menuOpen ? (
            <div className="ppp-pop" role="menu" data-testid={`ppp-menu-panel-${variantId}`}>
              <button
                type="button"
                role="menuitem"
                data-testid={`ppp-info-${variantId}`}
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
                  data-testid={`ppp-remove-${variantId}`}
                  onClick={() => {
                    setMenuOpen(false);
                    onRemove(variantId);
                  }}
                >
                  Hapus dari preview
                </button>
              ) : null}
              {editing && onNudge ? (
                <div className="ppp-nudge" role="group" aria-label={`Papan ketik ${title}`}>
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
        className="ppp-body"
        role={canDrill ? "link" : undefined}
        tabIndex={canDrill ? 0 : undefined}
        aria-label={canDrill ? `Buka daftar ${title}` : undefined}
        data-testid={`ppp-body-${variantId}`}
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
          <div className="ppp-skel" aria-busy="true" aria-label="Memuat" data-testid={`ppp-loading-${variantId}`}>
            <span />
            <span />
            <span />
          </div>
        ) : null}

        {status === "error" ? (
          <div className="ppp-quiet" data-testid={`ppp-error-${variantId}`}>
            <strong>Gagal memuat</strong>
            <p>Muat ulang untuk mencoba lagi.</p>
            {onRetry ? (
              <button
                type="button"
                className="ppp-btn"
                data-testid={`ppp-retry-${variantId}`}
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
          <div className="ppp-quiet" data-testid={`ppp-forbidden-${variantId}`}>
            <strong>Tidak ada izin</strong>
            <p>Kartu ini tidak termasuk akses Anda.</p>
          </div>
        ) : null}

        {showCount ? (
          <div className="ppp-kpi">
            <div className="ppp-kpi-main">
              <p className="ppp-metric" data-testid={`ppp-metric-${variantId}`}>
                <span className="ppp-metric-num">{formatPoCount(count)}</span>
                <span className="ppp-metric-unit"> PO</span>
              </p>
              <p className="ppp-caption">
                {status === "zero" ? PO_PERLU_ZERO_CAPTION : PO_PERLU_CAPTION}
              </p>
              <p className="ppp-meta">
                {showExtraHint ? PO_PERLU_META_LARGE : PO_PERLU_META}
              </p>
            </div>
            {drillLocked ? (
              <p className="ppp-foot ppp-locked" data-testid={`ppp-locked-${variantId}`}>
                Akses rincian terbatas
              </p>
            ) : null}
            {canDrill ? (
              <button
                type="button"
                className="ppp-foot ppp-drill"
                data-testid={`ppp-drill-${variantId}`}
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
          className="ppp-info-backdrop"
          data-testid={`ppp-info-backdrop-${variantId}`}
          onClick={() => {
            setInfoOpen(false);
            window.setTimeout(() => menuBtnRef.current?.focus(), 0);
          }}
        >
          <div
            className="ppp-info-panel"
            role="dialog"
            aria-modal="true"
            aria-labelledby={infoTitleId}
            data-testid={`ppp-info-panel-${variantId}`}
            onClick={stopBubble}
          >
            <div className="ppp-info-head">
              <h3 id={infoTitleId}>Info Widget</h3>
              <button
                type="button"
                className="ppp-icon-btn"
                ref={infoCloseRef}
                aria-label="Tutup info"
                data-testid={`ppp-info-close-${variantId}`}
                onClick={() => {
                  setInfoOpen(false);
                  window.setTimeout(() => menuBtnRef.current?.focus(), 0);
                }}
              >
                <X size={16} />
              </button>
            </div>
            <p>{PO_PERLU_INFO}</p>
          </div>
        </div>
      ) : null}
    </article>
  );
}
