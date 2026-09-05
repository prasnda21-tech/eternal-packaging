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
    publicCustomerPricingGroups: jest.fn(),
    publicCustomerPricingGroup: jest.fn(),
    calculatePublicCustomerPrice: jest.fn(),
  },
}));

global.IS_REACT_ACT_ENVIRONMENT = true;

const groups = [
  {
    public_id: "group-paper-cup",
    name: "Paper Cup",
    public_description: "Paper cup premium untuk kebutuhan minuman.",
    image_url: "/api/public/customer-pricing/groups/group-paper-cup/image",
    variants: [
      { public_variant_id: "variant-12", display_name: "12oz", unit: "pcs" },
      { public_variant_id: "variant-16", display_name: "16oz", unit: "pcs" },
    ],
  },
  {
    public_id: "group-paper-bag",
    name: "Paper Bag",
    public_description: null,
    image_url: null,
    variants: [
      { public_variant_id: "variant-small", display_name: "Small", unit: "pcs" },
    ],
  },
];

const cupDetail = {
  ...groups[0],
  variants: [
    {
      public_variant_id: "variant-12",
      display_name: "12oz",
      unit: "pcs",
      minimum_quantity: 1000,
      tiers: [
        { min_qty: 1000, max_qty: 2999, unit_price: 2500 },
        { min_qty: 3000, max_qty: 4999, unit_price: 2300 },
        { min_qty: 5000, max_qty: null, unit_price: 2100 },
      ],
    },
    {
      public_variant_id: "variant-16",
      display_name: "16oz",
      unit: "pcs",
      minimum_quantity: 1000,
      tiers: [{ min_qty: 1000, max_qty: null, unit_price: 2800 }],
    },
  ],
};

const flush = async () => {
  await act(async () => {
    await new Promise((resolve) => setTimeout(resolve, 0));
  });
};

const setNativeValue = (element, value) => {
  const setter = Object.getOwnPropertyDescriptor(
    Object.getPrototypeOf(element), "value"
  ).set;
  setter.call(element, value);
  element.dispatchEvent(new Event("input", { bubbles: true }));
  element.dispatchEvent(new Event("change", { bubbles: true }));
};

describe("PublicHargaPage catalog and detail", () => {
  let container;
  let root;

  const renderAt = async (pathName) => {
    mockParams = pathName === "/harga"
      ? {}
      : { groupPublicId: pathName.replace("/harga/", "") };
    await act(async () => root.render(<PublicHargaPage />));
    await flush();
  };

  beforeEach(() => {
    container = document.createElement("div");
    document.body.appendChild(container);
    root = createRoot(container);
    api.publicCustomerPricingGroups.mockResolvedValue({ groups });
    api.publicCustomerPricingGroup.mockResolvedValue({ group: cupDetail });
    api.calculatePublicCustomerPrice.mockResolvedValue({
      group: { public_id: "group-paper-cup", name: "Paper Cup" },
      variant: {
        public_variant_id: "variant-12",
        display_name: "12oz",
        unit: "pcs",
      },
      quantity: 5000,
      selected_tier: { min_qty: 5000, max_qty: null },
      unit_price: 2199,
      total: 10995000,
      currency: "IDR",
      calculated_at: "2026-09-04T00:00:00+00:00",
    });
    mockNavigate.mockReset();
    mockParams = {};
  });

  afterEach(async () => {
    await act(async () => root.unmount());
    container.remove();
    jest.clearAllMocks();
  });

  test("/harga renders a catalog first with image and intentional placeholder cards", async () => {
    await renderAt("/harga");
    expect(container.querySelector('[data-testid="public-harga-page"]')).not.toBeNull();
    expect(container.querySelector(".app-shell")).toBeNull();
    expect(container.textContent).toContain("Cek Harga Produk");
    expect(container.textContent).toContain("Paper Cup");
    expect(container.textContent).toContain("Paper Bag");
    expect(container.textContent).toContain("Paper cup premium");
    expect(container.textContent).toContain("2 varian");
    expect(container.querySelector('img[alt="Paper Cup"]').src).toContain(
      "/api/public/customer-pricing/groups/group-paper-cup/image"
    );
    expect(container.querySelectorAll('[data-testid="harga-image-placeholder"]').length)
      .toBe(1);
    expect(container.querySelector('[data-testid="harga-quantity-input"]')).toBeNull();
    expect(api.publicCustomerPricingGroup).not.toHaveBeenCalled();
  });

  test("clicking the full catalog card navigates to the opaque detail route", async () => {
    await renderAt("/harga");
    const card = container.querySelector('[data-testid="harga-group-group-paper-cup"]');
    await act(async () => card.dispatchEvent(new MouseEvent("click", { bubbles: true })));
    expect(mockNavigate).toHaveBeenCalledWith("/harga/group-paper-cup");
  });

  test("detail loads presentation data, chips, quantity, result, and backend tier highlight", async () => {
    await renderAt("/harga/group-paper-cup");
    expect(api.publicCustomerPricingGroup).toHaveBeenCalledWith("group-paper-cup");
    expect(container.textContent).toContain("Paper cup premium");
    expect(container.querySelector('[data-testid="harga-detail-image"] img')).not.toBeNull();

    const first = container.querySelector('[data-testid="harga-variant-variant-12"]');
    const second = container.querySelector('[data-testid="harga-variant-variant-16"]');
    expect(first.getAttribute("aria-pressed")).toBe("true");
    await act(async () => second.dispatchEvent(new MouseEvent("click", { bubbles: true })));
    expect(second.getAttribute("aria-pressed")).toBe("true");
    expect(container.textContent).toContain("Rp 2.800");
    await act(async () => first.dispatchEvent(new MouseEvent("click", { bubbles: true })));

    const quantity = container.querySelector('[data-testid="harga-quantity-input"]');
    await act(async () => setNativeValue(quantity, "5000"));
    const button = container.querySelector('[data-testid="harga-calculate-button"]');
    await act(async () => button.dispatchEvent(new MouseEvent("click", { bubbles: true })));
    await flush();
    expect(api.calculatePublicCustomerPrice).toHaveBeenCalledWith({
      group_public_id: "group-paper-cup",
      public_variant_id: "variant-12",
      quantity: 5000,
    });
    expect(container.querySelector('[data-testid="harga-unit-price"]').textContent)
      .toBe("Rp 2.199");
    expect(container.querySelector('[data-testid="harga-total"]').textContent)
      .toBe("Rp 10.995.000");
    expect(container.querySelector('[data-testid="harga-selected-tier"]').textContent)
      .toBe("5.000+ pcs");
    expect(container.querySelector('[data-testid="harga-tier-selected-row"]').textContent)
      .toContain("Rp 2.100");
  });

  test("backend validation errors are readable and no price is calculated locally", async () => {
    api.calculatePublicCustomerPrice.mockRejectedValueOnce({
      response: {
        status: 422,
        data: { detail: "Minimum pemesanan untuk produk ini adalah 1.000 pcs." },
      },
    });
    await renderAt("/harga/group-paper-cup");
    const quantity = container.querySelector('[data-testid="harga-quantity-input"]');
    await act(async () => setNativeValue(quantity, "999"));
    await act(async () => container
      .querySelector('[data-testid="harga-calculate-button"]')
      .dispatchEvent(new MouseEvent("click", { bubbles: true })));
    await flush();
    expect(container.querySelector('[data-testid="harga-calculation-error"]').textContent)
      .toContain("Minimum pemesanan");
    expect(container.querySelector('[data-testid="harga-result"]')).toBeNull();
  });

  test("invalid group and catalog failures use safe public states", async () => {
    api.publicCustomerPricingGroup.mockRejectedValueOnce({
      response: { status: 404, data: { detail: "Produk harga tidak tersedia." } },
    });
    await renderAt("/harga/not-valid");
    expect(container.querySelector('[data-testid="public-harga-not-found"]').textContent)
      .toContain("Produk tidak tersedia.");
    expect(container.textContent).not.toContain("[object Object]");
  });

  test("back to catalog uses public navigation and public DOM excludes internal pricing labels", async () => {
    await renderAt("/harga/group-paper-cup");
    const back = Array.from(container.querySelectorAll("a"))
      .find((anchor) => anchor.textContent.includes("Kembali ke Katalog"));
    expect(back.getAttribute("href")).toBe("/harga");
    const publicText = container.textContent.toLowerCase();
    for (const label of ["hpp", "profit", "draft", "published", "margin", "markup"]) {
      expect(publicText).not.toContain(label);
    }
    expect(container.querySelector(".app-shell")).toBeNull();
  });

  test("empty catalog and missing detail image use polished public placeholders", async () => {
    api.publicCustomerPricingGroups.mockResolvedValueOnce({ groups: [] });
    await renderAt("/harga");
    expect(container.querySelector('[data-testid="public-harga-empty"]').textContent)
      .toContain("Produk tidak tersedia.");
    expect(container.querySelector('[data-testid="harga-quantity-input"]')).toBeNull();

    api.publicCustomerPricingGroup.mockResolvedValueOnce({
      group: { ...cupDetail, image_url: null },
    });
    await renderAt("/harga/group-paper-cup");
    expect(container.querySelector('[data-testid="harga-detail-image"] img')).toBeNull();
    expect(
      container.querySelector('[data-testid="harga-detail-image"] [data-testid="harga-image-placeholder"]')
    ).not.toBeNull();
    expect(container.querySelector('[data-testid="harga-variant-buttons"]')).not.toBeNull();
  });

  test("product photos display own images, no-photo uses placeholder, and never fallback to group cover", async () => {
    const photoGroup = {
      public_id: "group-custom-box",
      name: "Custom Box",
      public_description: "Custom box packaging",
      image_url: "/api/public/customer-pricing/groups/group-custom-box/image",
      variants: [
        {
          public_variant_id: "var-box-a",
          display_name: "Box A",
          unit: "pcs",
          image_url: "/api/public/customer-pricing/groups/group-custom-box/variants/var-box-a/image",
        },
        {
          public_variant_id: "var-box-b",
          display_name: "Box B",
          unit: "pcs",
          image_url: null,
        },
      ],
    };
    const photoDetail = {
      ...photoGroup,
      variants: [
        {
          ...photoGroup.variants[0],
          minimum_quantity: 500,
          tiers: [{ min_qty: 500, max_qty: null, unit_price: 1500 }],
        },
        {
          ...photoGroup.variants[1],
          minimum_quantity: 500,
          tiers: [{ min_qty: 500, max_qty: null, unit_price: 1800 }],
        },
      ],
    };

    api.publicCustomerPricingGroups.mockResolvedValueOnce({ groups: [photoGroup] });
    await renderAt("/harga");

    const groupImg = container.querySelector('[data-testid="harga-card-image"] img');
    expect(groupImg.src).toContain(photoGroup.image_url);

    const varAThumb = container.querySelector('[data-testid="harga-variant-item-var-box-a"] img');
    expect(varAThumb).not.toBeNull();
    expect(varAThumb.src).toContain("var-box-a/image");

    const varBPlaceholder = container.querySelector('[data-testid="harga-variant-item-var-box-b"] [data-testid="harga-variant-placeholder"]');
    expect(varBPlaceholder).not.toBeNull();
    expect(container.querySelector('[data-testid="harga-variant-item-var-box-b"] img')).toBeNull();

    api.publicCustomerPricingGroup.mockResolvedValueOnce({ group: photoDetail });
    await renderAt("/harga/group-custom-box");

    const detailCover = container.querySelector('[data-testid="harga-detail-image"] img');
    expect(detailCover.src).toContain(photoGroup.image_url);

    const btnA = container.querySelector('[data-testid="harga-variant-var-box-a"] img');
    expect(btnA).not.toBeNull();
    expect(btnA.src).toContain("var-box-a/image");

    const btnB = container.querySelector('[data-testid="harga-variant-var-box-b"] [data-testid="harga-variant-placeholder"]');
    expect(btnB).not.toBeNull();
    expect(container.querySelector('[data-testid="harga-variant-var-box-b"] img')).toBeNull();

    const selectedBannerImg = container.querySelector('[data-testid="harga-selected-variant-card"] img');
    expect(selectedBannerImg.src).toContain("var-box-a/image");

    const btnBEl = container.querySelector('[data-testid="harga-variant-var-box-b"]');
    await act(async () => btnBEl.dispatchEvent(new MouseEvent("click", { bubbles: true })));
    const bannerPlaceholder = container.querySelector('[data-testid="harga-selected-variant-card"] [data-testid="harga-variant-placeholder"]');
    expect(bannerPlaceholder).not.toBeNull();
    expect(container.querySelector('[data-testid="harga-selected-variant-card"] img')).toBeNull();
  });
});
