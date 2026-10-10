import { act } from "react";
import { createRoot } from "react-dom/client";
import api from "../lib/api";
import PublicHargaPage from "./PublicHargaPage";

const mockNavigate = jest.fn();
let mockParams = {};
jest.mock("react-router-dom", () => ({
  Link: ({ to, children, ...props }) => <a href={to} {...props}>{children}</a>,
  useNavigate: () => mockNavigate,
  useParams: () => mockParams,
}), { virtual: true });

jest.mock("../lib/api", () => ({
  __esModule: true,
  publicApiUrl: (path) => `http://localhost:8000${path}`,
  default: {
    publicCatalogHome: jest.fn(),
    publicCustomerPricingGroups: jest.fn(),
    publicCustomerPricingGroup: jest.fn(),
    publicCustomerPricingGroupBySlug: jest.fn(),
    calculatePublicCustomerPrice: jest.fn(),
  },
}));

global.IS_REACT_ACT_ENVIRONMENT = true;

const homePayload = {
  settings: {
    catalog_name: "Eternal Catalog",
    hero_title: "Kemasan Berkualitas untuk Bisnis Anda",
    hero_subtitle: "Harga produk terkini",
    public_whatsapp_phone: "6281234567890",
    catalog_enabled: true,
  },
  categories: [
    { id: "cat-cup", name: "Cup", slug: "cup", image_url: null, display_order: 1 },
    { id: "cat-bowl", name: "Bowl", slug: "bowl", image_url: null, display_order: 2 },
  ],
  groups: [
    {
      public_id: "group-paper-cup",
      slug: "paper-cup",
      name: "Paper Cup Premium",
      public_description: "Paper cup premium untuk kebutuhan minuman.",
      image_url: "/api/public/customer-pricing/groups/group-paper-cup/image",
      category: { id: "cat-cup", name: "Cup", slug: "cup" },
      min_unit_price: 2100,
      variant_count: 2,
      size_summary: "12oz – 16oz",
      search_text: "Paper Cup Premium Cup 12oz 16oz",
      variants: [
        { public_variant_id: "variant-12", display_name: "12oz", unit: "pcs", size: "12oz", color: null, finishing: null },
        { public_variant_id: "variant-16", display_name: "16oz", unit: "pcs", size: "16oz", color: null, finishing: null },
      ],
    },
    {
      public_id: "group-paper-bag",
      slug: null,
      name: "Paper Bag",
      public_description: null,
      image_url: null,
      category: { id: "cat-bowl", name: "Bowl", slug: "bowl" },
      min_unit_price: 1500,
      variant_count: 1,
      size_summary: null,
      search_text: "Paper Bag Bowl Small",
      variants: [
        { public_variant_id: "variant-small", display_name: "Small", unit: "pcs" },
      ],
    },
  ],
  message: null,
};

const cupDetail = {
  public_id: "group-paper-cup",
  slug: "paper-cup",
  name: "Paper Cup Premium",
  public_description: "Paper cup premium untuk kebutuhan minuman.",
  image_url: "/api/public/customer-pricing/groups/group-paper-cup/image",
  category: { id: "cat-cup", name: "Cup", slug: "cup" },
  min_unit_price: 2100,
  variant_count: 2,
  meta_title: "Paper Cup SEO",
  meta_description: "Deskripsi SEO paper cup",
  contact: { public_whatsapp_phone: "6281234567890" },
  variants: [
    {
      public_variant_id: "variant-12",
      display_name: "12oz Bening",
      unit: "pcs",
      minimum_quantity: 1000,
      size: "12oz",
      color: "Bening",
      finishing: null,
      image_url: "/api/public/customer-pricing/groups/group-paper-cup/variants/variant-12/gallery/img-a/image",
      images: [
        {
          id: "img-a",
          role: "MAIN",
          role_label: "Utama",
          display_order: 0,
          is_default: true,
          url: "/api/public/customer-pricing/groups/group-paper-cup/variants/variant-12/gallery/img-a/image",
        },
        {
          id: "img-b",
          role: "TOP",
          role_label: "Atas",
          display_order: 1,
          is_default: false,
          url: "/api/public/customer-pricing/groups/group-paper-cup/variants/variant-12/gallery/img-b/image",
        },
      ],
      tiers: [
        { min_qty: 1000, max_qty: 2999, unit_price: 2500 },
        { min_qty: 3000, max_qty: 4999, unit_price: 2300 },
        { min_qty: 5000, max_qty: null, unit_price: 2100 },
      ],
    },
    {
      public_variant_id: "variant-16",
      display_name: "16oz Hitam",
      unit: "pcs",
      minimum_quantity: 1000,
      size: "16oz",
      color: "Hitam",
      finishing: null,
      image_url: "/api/public/customer-pricing/groups/group-paper-cup/variants/variant-16/image",
      images: [],
      tiers: [{ min_qty: 1000, max_qty: null, unit_price: 2800 }],
    },
  ],
};

const flatDetail = {
  public_id: "group-flat",
  slug: null,
  name: "Flat Box",
  public_description: null,
  image_url: null,
  min_unit_price: 1800,
  contact: { public_whatsapp_phone: "" },
  variants: [
    {
      public_variant_id: "var-a",
      display_name: "Box A",
      unit: "pcs",
      minimum_quantity: 500,
      size: null,
      color: null,
      finishing: null,
      image_url: null,
      images: [],
      tiers: [{ min_qty: 500, max_qty: null, unit_price: 1800 }],
    },
    {
      public_variant_id: "var-b",
      display_name: "Box B",
      unit: "pcs",
      minimum_quantity: 500,
      size: null,
      color: null,
      finishing: null,
      image_url: null,
      images: [],
      tiers: [{ min_qty: 500, max_qty: null, unit_price: 1900 }],
    },
  ],
};

const flush = async () => {
  await act(async () => {
    await Promise.resolve();
    await Promise.resolve();
  });
};

describe("PublicHargaPage catalog and detail", () => {
  let container;
  let root;

  const renderAt = async (pathName) => {
    if (pathName === "/harga") {
      mockParams = {};
    } else if (pathName.startsWith("/produk/")) {
      mockParams = { slug: pathName.replace("/produk/", "") };
    } else {
      mockParams = { groupPublicId: pathName.replace("/harga/", "") };
    }
    await act(async () => root.render(<PublicHargaPage />));
    await flush();
  };

  beforeEach(() => {
    jest.useFakeTimers();
    container = document.createElement("div");
    document.body.appendChild(container);
    root = createRoot(container);
    api.publicCatalogHome.mockResolvedValue(homePayload);
    api.publicCustomerPricingGroup.mockResolvedValue({ group: cupDetail });
    api.publicCustomerPricingGroupBySlug.mockResolvedValue({ group: cupDetail });
    api.calculatePublicCustomerPrice.mockResolvedValue({
      quantity: 1000,
      unit_price: 2500,
      total: 2500000,
    });
    mockNavigate.mockReset();
    mockParams = {};
  });

  afterEach(async () => {
    await act(async () => root.unmount());
    container.remove();
    jest.useRealTimers();
    jest.clearAllMocks();
  });

  const flushCalc = async () => {
    await act(async () => {
      jest.advanceTimersByTime(320);
      await Promise.resolve();
    });
    await flush();
  };

  test("home renders hero settings, categories, cards, min price, variant count", async () => {
    await renderAt("/harga");
    expect(container.querySelector('[data-testid="public-harga-page"]')).not.toBeNull();
    expect(container.querySelector(".app-shell")).toBeNull();
    expect(container.textContent).toContain("Kemasan Berkualitas untuk Bisnis Anda");
    expect(container.textContent).toContain("Harga produk terkini");
    expect(container.textContent).toContain("Paper Cup Premium");
    expect(container.textContent).toContain("Paper Bag");
    expect(container.textContent).toContain("Cup");
    expect(container.textContent).toContain("2 varian");
    expect(container.querySelector('[data-testid="harga-min-price-group-paper-cup"]').textContent)
      .toContain("Rp 2.100");
    expect(container.querySelector('img[alt="Paper Cup Premium"]').src).toContain(
      "/api/public/customer-pricing/groups/group-paper-cup/image"
    );
    expect(container.querySelectorAll('[data-testid="harga-image-placeholder"]').length).toBe(1);
    expect(container.querySelector('[data-testid="harga-hero-search-wrap"]')).not.toBeNull();
    expect(api.publicCatalogHome).toHaveBeenCalled();
    expect(api.publicCustomerPricingGroup).not.toHaveBeenCalled();
  });

  test("fractional min unit price keeps decimals in id-ID money format", async () => {
    api.publicCatalogHome.mockResolvedValueOnce({
      ...homePayload,
      groups: [
        {
          ...homePayload.groups[0],
          public_id: "group-fraction",
          name: "Fraction Cup",
          min_unit_price: 1.4,
          variant_count: 1,
        },
      ],
    });
    await renderAt("/harga");
    const price = container.querySelector('[data-testid="harga-min-price-group-fraction"]').textContent;
    expect(price).toContain("Rp 1,40");
    expect(price).not.toMatch(/Rp 1\s*\/\s*pcs/);
  });

  test("home uses Produk Tersedia and neutral hero labels without unsupported claims", async () => {
    await renderAt("/harga");
    expect(container.textContent).toContain("Produk Tersedia");
    expect(container.textContent).toContain("Temukan produk yang Anda butuhkan");
    expect(container.textContent).not.toMatch(/Produk Populer/i);
    expect(container.textContent).not.toContain("Kualitas Terjamin");
    expect(container.textContent).not.toContain("Harga Terupdate");
    expect(container.textContent).not.toContain("Pilihan Lengkap");
    expect(container.textContent).not.toContain("Mudah Dipesan");
    expect(container.textContent).toContain("Harga Transparan");
    expect(container.textContent).toContain("Banyak Pilihan Varian");
    expect(container.textContent).toContain("Harga Berdasarkan Jumlah");
    expect(container.textContent).toContain("Mudah Hubungi Tim");
  });

  test("search and category filter narrow product cards", async () => {
    await renderAt("/harga");
    const search = container.querySelector('[data-testid="harga-hero-search"]');
    await act(async () => {
      const setter = Object.getOwnPropertyDescriptor(Object.getPrototypeOf(search), "value").set;
      setter.call(search, "bag");
      search.dispatchEvent(new Event("input", { bubbles: true }));
      search.dispatchEvent(new Event("change", { bubbles: true }));
    });
    expect(container.textContent).toContain("Paper Bag");
    expect(container.textContent).not.toContain("Paper Cup Premium");

    await act(async () => {
      const setter = Object.getOwnPropertyDescriptor(Object.getPrototypeOf(search), "value").set;
      setter.call(search, "");
      search.dispatchEvent(new Event("input", { bubbles: true }));
      search.dispatchEvent(new Event("change", { bubbles: true }));
    });
    await act(async () => {
      container.querySelector('[data-testid="harga-category-cat-cup"]')
        .dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });
    expect(container.textContent).toContain("Paper Cup Premium");
    expect(container.textContent).not.toContain("Paper Bag");
  });

  test("card with slug navigates to /produk/{slug}", async () => {
    await renderAt("/harga");
    const card = container.querySelector('[data-testid="harga-group-group-paper-cup"]');
    await act(async () => card.dispatchEvent(new MouseEvent("click", { bubbles: true })));
    expect(mockNavigate).toHaveBeenCalledWith("/produk/paper-cup");
  });

  test("card without slug navigates to /harga/{public_id}", async () => {
    await renderAt("/harga");
    const card = container.querySelector('[data-testid="harga-group-group-paper-bag"]');
    await act(async () => card.dispatchEvent(new MouseEvent("click", { bubbles: true })));
    expect(mockNavigate).toHaveBeenCalledWith("/harga/group-paper-bag");
  });

  test("WhatsApp shown when phone configured and hidden when missing", async () => {
    await renderAt("/harga");
    expect(container.querySelector('[data-testid="harga-header-whatsapp"]')).not.toBeNull();
    expect(container.querySelector('[data-testid="harga-help-whatsapp"]')).not.toBeNull();

    await act(async () => root.unmount());
    container.remove();
    container = document.createElement("div");
    document.body.appendChild(container);
    root = createRoot(container);
    api.publicCatalogHome.mockResolvedValueOnce({
      ...homePayload,
      settings: { ...homePayload.settings, public_whatsapp_phone: "" },
    });
    await renderAt("/harga");
    expect(container.querySelector('[data-testid="harga-header-whatsapp"]')).toBeNull();
    expect(container.querySelector('[data-testid="harga-help-whatsapp"]')).toBeNull();
  });

  test("disabled catalog shows customer-safe message", async () => {
    api.publicCatalogHome.mockResolvedValueOnce({
      settings: { ...homePayload.settings, catalog_enabled: false, public_whatsapp_phone: "" },
      categories: [],
      groups: [],
      message: "Katalog harga sedang tidak tersedia.",
    });
    await renderAt("/harga");
    expect(container.querySelector('[data-testid="public-harga-disabled"]').textContent)
      .toContain("Katalog harga sedang tidak tersedia.");
  });

  test("empty catalog uses polished public state", async () => {
    api.publicCatalogHome.mockResolvedValueOnce({
      ...homePayload,
      groups: [],
      categories: [],
    });
    await renderAt("/harga");
    expect(container.querySelector('[data-testid="public-harga-empty"]').textContent)
      .toContain("Produk tidak tersedia.");
  });

  test("public_id detail loads public name, structured selectors, tiers, MOQ, WhatsApp", async () => {
    await renderAt("/harga/group-paper-cup");
    expect(api.publicCustomerPricingGroup).toHaveBeenCalledWith("group-paper-cup");
    expect(container.querySelector('[data-testid="harga-detail-title"]').textContent)
      .toBe("Paper Cup Premium");
    expect(container.textContent).toContain("Mulai dari");
    expect(container.querySelector('[data-testid="harga-structured-selectors"]')).not.toBeNull();
    expect(container.querySelector('[data-testid="harga-size-12oz"]')).not.toBeNull();
    expect(container.querySelector('[data-testid="harga-detail-whatsapp"]')).not.toBeNull();
  });

  test("slug route loads same detail via by-slug API", async () => {
    await renderAt("/produk/paper-cup");
    expect(api.publicCustomerPricingGroupBySlug).toHaveBeenCalledWith("paper-cup");
    expect(container.querySelector('[data-testid="harga-detail-title"]').textContent)
      .toBe("Paper Cup Premium");
  });

  test("structured selection changes price, tiers, and default image", async () => {
    api.calculatePublicCustomerPrice.mockResolvedValue({
      quantity: 1000,
      unit_price: 2800,
      total: 2800000,
    });
    await renderAt("/harga/group-paper-cup");
    const beforeSrc = container.querySelector('[data-testid="harga-detail-image"] img')?.src || "";
    await act(async () => {
      container.querySelector('[data-testid="harga-size-16oz"]')
        .dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });
    await act(async () => {
      container.querySelector('[data-testid="harga-color-Hitam"]')
        .dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });
    await flush();
    expect(container.querySelector('[data-testid="harga-current-price"]').textContent)
      .toContain("Rp 2.800");
    expect(container.querySelector('[data-testid="harga-moq"]').textContent)
      .toContain("1.000");
    expect(container.querySelector('[data-testid="harga-tier-block"]').textContent)
      .toContain("Rp 2.800");
    const afterSrc = container.querySelector('[data-testid="harga-detail-image"] img')?.src || "";
    expect(afterSrc).toContain("variant-16");
    expect(afterSrc).not.toBe(beforeSrc);
    await flushCalc();
    expect(api.calculatePublicCustomerPrice).toHaveBeenCalledWith({
      group_public_id: "group-paper-cup",
      public_variant_id: "variant-16",
      quantity: 1000,
    });
    expect(container.querySelector('[data-testid="harga-estimate-success"]').textContent)
      .toContain("Rp 2.800.000");
  });

  test("thumbnail click changes preview only and does not change price/variant", async () => {
    await renderAt("/harga/group-paper-cup");
    await act(async () => {
      container.querySelector('[data-testid="harga-size-12oz"]')
        .dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });
    await act(async () => {
      container.querySelector('[data-testid="harga-color-Bening"]')
        .dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });
    await flush();
    const priceBefore = container.querySelector('[data-testid="harga-current-price"]').textContent;
    const previewBefore = container.querySelector('[data-testid="harga-detail-image"] img').src;
    await act(async () => {
      container.querySelector('[data-testid="harga-thumb-img-b"]')
        .dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });
    const previewAfter = container.querySelector('[data-testid="harga-detail-image"] img').src;
    expect(previewAfter).toContain("img-b");
    expect(previewAfter).not.toBe(previewBefore);
    expect(container.querySelector('[data-testid="harga-current-price"]').textContent)
      .toBe(priceBefore);
    expect(container.querySelector('[data-testid="harga-moq"]').textContent)
      .toContain("1.000");
  });

  test("flat variant fallback works when structured metadata missing", async () => {
    api.publicCustomerPricingGroup.mockResolvedValueOnce({ group: flatDetail });
    await renderAt("/harga/group-flat");
    expect(container.querySelector('[data-testid="harga-flat-variants"]')).not.toBeNull();
    expect(container.querySelector('[data-testid="harga-structured-selectors"]')).toBeNull();
    await act(async () => {
      container.querySelector('[data-testid="harga-variant-var-b"]')
        .dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });
    expect(container.querySelector('[data-testid="harga-current-price"]').textContent)
      .toContain("Rp 1.900");
  });

  test("invalid group uses safe public state", async () => {
    api.publicCustomerPricingGroup.mockRejectedValueOnce({
      response: { status: 404, data: { detail: "Produk harga tidak tersedia." } },
    });
    await renderAt("/harga/not-valid");
    expect(container.querySelector('[data-testid="public-harga-not-found"]').textContent)
      .toContain("Produk");
    expect(container.textContent).not.toContain("[object Object]");
  });

  test("back link and public DOM exclude internal pricing labels", async () => {
    await renderAt("/harga/group-paper-cup");
    const back = Array.from(container.querySelectorAll("a"))
      .find((anchor) => anchor.textContent.includes("Kembali ke Katalog"));
    expect(back.getAttribute("href")).toBe("/harga");
    const publicText = container.textContent.toLowerCase();
    for (const label of ["hpp", "profit", "draft", "published", "margin", "markup"]) {
      expect(publicText).not.toContain(label);
    }
  });

  test("missing detail image uses branded placeholder", async () => {
    api.publicCustomerPricingGroup.mockResolvedValueOnce({
      group: { ...flatDetail, variants: [flatDetail.variants[0]] },
    });
    await renderAt("/harga/group-flat");
    expect(container.querySelector('[data-testid="harga-detail-image"] img')).toBeNull();
    expect(
      container.querySelector('[data-testid="harga-detail-image"] [data-testid="harga-image-placeholder"]')
    ).not.toBeNull();
  });

  test("official calculate API drives estimate total and WhatsApp quote lines", async () => {
    api.calculatePublicCustomerPrice.mockResolvedValue({
      quantity: 1000,
      unit_price: 2500,
      total: 2500000,
    });
    await renderAt("/harga/group-paper-cup");
    await act(async () => {
      container.querySelector('[data-testid="harga-size-12oz"]')
        .dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });
    await act(async () => {
      container.querySelector('[data-testid="harga-color-Bening"]')
        .dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });
    await flush();
    await flushCalc();
    expect(container.querySelector('[data-testid="harga-estimate-card"]')).not.toBeNull();
    expect(container.querySelector('[data-testid="harga-estimate-success"]').textContent)
      .toContain("Rp 2.500.000");
    const wa = container.querySelector('[data-testid="harga-detail-whatsapp"]').getAttribute("href");
    expect(decodeURIComponent(wa)).toContain("Harga satuan: Rp 2.500");
    expect(decodeURIComponent(wa)).toContain("Estimasi total: Rp 2.500.000");
    expect(decodeURIComponent(wa)).toContain("Varian: 12oz Bening");
  });

  test("failed calculate hides total estimate and omits price from WhatsApp", async () => {
    api.calculatePublicCustomerPrice.mockRejectedValue({
      response: { status: 400, data: { detail: "Jumlah di bawah minimum pembelian." } },
    });
    await renderAt("/harga/group-paper-cup");
    await act(async () => {
      container.querySelector('[data-testid="harga-size-12oz"]')
        .dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });
    await act(async () => {
      container.querySelector('[data-testid="harga-color-Bening"]')
        .dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });
    await flush();
    await flushCalc();
    expect(container.querySelector('[data-testid="harga-estimate-success"]')).toBeNull();
    expect(container.querySelector('[data-testid="harga-estimate-error"]').textContent)
      .toMatch(/minimum|tidak valid|Jumlah/i);
    const wa = container.querySelector('[data-testid="harga-detail-whatsapp"]').getAttribute("href");
    expect(decodeURIComponent(wa)).not.toContain("Estimasi total:");
    expect(decodeURIComponent(wa)).not.toContain("Harga satuan:");
  });

  test("many flat variants use searchable selector", async () => {
    const many = Array.from({ length: 8 }, (_, index) => ({
      public_variant_id: `var-${index}`,
      display_name: `Box ${index + 1}`,
      unit: "pcs",
      minimum_quantity: 100,
      size: null,
      color: null,
      finishing: null,
      image_url: null,
      images: [],
      tiers: [{ min_qty: 100, max_qty: null, unit_price: 1000 + index }],
    }));
    api.publicCustomerPricingGroup.mockResolvedValueOnce({
      group: { ...flatDetail, variants: many, variant_count: many.length },
    });
    await renderAt("/harga/group-flat");
    expect(container.querySelector('[data-testid="harga-variant-search"]')).not.toBeNull();
    expect(container.querySelector('[data-testid="harga-variant-select"]')).not.toBeNull();
  });

  test("breadcrumb renders on detail page", async () => {
    await renderAt("/produk/paper-cup");
    const crumb = container.querySelector('[data-testid="harga-breadcrumb"]');
    expect(crumb).not.toBeNull();
    expect(crumb.textContent).toContain("Beranda");
    expect(crumb.textContent).toContain("Cup");
    expect(crumb.textContent).toContain("Paper Cup Premium");
  });

  test("detail money formatting keeps whole rupiah compact and quotes decimals when needed", async () => {
    api.publicCustomerPricingGroup.mockResolvedValueOnce({
      group: {
        ...cupDetail,
        min_unit_price: 1.4,
        variants: [{
          ...cupDetail.variants[0],
          tiers: [
            { min_qty: 1000, max_qty: 2999, unit_price: 1.4 },
            { min_qty: 3000, max_qty: null, unit_price: 2100 },
          ],
        }, cupDetail.variants[1]],
      },
    });
    api.calculatePublicCustomerPrice.mockResolvedValue({
      quantity: 1000,
      unit_price: 1.4,
      total: 1400,
    });
    await renderAt("/harga/group-paper-cup");
    await act(async () => {
      container.querySelector('[data-testid="harga-size-12oz"]')
        .dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });
    await act(async () => {
      container.querySelector('[data-testid="harga-color-Bening"]')
        .dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });
    await flush();
    await flushCalc();
    expect(container.querySelector('[data-testid="harga-estimate-success"]').textContent)
      .toContain("Rp 1,40");
    expect(container.querySelector('[data-testid="harga-tier-block"]').textContent)
      .toContain("Rp 1,40");
    expect(container.querySelector('[data-testid="harga-tier-block"]').textContent)
      .toContain("Rp 2.100");
  });
});
