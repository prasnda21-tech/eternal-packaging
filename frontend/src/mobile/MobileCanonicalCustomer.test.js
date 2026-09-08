import { act } from "react";
import { createRoot } from "react-dom/client";
import api from "../lib/api";
import MobilePOCard from "./components/MobilePOCard";
import MobileTaskCard from "./components/MobileTaskCard";
import MobileDeliveryWorkspace from "./workspaces/MobileDeliveryWorkspace";
import MobileOrderDetailPage from "./MobileOrderDetailPage";
import MobileOrdersPage from "./MobileOrdersPage";
import { PRODUCTION_SCHEDULE_COLUMN_IDS } from "../lib/productionScheduleTable";

const mockHasPerm = jest.fn(() => true);
const mockSearch = { ws: "delivery" };
const mockNavigate = jest.fn();

jest.mock("react-router-dom", () => ({
  Link: ({ to, children, ...props }) => <a href={to} {...props}>{children}</a>,
  useParams: () => ({ id: "po-abc" }),
  useNavigate: () => mockNavigate,
  useLocation: () => ({ state: null, pathname: "/m/orders/po-abc" }),
  useSearchParams: () => [new URLSearchParams(mockSearch.ws ? `ws=${mockSearch.ws}` : ""), jest.fn()],
}), { virtual: true });

jest.mock("../lib/auth", () => ({
  useAuth: () => ({ hasPerm: (perm) => mockHasPerm(perm) }),
}));

jest.mock("../lib/api", () => ({
  __esModule: true,
  default: {
    getOrder: jest.fn(),
    listOrders: jest.fn(),
  },
  fileUrl: (p) => p,
}));

global.IS_REACT_ACT_ENVIRONMENT = true;

const flush = async () => {
  await act(async () => {
    await new Promise((resolve) => setTimeout(resolve, 0));
  });
};

const poAbc = {
  id: "po-abc",
  po_number: "PO-ABC",
  customer: "PT ABC",
  customer_name: "",
  outlet: "Yogyakarta",
  address: "Jl. Tegal Melati No. 1 Yogyakarta",
  item: "Cup 16oz",
  current_stage: 13,
  deadline: "2026-09-10",
  line_items: [{ name: "Cup 16oz", quantity: 1000 }],
};

describe("PWA canonical customer display", () => {
  let container;
  let root;

  beforeEach(() => {
    container = document.createElement("div");
    document.body.appendChild(container);
    root = createRoot(container);
    mockHasPerm.mockReset();
    mockHasPerm.mockReturnValue(true);
    mockSearch.ws = "delivery";
    api.getOrder.mockReset();
    api.listOrders.mockReset();
  });

  afterEach(() => {
    act(() => root.unmount());
    container.remove();
  });

  test("A. PO detail Customer uses customer, not outlet", async () => {
    api.getOrder.mockResolvedValue({ order: poAbc });
    mockSearch.ws = "screener";
    await act(async () => { root.render(<MobileOrderDetailPage />); });
    await flush();
    const cell = container.querySelector('[data-testid="mobile-order-customer"]');
    expect(cell.textContent).toContain("Customer");
    expect(cell.textContent).toContain("PT ABC");
    expect(cell.textContent).not.toContain("Yogyakarta");
  });

  test("B. Driver panel Customer is canonical while address keeps location", () => {
    act(() => {
      root.render(<MobileDeliveryWorkspace order={poAbc} canEdit={false} onUpdated={() => {}} />);
    });
    expect(container.querySelector('[data-testid="mobile-delivery-customer"]').textContent).toBe("PT ABC");
    expect(container.textContent).toContain("Jl. Tegal Melati No. 1 Yogyakarta");
    expect(container.querySelector('a[href*="google.com/maps"]')).toBeTruthy();
  });

  test("C. Task card customer wins over outlet", () => {
    act(() => {
      root.render(
        <MobileTaskCard
          workspaceId="print"
          task={{
            task_id: "operator:po-abc:0:-",
            order_id: "po-abc",
            order_number: "PO-ABC",
            customer: "PT ABC",
            customer_name: "Other",
            outlet: "Yogyakarta",
            item: "Cup",
            target_qty: 10,
            completed_qty: 0,
            status: "belum",
          }}
        />,
      );
    });
    expect(container.querySelector(".mop-card-title").textContent).toBe("PT ABC");
    expect(container.querySelector(".mop-card-title").textContent).not.toContain("Yogyakarta");
  });

  test("D. Orders list card customer wins over outlet", () => {
    act(() => {
      root.render(<MobilePOCard order={poAbc} />);
    });
    expect(container.querySelector(".mop-card-title").textContent).toBe("PT ABC");
    expect(container.querySelector(".mop-card-title").textContent).not.toContain("Yogyakarta");
  });

  test.each([
    ["E. Screener", "screener"],
    ["F. Helper", "prep"],
    ["G. Operator", "print"],
    ["H. Loading", "loading"],
    ["I. Packing", "packing"],
    ["J. Pengiriman", "delivery"],
  ])("%s panel header uses canonical customer", async (_label, ws) => {
    api.getOrder.mockResolvedValue({ order: poAbc });
    mockSearch.ws = ws;
    await act(async () => { root.render(<MobileOrderDetailPage />); });
    await flush();
    expect(container.querySelector('[data-testid="mobile-order-customer"]').textContent).toContain("PT ABC");
    expect(container.querySelector('[data-testid="mobile-order-customer"]').textContent).not.toContain("Yogyakarta");
  });

  test("K. customer_name is used when customer is missing", () => {
    act(() => {
      root.render(<MobilePOCard order={{ ...poAbc, customer: "", customer_name: "PT ABC" }} />);
    });
    expect(container.querySelector(".mop-card-title").textContent).toBe("PT ABC");
  });

  test("L. missing canonical customer does not fall back to outlet", () => {
    act(() => {
      root.render(<MobilePOCard order={{ ...poAbc, customer: "", customer_name: "" }} />);
    });
    expect(container.querySelector(".mop-card-title").textContent).toBe("—");
    expect(container.querySelector(".mop-card-title").textContent).not.toBe("Yogyakarta");
  });

  test("M. schedule keeps separate Customer and Outlet columns", () => {
    expect(PRODUCTION_SCHEDULE_COLUMN_IDS).toContain("customer");
    expect(PRODUCTION_SCHEDULE_COLUMN_IDS).toContain("outlet");
    expect(PRODUCTION_SCHEDULE_COLUMN_IDS.indexOf("customer")).toBeLessThan(
      PRODUCTION_SCHEDULE_COLUMN_IDS.indexOf("outlet"),
    );
  });

  test("orders search matches canonical customer, not outlet", async () => {
    api.listOrders.mockResolvedValue({
      orders: [{ ...poAbc, customer_name: "" }],
    });
    await act(async () => { root.render(<MobileOrdersPage />); });
    await flush();
    const input = container.querySelector('[data-testid="mobile-orders-search"]');
    await act(async () => {
      const setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, "value").set;
      setter.call(input, "Yogyakarta");
      input.dispatchEvent(new Event("input", { bubbles: true }));
    });
    expect(container.querySelector('[data-testid="mobile-po-PO-ABC"]')).toBeFalsy();
    await act(async () => {
      const setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, "value").set;
      setter.call(input, "PT ABC");
      input.dispatchEvent(new Event("input", { bubbles: true }));
    });
    expect(container.querySelector('[data-testid="mobile-po-PO-ABC"]')).toBeTruthy();
  });
});
