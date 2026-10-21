import { useCallback, useEffect, useMemo, useState } from "react";
import { useLocation, useNavigate, useSearchParams } from "react-router-dom";
import { api } from "../lib/api";
import { formatDate } from "../lib/constants";
import CustomerPricingSettings from "./CustomerPricingSettings";
import PublicCatalogSettings from "./PublicCatalogSettings";
import "./PricingCatalogSettings.css";

/** Group-scoped primary sections */
const MAIN_SECTIONS = [
  { id: "kelompok", label: "Kelompok Produk", testid: "hk-nav-kelompok", domain: "either" },
  { id: "harga", label: "Harga", testid: "hk-nav-harga", domain: "pricing" },
  { id: "katalog", label: "Katalog", testid: "hk-nav-katalog", domain: "catalog" },
  { id: "varian", label: "Varian", testid: "hk-nav-varian", domain: "catalog" },
  { id: "galeri", label: "Galeri", testid: "hk-nav-galeri", domain: "catalog" },
];

/** Global presentation (not group-scoped) */
const GLOBAL_SECTIONS = [
  { id: "umum", label: "Informasi Umum", testid: "hk-nav-umum", domain: "catalog" },
  { id: "kategori", label: "Kategori", testid: "hk-nav-kategori", domain: "catalog" },
];

/**
 * URL & SEO stays available as group-scoped Katalog utility (Option B):
 * not a global nav item; shown only when a group is selected.
 */
const GROUP_UTILITY_SECTIONS = [
  { id: "seo", label: "URL & SEO", testid: "hk-nav-seo", domain: "catalog" },
];

const ALL_SECTIONS = [...MAIN_SECTIONS, ...GLOBAL_SECTIONS, ...GROUP_UTILITY_SECTIONS];

/** Sections that use shared pricing_group_id context bar */
export const GROUP_SCOPED_SECTIONS = new Set(["harga", "katalog", "varian", "galeri", "seo"]);

/** Global sections keep group in URL but hide context bar */
export const GLOBAL_CONTEXT_HIDDEN = new Set(["umum", "kategori", "kelompok"]);

const CATALOG_SECTION_MAP = {
  katalog: "kelompok",
  varian: "varian",
  galeri: "galeri",
  umum: "umum",
  kategori: "kategori",
  seo: "seo",
};

function errDetail(err) {
  return err?.response?.data?.detail || err?.message || "";
}

function sectionAllowed(sectionId, canViewPricing, canViewCatalog) {
  const meta = ALL_SECTIONS.find((item) => item.id === sectionId);
  if (!meta) return false;
  if (meta.domain === "pricing") return canViewPricing;
  if (meta.domain === "catalog") return canViewCatalog;
  return canViewPricing || canViewCatalog;
}

function firstAllowedSection(canViewPricing, canViewCatalog) {
  for (const item of ALL_SECTIONS) {
    if (sectionAllowed(item.id, canViewPricing, canViewCatalog)) return item.id;
  }
  return "kelompok";
}

export function resolvePricingCatalogSection(raw, canViewPricing, canViewCatalog) {
  const requested = String(raw || "").trim().toLowerCase() || "kelompok";
  if (sectionAllowed(requested, canViewPricing, canViewCatalog)) return requested;
  return firstAllowedSection(canViewPricing, canViewCatalog);
}

/** Default section after Kelola from landing — prefer Harga. */
export function defaultKelolaSection(canViewPricing, canViewCatalog) {
  if (canViewPricing) return "harga";
  if (canViewCatalog) return "katalog";
  return "kelompok";
}

/** Keep current group-scoped section when changing group mid-work. */
export function defaultGroupSection(canViewPricing, canViewCatalog, currentSection) {
  if (currentSection && GROUP_SCOPED_SECTIONS.has(currentSection)
    && sectionAllowed(currentSection, canViewPricing, canViewCatalog)) {
    return currentSection;
  }
  return defaultKelolaSection(canViewPricing, canViewCatalog);
}

export function pricingStatusLabel(row) {
  if (!row?.hasPricing) return "Belum diatur";
  if (row.publishedVersionId) return "Published";
  if (row.draftVersionId) return "Draft";
  return "Belum diatur";
}

export function catalogStatusLabel(row) {
  if (!row?.hasCatalogOverlay) return "Belum diatur";
  if (row.catalogVisible) return "Tampil";
  return "Tersembunyi";
}

/** Pricing-group membership count from list summary_version.products */
export function productCountFromPricing(group) {
  return (group?.summary_version?.products || []).length;
}

/**
 * Client merge by pricing_group_id.
 * PRODUCT COUNT = pricing memberships only.
 * Catalog list APIs have no distinct variant_count → do not invent a second count.
 */
export function mergeUnifiedGroupRows({
  pricingGroups = [],
  catalogGroups = [],
  canViewPricing = false,
  canViewCatalog = false,
} = {}) {
  const pricingById = new Map();
  if (canViewPricing) {
    for (const group of pricingGroups || []) {
      if (group?.id) pricingById.set(group.id, group);
    }
  }
  const catalogById = new Map();
  if (canViewCatalog) {
    for (const group of catalogGroups || []) {
      const id = group?.pricing_group_id;
      if (id) catalogById.set(id, group);
    }
  }

  const ids = new Set([...pricingById.keys(), ...catalogById.keys()]);
  const rows = [];
  for (const id of ids) {
    const pricing = pricingById.get(id) || null;
    const catalogRow = catalogById.get(id) || null;
    const catalog = catalogRow?.catalog || null;
    const hasPricing = Boolean(pricing);
    const hasCatalogOverlay = Boolean(catalog?.configured);
    const groupName = pricing?.group_name || catalogRow?.group_name || "";
    const publicName = (catalog?.public_name || "").trim();
    const productCount = hasPricing ? productCountFromPricing(pricing) : null;
    rows.push({
      pricing_group_id: id,
      group_name: groupName,
      public_name: publicName,
      display_name: publicName || groupName,
      slug: catalog?.slug || "",
      category_name: catalog?.category_name || "",
      hasPricing,
      hasCatalogOverlay,
      pricingEnabled: hasPricing
        ? Boolean(pricing.enabled)
        : (catalogRow ? Boolean(catalogRow.pricing_enabled) : null),
      draftVersionId: hasPricing ? (pricing.current_draft_version_id || null) : null,
      draftVersionLabel: hasPricing && pricing.current_draft_version_id
        ? `v${pricing.current_draft_version_number || pricing.summary_version?.version_number || "✓"}`
        : "—",
      publishedVersionId: hasPricing
        ? (pricing.current_published_version_id || null)
        : (catalogRow?.current_published_version_id || null),
      publishedVersionLabel: hasPricing && pricing.current_published_version_id
        ? `v${pricing.current_published_version_number || "✓"}`
        : (hasPricing ? "—" : (catalogRow?.current_published_version_id ? "✓" : "—")),
      productCount,
      catalogVisible: hasCatalogOverlay ? Boolean(catalog.visible) : null,
      updated_at: pricing?.updated_at || catalog?.updated_at || null,
    });
  }

  rows.sort((a, b) => String(a.display_name || a.group_name || "")
    .localeCompare(String(b.display_name || b.group_name || ""), "id"));
  return rows;
}

export function filterUnifiedGroupRows(rows, { search = "", pricingFilter = "all", catalogFilter = "all" } = {}) {
  const needle = String(search || "").trim().toLowerCase();
  return (rows || []).filter((row) => {
    if (needle) {
      const hay = [
        row.group_name,
        row.public_name,
        row.display_name,
        row.slug,
        row.category_name,
      ].join(" ").toLowerCase();
      if (!hay.includes(needle)) return false;
    }
    if (pricingFilter && pricingFilter !== "all") {
      const status = pricingStatusLabel(row);
      if (pricingFilter === "draft" && status !== "Draft") return false;
      if (pricingFilter === "published" && status !== "Published") return false;
      if (pricingFilter === "unset" && status !== "Belum diatur") return false;
    }
    if (catalogFilter && catalogFilter !== "all") {
      const status = catalogStatusLabel(row);
      if (catalogFilter === "tampil" && status !== "Tampil") return false;
      if (catalogFilter === "tersembunyi" && status !== "Tersembunyi") return false;
      if (catalogFilter === "unset" && status !== "Belum diatur") return false;
    }
    return true;
  });
}

export function contextPricingValue(row) {
  if (!row) return "";
  const status = pricingStatusLabel(row);
  if (status === "Published" && row.publishedVersionLabel !== "—") {
    return `${status} ${row.publishedVersionLabel}`;
  }
  if (status === "Draft" && row.draftVersionLabel !== "—") {
    return `${status} ${row.draftVersionLabel}`;
  }
  return status;
}

export default function PricingCatalogSettings({
  notify,
  canViewPricing,
  canEditPricing,
  canPublishPricing,
  canViewCatalog,
  canEditCatalog,
}) {
  const navigate = useNavigate();
  const location = useLocation();
  const [searchParams] = useSearchParams();
  const pricingCatalogActive = (() => {
    const path = String(location.pathname || "").replace(/\/$/, "") || "/";
    return path === "/settings/pricing-catalog";
  })();
  const section = useMemo(
    () => resolvePricingCatalogSection(
      searchParams.get("section"),
      canViewPricing,
      canViewCatalog
    ),
    [searchParams, canViewPricing, canViewCatalog]
  );
  const groupParam = String(searchParams.get("group") || "").trim();

  const [pricingGroups, setPricingGroups] = useState([]);
  const [catalogGroups, setCatalogGroups] = useState([]);
  const [listsLoaded, setListsLoaded] = useState(false);
  const [listError, setListError] = useState("");
  const [landingSearch, setLandingSearch] = useState("");
  const [pricingFilter, setPricingFilter] = useState("all");
  const [catalogFilter, setCatalogFilter] = useState("all");
  const [changeOpen, setChangeOpen] = useState(false);
  const [changeQuery, setChangeQuery] = useState("");

  // Never rewrite away from other Settings tabs (e.g. sales-user-mapping) after leave.
  const writeUrl = useCallback((nextSection, nextGroup, { replace = false } = {}) => {
    const path = String(location.pathname || "").replace(/\/$/, "") || "/";
    if (path !== "/settings/pricing-catalog") return;
    const next = new URLSearchParams();
    next.set("section", nextSection);
    const gid = String(nextGroup || "").trim();
    if (gid) next.set("group", gid);
    navigate(`/settings/pricing-catalog?${next.toString()}`, { replace });
  }, [navigate, location.pathname]);

  useEffect(() => {
    if (!pricingCatalogActive) return;
    const raw = searchParams.get("section");
    const rawGroup = String(searchParams.get("group") || "").trim();
    if (raw === section && rawGroup === groupParam) return;
    writeUrl(section, groupParam, { replace: true });
  }, [pricingCatalogActive, section, groupParam, searchParams, writeUrl]);

  const loadLists = useCallback(async () => {
    setListError("");
    try {
      const tasks = [];
      if (canViewPricing) {
        tasks.push(
          api.customerPricingGroups().then((res) => setPricingGroups(res.groups || []))
        );
      } else {
        setPricingGroups([]);
      }
      if (canViewCatalog) {
        tasks.push(
          api.publicCatalogGroups().then((res) => setCatalogGroups(res.groups || []))
        );
      } else {
        setCatalogGroups([]);
      }
      await Promise.all(tasks);
    } catch (err) {
      setListError(errDetail(err) || "Gagal memuat daftar kelompok.");
      setPricingGroups([]);
      setCatalogGroups([]);
    } finally {
      setListsLoaded(true);
    }
  }, [canViewPricing, canViewCatalog]);

  useEffect(() => {
    setListsLoaded(false);
    loadLists();
  }, [loadLists]);

  const unifiedRows = useMemo(
    () => mergeUnifiedGroupRows({
      pricingGroups,
      catalogGroups,
      canViewPricing,
      canViewCatalog,
    }),
    [pricingGroups, catalogGroups, canViewPricing, canViewCatalog]
  );

  const selectedRow = useMemo(() => {
    if (!groupParam || !listsLoaded) return null;
    return unifiedRows.find((row) => row.pricing_group_id === groupParam) || null;
  }, [groupParam, unifiedRows, listsLoaded]);

  useEffect(() => {
    if (!pricingCatalogActive) return;
    if (!listsLoaded || !groupParam) return;
    if (selectedRow) return;
    writeUrl(section, "", { replace: true });
  }, [pricingCatalogActive, listsLoaded, groupParam, selectedRow, section, writeUrl]);

  const filteredRows = useMemo(
    () => filterUnifiedGroupRows(unifiedRows, {
      search: landingSearch,
      pricingFilter: canViewPricing ? pricingFilter : "all",
      catalogFilter: canViewCatalog ? catalogFilter : "all",
    }),
    [unifiedRows, landingSearch, pricingFilter, catalogFilter, canViewPricing, canViewCatalog]
  );

  const changeOptions = useMemo(() => {
    const needle = changeQuery.trim().toLowerCase();
    if (!needle) return unifiedRows;
    return unifiedRows.filter((row) => {
      const hay = [row.group_name, row.public_name, row.slug, row.category_name].join(" ").toLowerCase();
      return hay.includes(needle);
    });
  }, [unifiedRows, changeQuery]);

  const setSection = (nextSection) => {
    if (!sectionAllowed(nextSection, canViewPricing, canViewCatalog)) return;
    if (nextSection === "seo" && !groupParam) {
      writeUrl("kelompok", "");
      return;
    }
    writeUrl(nextSection, groupParam);
  };

  const selectGroup = (pricingGroupId, { sectionOverride } = {}) => {
    const gid = String(pricingGroupId || "").trim();
    if (!gid) {
      writeUrl("kelompok", "");
      setChangeOpen(false);
      return;
    }
    const nextSection = sectionOverride
      || defaultGroupSection(canViewPricing, canViewCatalog, section);
    writeUrl(nextSection, gid);
    setChangeOpen(false);
    setChangeQuery("");
  };

  const kelolaGroup = (pricingGroupId) => {
    selectGroup(pricingGroupId, {
      sectionOverride: defaultKelolaSection(canViewPricing, canViewCatalog),
    });
  };

  const catalogSection = CATALOG_SECTION_MAP[section] || null;
  const showCatalog = Boolean(catalogSection) && canViewCatalog;
  const showPricing = section === "harga" && canViewPricing;
  const activeGroupId = selectedRow ? selectedRow.pricing_group_id : "";
  const showContextBar = Boolean(selectedRow) && GROUP_SCOPED_SECTIONS.has(section);
  const showGroupUtility = Boolean(selectedRow) && canViewCatalog;

  return (
    <div className="settings-card hk-page" data-testid="pricing-catalog-settings">
      <div className="hk-head">
        <div>
          <h2>Harga & Katalog</h2>
          <p className="hk-subhead">
            Atur harga customer dan tampilan katalog publik dalam satu workspace.
          </p>
        </div>
      </div>

      <nav className="hk-section-nav" aria-label="Bagian utama Harga & Katalog" data-testid="hk-main-nav">
        {MAIN_SECTIONS.filter((item) => sectionAllowed(item.id, canViewPricing, canViewCatalog)).map((item) => (
          <button
            key={item.id}
            type="button"
            className={section === item.id ? "is-active" : ""}
            data-testid={item.testid}
            aria-current={section === item.id ? "page" : undefined}
            onClick={() => setSection(item.id)}
          >
            {item.label}
          </button>
        ))}
      </nav>

      {canViewCatalog ? (
        <nav className="hk-secondary-nav" aria-label="Pengaturan katalog global" data-testid="hk-secondary-nav">
          <span className="hk-secondary-label">Global</span>
          {GLOBAL_SECTIONS.map((item) => (
            <button
              key={item.id}
              type="button"
              className={section === item.id ? "is-active" : ""}
              data-testid={item.testid}
              aria-current={section === item.id ? "page" : undefined}
              onClick={() => setSection(item.id)}
            >
              {item.label}
            </button>
          ))}
          {showGroupUtility ? (
            <>
              <span className="hk-secondary-label hk-secondary-label-group">Kelompok</span>
              {GROUP_UTILITY_SECTIONS.map((item) => (
                <button
                  key={item.id}
                  type="button"
                  className={section === item.id ? "is-active" : ""}
                  data-testid={item.testid}
                  aria-current={section === item.id ? "page" : undefined}
                  onClick={() => setSection(item.id)}
                >
                  {item.label}
                </button>
              ))}
            </>
          ) : null}
        </nav>
      ) : null}

      {showContextBar ? (
        <div className="hk-context-bar" data-testid="hk-group-context">
          <div className="hk-context-title-row">
            <div className="hk-context-names">
              <strong className="hk-context-name" data-testid="hk-context-group-name">
                {selectedRow.display_name}
              </strong>
              {selectedRow.public_name
                && selectedRow.group_name
                && selectedRow.public_name !== selectedRow.group_name ? (
                <span className="hk-context-internal" data-testid="hk-context-internal-name">
                  Internal: {selectedRow.group_name}
                </span>
              ) : null}
            </div>
            <div className="hk-context-actions">
              <button
                type="button"
                className="secondary sm hk-change-group-btn"
                data-testid="hk-change-group"
                aria-expanded={changeOpen}
                aria-haspopup="listbox"
                onClick={() => setChangeOpen((open) => !open)}
              >
                Ganti Kelompok
              </button>
              {changeOpen ? (
                <div className="hk-change-pop" data-testid="hk-change-group-pop" role="listbox">
                  <input
                    value={changeQuery}
                    onChange={(event) => setChangeQuery(event.target.value)}
                    placeholder="Cari kelompok..."
                    data-testid="hk-change-group-search"
                    aria-label="Cari kelompok untuk diganti"
                    autoFocus
                  />
                  <button
                    type="button"
                    className="hk-change-landing"
                    data-testid="hk-change-to-landing"
                    onClick={() => {
                      writeUrl("kelompok", groupParam);
                      setChangeOpen(false);
                    }}
                  >
                    Lihat semua kelompok
                  </button>
                  <ul>
                    {changeOptions.map((row) => (
                      <li key={row.pricing_group_id}>
                        <button
                          type="button"
                          role="option"
                          aria-selected={row.pricing_group_id === activeGroupId}
                          data-testid={`hk-change-option-${row.pricing_group_id}`}
                          className={row.pricing_group_id === activeGroupId ? "is-active" : ""}
                          onClick={() => selectGroup(row.pricing_group_id)}
                        >
                          <span>{row.display_name}</span>
                          {row.public_name && row.group_name !== row.public_name ? (
                            <span className="muted small">{row.group_name}</span>
                          ) : null}
                        </button>
                      </li>
                    ))}
                    {!changeOptions.length ? (
                      <li className="muted small hk-change-empty">Tidak ada kelompok</li>
                    ) : null}
                  </ul>
                </div>
              ) : null}
            </div>
          </div>
          <dl className="hk-context-meta" data-testid="hk-context-meta">
            {canViewPricing ? (
              <div>
                <dt>Harga</dt>
                <dd>{contextPricingValue(selectedRow)}</dd>
              </div>
            ) : null}
            {canViewCatalog ? (
              <div>
                <dt>Katalog</dt>
                <dd>{catalogStatusLabel(selectedRow)}</dd>
              </div>
            ) : null}
            {canViewCatalog ? (
              <div>
                <dt>Kategori</dt>
                <dd>{selectedRow.category_name || "Belum diatur"}</dd>
              </div>
            ) : null}
            {canViewPricing && selectedRow.productCount != null ? (
              <div>
                <dt>Produk</dt>
                <dd>{selectedRow.productCount}</dd>
              </div>
            ) : null}
            {canViewCatalog ? (
              <div>
                <dt>Slug</dt>
                <dd>{selectedRow.slug || "—"}</dd>
              </div>
            ) : null}
          </dl>
        </div>
      ) : null}

      {listError ? (
        <p className="error small" data-testid="hk-list-error">{listError}</p>
      ) : null}

      {section === "kelompok" && (
        <section className="hk-landing" data-testid="hk-kelompok-landing">
          <div className="hk-landing-toolbar">
            <input
              className="hk-landing-search"
              value={landingSearch}
              onChange={(event) => setLandingSearch(event.target.value)}
              placeholder="Cari nama, publik, slug, kategori..."
              data-testid="hk-landing-search"
              aria-label="Cari kelompok"
            />
            {canViewPricing ? (
              <select
                value={pricingFilter}
                onChange={(event) => setPricingFilter(event.target.value)}
                data-testid="hk-filter-pricing"
                aria-label="Filter harga"
              >
                <option value="all">Harga: Semua</option>
                <option value="draft">Harga: Draft</option>
                <option value="published">Harga: Published</option>
                <option value="unset">Harga: Belum diatur</option>
              </select>
            ) : null}
            {canViewCatalog ? (
              <select
                value={catalogFilter}
                onChange={(event) => setCatalogFilter(event.target.value)}
                data-testid="hk-filter-catalog"
                aria-label="Filter katalog"
              >
                <option value="all">Katalog: Semua</option>
                <option value="tampil">Katalog: Tampil</option>
                <option value="tersembunyi">Katalog: Tersembunyi</option>
                <option value="unset">Katalog: Belum diatur</option>
              </select>
            ) : null}
          </div>

          <div className="hk-landing-table-wrap">
            <table className="hk-landing-table" data-testid="hk-unified-table">
              <thead>
                <tr>
                  <th>Kelompok</th>
                  {canViewPricing ? <th>Produk</th> : null}
                  {canViewPricing ? <th>Harga</th> : null}
                  {canViewPricing ? <th>Published</th> : null}
                  {canViewCatalog ? <th>Kategori</th> : null}
                  {canViewCatalog ? <th>Katalog</th> : null}
                  <th>Updated</th>
                  <th>Aksi</th>
                </tr>
              </thead>
              <tbody>
                {!listsLoaded ? (
                  <tr>
                    <td colSpan={8} className="muted small">Memuat kelompok…</td>
                  </tr>
                ) : null}
                {listsLoaded && filteredRows.map((row) => (
                  <tr
                    key={row.pricing_group_id}
                    data-testid={`hk-row-${row.pricing_group_id}`}
                    data-pricing-group-id={row.pricing_group_id}
                    className={row.pricing_group_id === activeGroupId ? "is-selected" : ""}
                    onClick={() => kelolaGroup(row.pricing_group_id)}
                  >
                    <td>
                      <strong>{row.display_name}</strong>
                      {row.public_name
                        && row.group_name
                        && row.public_name !== row.group_name ? (
                        <span className="hk-cell-sub">Internal: {row.group_name}</span>
                      ) : null}
                    </td>
                    {canViewPricing ? (
                      <td>{row.productCount != null ? row.productCount : "—"}</td>
                    ) : null}
                    {canViewPricing ? (
                      <td data-testid={`hk-pricing-status-${row.pricing_group_id}`}>
                        <span className={`hk-status hk-status-pricing-${pricingStatusLabel(row).toLowerCase().replace(/\s+/g, "-")}`}>
                          {pricingStatusLabel(row)}
                        </span>
                      </td>
                    ) : null}
                    {canViewPricing ? <td>{row.publishedVersionLabel}</td> : null}
                    {canViewCatalog ? (
                      <td>{row.category_name || "Belum diatur"}</td>
                    ) : null}
                    {canViewCatalog ? (
                      <td data-testid={`hk-catalog-status-${row.pricing_group_id}`}>
                        <span className={`hk-status hk-status-catalog-${catalogStatusLabel(row).toLowerCase().replace(/\s+/g, "-")}`}>
                          {catalogStatusLabel(row)}
                        </span>
                      </td>
                    ) : null}
                    <td className="hk-updated-cell">
                      {row.updated_at ? formatDate(row.updated_at) : "—"}
                    </td>
                    <td>
                      <button
                        type="button"
                        className="ghost sm"
                        data-testid={`hk-kelola-${row.pricing_group_id}`}
                        aria-label={`Kelola ${row.display_name}`}
                        onClick={(event) => {
                          event.stopPropagation();
                          kelolaGroup(row.pricing_group_id);
                        }}
                      >
                        Kelola
                      </button>
                    </td>
                  </tr>
                ))}
                {listsLoaded && !filteredRows.length ? (
                  <tr>
                    <td colSpan={8} className="muted small" data-testid="hk-landing-empty">
                      Tidak ada kelompok yang cocok.
                    </td>
                  </tr>
                ) : null}
              </tbody>
            </table>
          </div>
        </section>
      )}

      {showPricing ? (
        <div
          data-testid="hk-section-harga"
          className={activeGroupId ? "hk-embed-focused" : ""}
        >
          <CustomerPricingSettings
            notify={notify}
            canEdit={canEditPricing}
            canPublish={canPublishPricing}
            embedded
            selectedGroupId={activeGroupId}
          />
        </div>
      ) : null}

      {showCatalog ? (
        <div
          data-testid={`hk-section-${section}`}
          className={
            activeGroupId && (section === "katalog" || section === "seo")
              ? "hk-embed-focused"
              : ""
          }
        >
          {!activeGroupId && (section === "varian" || section === "galeri" || section === "seo") ? (
            <p className="muted small" data-testid="hk-section-pick-group">
              Pilih kelompok dari Kelompok Produk.
            </p>
          ) : (
            <PublicCatalogSettings
              notify={notify}
              canEdit={canEditCatalog}
              embedded
              section={catalogSection}
              selectedGroupId={activeGroupId}
              onSectionChange={(pcsSection) => {
                const mapped = Object.entries(CATALOG_SECTION_MAP)
                  .find(([, value]) => value === pcsSection)?.[0];
                if (mapped) setSection(mapped);
              }}
            />
          )}
        </div>
      ) : null}
    </div>
  );
}
