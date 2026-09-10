import { act } from "react";
import { createRoot } from "react-dom/client";
import api from "../lib/api";
import MobileOrderDetailPage from "./MobileOrderDetailPage";

const mockHasPerm = jest.fn(() => false);
const mockSetParams = jest.fn();

jest.mock("react-router-dom", () => ({
  useParams: () => ({ id: "po-1" }),
  useNavigate: () => jest.fn(),
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
  default: ({ title }) => <header>{title}</header>,
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
  default: () => <div data-testid="ws-packing" />,
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
});
