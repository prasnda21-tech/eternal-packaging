import { act } from "react";
import { createRoot } from "react-dom/client";
import api from "../lib/api";
import MobileOrderDetailPage from "./MobileOrderDetailPage";

const mockHasPerm = jest.fn(() => false);
const mockSetParams = jest.fn();
const mockNavigate = jest.fn();

jest.mock("react-router-dom", () => ({
  useParams: () => ({ id: "po-1" }),
  useNavigate: () => mockNavigate,
  useLocation: () => ({ state: { fromTask: true } }),
  useSearchParams: () => [new URLSearchParams("ws=prep"), mockSetParams],
  Link: ({ to, children, ...props }) => <a href={to} {...props}>{children}</a>,
}), { virtual: true });

jest.mock("../lib/auth", () => ({
  useAuth: () => ({ hasPerm: (perm) => mockHasPerm(perm) }),
}));

jest.mock("../lib/api", () => ({
  __esModule: true,
  default: {
    getOrder: jest.fn(),
  },
}));

jest.mock("./MobileTopBar", () => ({
  __esModule: true,
  default: ({ kicker, title, meta, onBack }) => (
    <header data-testid="mobile-topbar">
      {onBack ? (
        <button type="button" data-testid="mobile-back" onClick={onBack}>Kembali</button>
      ) : null}
      {kicker ? <p data-testid="mobile-topbar-kicker">{kicker}</p> : null}
      {title ? <h1 data-testid="mobile-topbar-title">{title}</h1> : null}
      {meta ? <p data-testid="mobile-topbar-meta">{meta}</p> : null}
    </header>
  ),
}));

jest.mock("./components/MobileStageFlow", () => ({
  __esModule: true,
  default: () => <div data-testid="stage-flow" />,
}));

jest.mock("./workspaces/MobileHelperPrepWorkspace", () => ({
  __esModule: true,
  default: () => <div data-testid="ws-prep" />,
}));

jest.mock("./workspaces/MobilePackingWorkspace", () => ({
  __esModule: true,
  default: () => <div data-testid="ws-packing">packing-body</div>,
}));

jest.mock("./workspaces/MobileLoadingWorkspace", () => ({
  __esModule: true,
  default: () => <div data-testid="ws-loading" />,
}));

jest.mock("./workspaces/MobileScreenerWorkspace", () => ({
  __esModule: true,
  default: () => <div data-testid="ws-screener" />,
}));

jest.mock("./workspaces/MobilePrintWorkspace", () => ({
  __esModule: true,
  default: () => <div data-testid="ws-print" />,
}));

jest.mock("./workspaces/MobileSampleWorkspace", () => ({
  __esModule: true,
  default: () => <div data-testid="ws-sample" />,
}));

jest.mock("./workspaces/MobileDeliveryWorkspace", () => ({
  __esModule: true,
  default: () => <div data-testid="ws-delivery" />,
}));

global.IS_REACT_ACT_ENVIRONMENT = true;

const flush = async () => {
  await act(async () => {
    await new Promise((resolve) => setTimeout(resolve, 0));
  });
};

describe("MobileOrderDetailPage Ringkasan PO", () => {
  let container;
  let root;

  beforeEach(() => {
    container = document.createElement("div");
    document.body.appendChild(container);
    root = createRoot(container);
    mockHasPerm.mockReset();
    mockSetParams.mockReset();
    mockNavigate.mockReset();
    api.getOrder.mockReset();
    api.getOrder.mockResolvedValue({
      order: {
        id: "po-1",
        po_number: "PO-1",
        customer_name: "PT Example",
        total_quantity: 5000,
        deadline: "2026-09-12",
        line_items: [{ name: "Cup 16oz", quantity: 5000 }],
      },
    });
  });

  afterEach(() => {
    act(() => root.unmount());
    container.remove();
  });

  test("po.view shows Ringkasan PO and Pilih Tugas dropdown", async () => {
    mockHasPerm.mockImplementation((p) => ["po.view", "helper.prepare", "packing.update"].includes(p));
    await act(async () => { root.render(<MobileOrderDetailPage />); });
    await flush();
    expect(container.querySelector('[data-testid="mobile-po-ringkasan"]')).toBeTruthy();
    expect(container.querySelector('[data-testid="mobile-po-ringkasan-title"]').textContent).toBe("Ringkasan PO");
    expect(container.querySelector('[data-testid="mobile-workspace-select"]')).toBeTruthy();
    expect(container.textContent).toContain("Pilih Tugas");
    expect(container.querySelector(".mop-ws-tabs")).toBeFalsy();
  });

  test("without po.view Ringkasan PO is absent", async () => {
    mockHasPerm.mockImplementation((p) => p === "helper.prepare");
    await act(async () => { root.render(<MobileOrderDetailPage />); });
    await flush();
    expect(container.querySelector('[data-testid="mobile-po-ringkasan"]')).toBeFalsy();
    expect(container.querySelector('[data-testid="mobile-workspace-select"]')).toBeTruthy();
  });

  test("primary heading is customer; PO number is secondary meta", async () => {
    mockHasPerm.mockImplementation((p) => ["po.view", "helper.prepare"].includes(p));
    await act(async () => { root.render(<MobileOrderDetailPage />); });
    await flush();
    expect(container.querySelector('[data-testid="mobile-topbar-title"]').textContent).toBe("PT Example");
    expect(container.querySelector('[data-testid="mobile-topbar-meta"]').textContent).toBe("PO-1");
    expect(container.querySelector('[data-testid="mobile-topbar-kicker"]').textContent).toMatch(/Tugas Saya/);
    expect(container.querySelector('[data-testid="mobile-topbar-kicker"]').textContent).toMatch(/Persiapan/);
    expect(container.querySelector('[data-testid="mobile-back"]')).toBeTruthy();
    expect(container.querySelector('[data-testid="mobile-order-customer"]').textContent).toContain("PT Example");
    expect(container.textContent).toContain("Pilih Tugas");
  });

  test("prefers canonical customer field over customer_name", async () => {
    api.getOrder.mockResolvedValue({
      order: {
        id: "po-1",
        po_number: "PO-QA15-DFAC2C",
        customer: "TEST_QA reimport",
        customer_name: "Should Not Win",
        total_quantity: 1,
        deadline: "2026-09-12",
        line_items: [{ name: "Box", quantity: 1 }],
      },
    });
    mockHasPerm.mockImplementation((p) => ["po.view", "packing.update"].includes(p));
    await act(async () => { root.render(<MobileOrderDetailPage />); });
    await flush();
    expect(container.querySelector('[data-testid="mobile-topbar-title"]').textContent).toBe("TEST_QA reimport");
    expect(container.querySelector('[data-testid="mobile-topbar-meta"]').textContent).toBe("PO-QA15-DFAC2C");
    expect(container.querySelector('[data-testid="mobile-order-customer"] strong').textContent).toBe("TEST_QA reimport");
  });
});
