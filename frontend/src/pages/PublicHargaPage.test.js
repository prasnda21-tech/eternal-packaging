import { act } from "react";
import { createRoot } from "react-dom/client";
import api from "../lib/api";
import PublicHargaPage from "./PublicHargaPage";

jest.mock("../lib/api", () => ({
  __esModule: true,
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
    variants: [
      { public_variant_id: "variant-12", display_name: "12oz", unit: "pcs" },
      { public_variant_id: "variant-16", display_name: "16oz", unit: "pcs" },
    ],
  },
  {
    public_id: "group-paper-bag",
    name: "Paper Bag",
    variants: [
      { public_variant_id: "variant-small", display_name: "Small", unit: "pcs" },
    ],
  },
];

const cupDetail = {
  public_id: "group-paper-cup",
  name: "Paper Cup",
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

const bagDetail = {
  public_id: "group-paper-bag",
  name: "Paper Bag",
  variants: [
    {
      public_variant_id: "variant-small",
      display_name: "Small",
      unit: "pcs",
      minimum_quantity: 500,
      tiers: [{ min_qty: 500, max_qty: null, unit_price: 1800 }],
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

describe("PublicHargaPage", () => {
  let container;
  let root;

  beforeEach(() => {
    container = document.createElement("div");
    document.body.appendChild(container);
    root = createRoot(container);
    api.publicCustomerPricingGroups.mockResolvedValue({ groups });
    api.publicCustomerPricingGroup.mockImplementation((id) => (
      Promise.resolve({ group: id === "group-paper-bag" ? bagDetail : cupDetail })
    ));
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
  });

  afterEach(async () => {
    await act(async () => root.unmount());
    container.remove();
    jest.clearAllMocks();
  });

  test("renders the anonymous catalog without an internal shell or internal pricing labels", async () => {
    await act(async () => root.render(<PublicHargaPage />));
    await flush();
    expect(container.querySelector('[data-testid="public-harga-page"]')).not.toBeNull();
    expect(container.querySelector(".app-shell")).toBeNull();
    expect(container.textContent).toContain("Eternal Packaging");
    expect(container.textContent).toContain("Paper Cup");
    expect(container.textContent).toContain("Paper Bag");
    expect(container.textContent).toContain("12oz");
    expect(container.textContent).toContain("1.000–2.999 pcs");
    const publicText = container.textContent.toLowerCase();
    for (const internalLabel of ["hpp", "profit", "draft", "published", "margin", "markup"]) {
      expect(publicText).not.toContain(internalLabel);
    }
  });

  test("changes group and variant and always clears an old backend result", async () => {
    await act(async () => root.render(<PublicHargaPage />));
    await flush();
    const variantSelect = container.querySelector('[data-testid="harga-variant-select"]');
    await act(async () => setNativeValue(variantSelect, "variant-16"));
    expect(variantSelect.value).toBe("variant-16");
    expect(container.textContent).toContain("Rp 2.800");

    const bagButton = container.querySelector(
      '[data-testid="harga-group-group-paper-bag"]'
    );
    await act(async () => bagButton.dispatchEvent(
      new MouseEvent("click", { bubbles: true })
    ));
    await flush();
    expect(api.publicCustomerPricingGroup).toHaveBeenLastCalledWith("group-paper-bag");
    expect(container.querySelector('[data-testid="harga-variant-select"]').value)
      .toBe("variant-small");
    expect(container.querySelector('[data-testid="harga-quantity-input"]').placeholder)
      .toBe("Minimal 500");
  });

  test("submits quantity and renders unit price total and tier only from backend response", async () => {
    await act(async () => root.render(<PublicHargaPage />));
    await flush();
    const quantity = container.querySelector('[data-testid="harga-quantity-input"]');
    await act(async () => setNativeValue(quantity, "5000"));
    const button = container.querySelector('[data-testid="harga-calculate-button"]');
    await act(async () => button.dispatchEvent(
      new MouseEvent("click", { bubbles: true })
    ));
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
  });

  test("shows backend MOQ errors safely", async () => {
    api.calculatePublicCustomerPrice.mockRejectedValueOnce({
      response: { status: 422, data: {
        detail: "Minimum pemesanan untuk produk ini adalah 1.000 pcs.",
      } },
    });
    await act(async () => root.render(<PublicHargaPage />));
    await flush();
    const quantity = container.querySelector('[data-testid="harga-quantity-input"]');
    await act(async () => setNativeValue(quantity, "999"));
    const button = container.querySelector('[data-testid="harga-calculate-button"]');
    await act(async () => button.dispatchEvent(
      new MouseEvent("click", { bubbles: true })
    ));
    await flush();
    expect(container.querySelector('[data-testid="harga-calculation-error"]').textContent)
      .toContain("Minimum pemesanan");
  });

  test("shows catalog loading and safe error states", async () => {
    let rejectCatalog;
    api.publicCustomerPricingGroups.mockReturnValueOnce(
      new Promise((_resolve, reject) => { rejectCatalog = reject; })
    );
    await act(async () => root.render(<PublicHargaPage />));
    expect(container.querySelector('[data-testid="public-harga-loading"]')).not.toBeNull();
    await act(async () => rejectCatalog({
      response: { status: 503, data: { detail: "Harga produk tidak dapat dimuat." } },
    }));
    await flush();
    expect(container.querySelector('[data-testid="public-harga-error"]').textContent)
      .toContain("Harga produk tidak dapat dimuat.");
  });

  test("shows the friendly empty state", async () => {
    api.publicCustomerPricingGroups.mockResolvedValueOnce({ groups: [] });
    await act(async () => root.render(<PublicHargaPage />));
    await flush();
    expect(container.querySelector('[data-testid="public-harga-empty"]').textContent)
      .toContain("Harga produk belum tersedia.");
  });
});
