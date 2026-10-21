import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link, Navigate, useNavigate, useSearchParams } from "react-router-dom";
import {
  BookOpen, Boxes, Building2, ClipboardList, Factory, FileSpreadsheet,
  LayoutGrid, List, Package, PiggyBank, Search, Star, TrendingUp, Wallet,
} from "lucide-react";
import { api } from "../lib/api";
import { useAuth } from "../lib/auth";
import {
  REPORT_CATEGORY_ORDER,
  categoryCounts,
  filterReportCatalog,
  readReportFavorites,
  toggleReportFavorite,
  visibleReportCatalog,
} from "../lib/reportCatalog";
import "./ReportCatalog.css";

const TONE_ICON = {
  green: TrendingUp,
  teal: Package,
  amber: Boxes,
  blue: BookOpen,
  purple: Factory,
  emerald: PiggyBank,
  orange: Building2,
  cyan: Wallet,
};

const CATEGORY_LABEL = Object.fromEntries(REPORT_CATEGORY_ORDER.map((c) => [c.id, c.label]));

function formatSavedAt(value) {
  if (!value) return "—";
  const d = new Date(value);
  if (Number.isNaN(d.getTime())) return String(value);
  return d.toLocaleString("id-ID", {
    day: "2-digit", month: "short", year: "numeric",
    hour: "2-digit", minute: "2-digit",
  });
}

export default function ReportCatalog() {
  const { hasPerm, user } = useAuth();
  const navigate = useNavigate();
  const [searchParams, setSearchParams] = useSearchParams();
  const view = searchParams.get("view") === "saved" ? "saved" : "list";
  const userId = user?.id || user?._id || user?.email || "guest";

  const [query, setQuery] = useState("");
  const [searchFocused, setSearchFocused] = useState(false);
  const [category, setCategory] = useState("all");
  const [layout, setLayout] = useState("cards");
  const [favorites, setFavorites] = useState(() => readReportFavorites(userId));
  const [saved, setSaved] = useState([]);
  const [savedBusy, setSavedBusy] = useState(false);
  const [savedError, setSavedError] = useState("");
  const searchRef = useRef(null);

  const visible = useMemo(() => visibleReportCatalog(hasPerm), [hasPerm]);
  const counts = useMemo(() => categoryCounts(visible), [visible]);
  const favoriteCount = useMemo(
    () => visible.filter((r) => favorites.includes(r.id)).length,
    [visible, favorites],
  );

  const filtered = useMemo(
    () => filterReportCatalog(visible, { query, category, favoriteIds: favorites }),
    [visible, query, category, favorites],
  );

  useEffect(() => {
    setFavorites(readReportFavorites(userId));
  }, [userId]);

  const canLoadSalesMemorized = hasPerm("reports.sales.view");
  useEffect(() => {
    if (view !== "saved") return undefined;
    if (!canLoadSalesMemorized) {
      setSaved([]);
      setSavedError("");
      setSavedBusy(false);
      return undefined;
    }
    let active = true;
    setSavedBusy(true);
    setSavedError("");
    api.salesReportCatalog()
      .then((data) => {
        if (!active) return;
        setSaved(Array.isArray(data?.memorized) ? data.memorized : []);
      })
      .catch((err) => {
        if (!active) return;
        setSaved([]);
        setSavedError(err?.response?.data?.detail || err?.message || "Gagal memuat laporan tersimpan.");
      })
      .finally(() => { if (active) setSavedBusy(false); });
    return () => { active = false; };
  }, [view, canLoadSalesMemorized, userId]);

  const onToggleFavorite = useCallback((reportId, event) => {
    event.preventDefault();
    event.stopPropagation();
    setFavorites(toggleReportFavorite(userId, reportId));
  }, [userId]);

  const openSaved = useCallback((item) => {
    if (!item?.report_id) return;
    const params = new URLSearchParams({ report: item.report_id });
    if (item.id) params.set("memorize", item.id);
    navigate(`/laporan/penjualan?${params.toString()}`);
  }, [navigate]);

  if (!visible.length) {
    return <Navigate to="/" replace />;
  }

  const chips = [
    { id: "all", label: `Semua (${counts.all || 0})` },
    { id: "favorites", label: `Favorit (${favoriteCount})` },
    ...REPORT_CATEGORY_ORDER.map((c) => ({
      id: c.id,
      label: `${c.label} (${counts[c.id] || 0})`,
    })),
  ];

  return (
    <section className="rpc" data-testid="report-catalog" data-view={view}>
      <header className="rpc-header">
        <p className="rpc-breadcrumb">Laporan › {view === "saved" ? "Laporan Tersimpan" : "Daftar Laporan"}</p>
        <div className="rpc-header-row">
          <div className="rpc-title-block">
            <h1>{view === "saved" ? "Laporan Tersimpan" : "Daftar Laporan"}</h1>
            <p className="rpc-sub">
              {view === "saved"
                ? "Tampilan laporan yang Anda simpan untuk akses cepat."
                : "Pusat laporan operasional dan keuangan Eternal Packaging."}
            </p>
          </div>
          {view === "list" && (
            <div className="rpc-toolbar" data-testid="report-catalog-toolbar">
              <label
                className={
                  "rpc-search"
                  + (searchFocused ? " is-focused" : "")
                  + (query ? " has-value" : "")
                }
              >
                <Search size={16} aria-hidden="true" />
                <input
                  ref={searchRef}
                  type="search"
                  value={query}
                  onChange={(e) => setQuery(e.target.value)}
                  onFocus={() => setSearchFocused(true)}
                  onBlur={() => setSearchFocused(false)}
                  placeholder="Cari laporan atau kategori"
                  aria-label="Cari laporan atau kategori"
                  data-testid="report-catalog-search"
                />
                <kbd aria-hidden="true">Ctrl K</kbd>
              </label>
              <div className="rpc-view-toggle" role="group" aria-label="Tampilan katalog">
                <button
                  type="button"
                  className={layout === "cards" ? "active" : ""}
                  aria-pressed={layout === "cards"}
                  data-testid="report-catalog-layout-cards"
                  onClick={() => setLayout("cards")}
                >
                  <LayoutGrid size={15} aria-hidden="true" />
                  <span>Tampilan Kartu</span>
                </button>
                <button
                  type="button"
                  className={layout === "list" ? "active" : ""}
                  aria-pressed={layout === "list"}
                  data-testid="report-catalog-layout-list"
                  onClick={() => setLayout("list")}
                >
                  <List size={15} aria-hidden="true" />
                  <span>Tampilan Daftar</span>
                </button>
              </div>
            </div>
          )}
        </div>
      </header>

      {view === "list" && (
        <>
          <div className="rpc-chips" role="tablist" aria-label="Kategori laporan">
            {chips.map((chip) => (
              <button
                key={chip.id}
                type="button"
                role="tab"
                aria-selected={category === chip.id}
                className={category === chip.id ? "active" : ""}
                data-testid={`report-catalog-chip-${chip.id}`}
                onClick={() => setCategory(chip.id)}
              >
                {chip.label}
              </button>
            ))}
          </div>

          {!filtered.length ? (
            <div className="rpc-empty" data-testid="report-catalog-empty">
              <ClipboardList size={28} aria-hidden="true" />
              <p>Tidak ada laporan pada filter ini.</p>
            </div>
          ) : layout === "cards" ? (
            <div className="rpc-grid" data-testid="report-catalog-grid">
              {filtered.map((row) => {
                const Icon = TONE_ICON[row.tone] || FileSpreadsheet;
                const fav = favorites.includes(row.id);
                return (
                  <Link
                    key={row.id}
                    to={row.to}
                    className={`rpc-card tone-${row.tone || "green"}`}
                    data-testid={`report-catalog-card-${row.id}`}
                  >
                    <div className="rpc-card-top">
                      <span className="rpc-card-icon" aria-hidden="true"><Icon size={18} /></span>
                      <button
                        type="button"
                        className={`rpc-fav${fav ? " is-on" : ""}`}
                        aria-label={fav ? `Hapus favorit ${row.name}` : `Favoritkan ${row.name}`}
                        aria-pressed={fav}
                        data-testid={`report-catalog-fav-${row.id}`}
                        onClick={(e) => onToggleFavorite(row.id, e)}
                      >
                        <Star size={16} fill={fav ? "currentColor" : "none"} />
                      </button>
                    </div>
                    <h2>{row.name}</h2>
                    <p>{row.description}</p>
                    <span className="rpc-tag">{CATEGORY_LABEL[row.category] || row.category}</span>
                  </Link>
                );
              })}
            </div>
          ) : (
            <ul className="rpc-list" data-testid="report-catalog-list">
              {filtered.map((row) => {
                const Icon = TONE_ICON[row.tone] || FileSpreadsheet;
                const fav = favorites.includes(row.id);
                return (
                  <li key={row.id}>
                    <Link to={row.to} data-testid={`report-catalog-row-${row.id}`}>
                      <span className={`rpc-list-icon tone-${row.tone || "green"}`} aria-hidden="true">
                        <Icon size={16} />
                      </span>
                      <span className="rpc-list-body">
                        <strong>{row.name}</strong>
                        <small>{row.description}</small>
                      </span>
                      <span className="rpc-tag">{CATEGORY_LABEL[row.category] || row.category}</span>
                    </Link>
                    <button
                      type="button"
                      className={`rpc-fav${fav ? " is-on" : ""}`}
                      aria-label={fav ? `Hapus favorit ${row.name}` : `Favoritkan ${row.name}`}
                      onClick={(e) => onToggleFavorite(row.id, e)}
                    >
                      <Star size={16} fill={fav ? "currentColor" : "none"} />
                    </button>
                  </li>
                );
              })}
            </ul>
          )}
        </>
      )}

      {view === "saved" && (
        <div className="rpc-saved" data-testid="report-catalog-saved">
          <div className="rpc-saved-actions">
            <button
              type="button"
              className="rpc-linkish"
              onClick={() => setSearchParams({})}
              data-testid="report-catalog-back-list"
            >
              ← Kembali ke Daftar Laporan
            </button>
          </div>
          {savedBusy && <p className="rpc-status">Memuat laporan tersimpan…</p>}
          {savedError && <p className="rpc-error" role="alert">{savedError}</p>}
          {!savedBusy && !savedError && !saved.length && (
            <div className="rpc-empty" data-testid="report-catalog-saved-empty">
              <ClipboardList size={28} aria-hidden="true" />
              <p>Belum ada laporan tersimpan.</p>
              <p className="rpc-empty-hint">Simpan tampilan dari Laporan Penjualan untuk muncul di sini.</p>
            </div>
          )}
          {!savedBusy && !!saved.length && (
            <ul className="rpc-saved-list" data-testid="report-catalog-saved-list">
              {saved.map((item) => (
                <li key={item.id || item.name}>
                  <button type="button" onClick={() => openSaved(item)} data-testid={`report-saved-${item.id}`}>
                    <span className="rpc-list-icon tone-green" aria-hidden="true"><FileSpreadsheet size={16} /></span>
                    <span className="rpc-list-body">
                      <strong>{item.name || "Tanpa nama"}</strong>
                      <small>
                        {item.report_id || "laporan"}
                        {item.description ? ` · ${item.description}` : ""}
                        {item.updated_at || item.created_at ? ` · ${formatSavedAt(item.updated_at || item.created_at)}` : ""}
                      </small>
                    </span>
                  </button>
                </li>
              ))}
            </ul>
          )}
        </div>
      )}
    </section>
  );
}
