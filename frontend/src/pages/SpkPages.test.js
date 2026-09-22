import { act } from "react";
import { createRoot } from "react-dom/client";
import SpkCreate from "./SpkCreate";
import SpkDashboard from "./SpkDashboard";
import SpkDetail from "./SpkDetail";

const mockHasPerm = jest.fn();
const mockNavigate = jest.fn();
const mockApi = {
  spkContext: jest.fn(),
  listSpk: jest.fn(),
  createSpk: jest.fn(),
  getSpk: jest.fn(),
  acknowledgeSpk: jest.fn(),
  startSpk: jest.fn(),
  updateSpkProgress: jest.fn(),
  requestSpkVerification: jest.fn(),
  verifySpk: jest.fn(),
  cancelSpk: jest.fn(),
  uploadSpkFile: jest.fn(),
};

jest.mock("react-router-dom", () => ({
  useNavigate: () => mockNavigate,
  useParams: () => ({ id: "spk-1" }),
}), { virtual: true });

jest.mock("../lib/auth", () => ({
  useAuth: () => ({
    user: { id: "u1", name: "Fahdo", role: "crm" },
    hasPerm: mockHasPerm,
  }),
}));

jest.mock("../lib/api", () => ({
  __esModule: true,
  default: {
    spkContext: (...args) => mockApi.spkContext(...args),
    listSpk: (...args) => mockApi.listSpk(...args),
    createSpk: (...args) => mockApi.createSpk(...args),
    getSpk: (...args) => mockApi.getSpk(...args),
    acknowledgeSpk: (...args) => mockApi.acknowledgeSpk(...args),
    startSpk: (...args) => mockApi.startSpk(...args),
    updateSpkProgress: (...args) => mockApi.updateSpkProgress(...args),
    requestSpkVerification: (...args) => mockApi.requestSpkVerification(...args),
    verifySpk: (...args) => mockApi.verifySpk(...args),
    cancelSpk: (...args) => mockApi.cancelSpk(...args),
    uploadSpkFile: (...args) => mockApi.uploadSpkFile(...args),
  },
}));

global.IS_REACT_ACT_ENVIRONMENT = true;

const flush = async () => {
  await act(async () => {
    await new Promise((resolve) => setTimeout(resolve, 0));
  });
};

const ORDERS = [
  {
    id: "spk-overdue",
    spk_number: "SPK-20260901-001",
    title: "Perbanyak aktivitas iklan Eternal",
    source_division_label: "Direksi",
    target_division_label: "Penjualan",
    pic_label: "Fahdo",
    deadline: "2026-09-06",
    deadline_state: "OVERDUE",
    progress_percent: "38.00",
    workflow_status: "IN_PROGRESS",
    last_activity_at: "2026-09-05T08:21:00Z",
  },
  {
    id: "spk-verify",
    spk_number: "SPK-20260901-002",
    title: "Laporan evaluasi campaign",
    source_division_label: "Direksi",
    target_division_label: "Penjualan",
    pic_label: "Fahdo",
    deadline: "2026-09-12",
    deadline_state: "ON_TRACK",
    progress_percent: "100.00",
    workflow_status: "WAITING_VERIFICATION",
    last_activity_at: "2026-09-07T08:10:00Z",
  },
];

function render(node) {
  const container = document.createElement("div");
  document.body.appendChild(container);
  const root = createRoot(container);
  act(() => { root.render(node); });
  return { container, root };
}

describe("SPK pages", () => {
  beforeEach(() => {
    mockHasPerm.mockReset();
    mockNavigate.mockReset();
    Object.values(mockApi).forEach((fn) => fn.mockReset());
    mockApi.spkContext.mockResolvedValue({
      context: {
        divisions: [{ id: "penjualan", label: "Penjualan" }],
        pics: [{ id: "pic-1", name: "Fahdo", divisions: ["penjualan"] }],
        branch_name: "Surabaya",
      },
    });
    mockApi.listSpk.mockResolvedValue({
      orders: ORDERS,
      summary: { active: 4, due_soon: 1, overdue: 2, waiting_verification: 1, completed_this_month: 3 },
      tabs: ["mine", "overdue", "verification"],
    });
  });

  test("dashboard hides Semua SPK and shows overdue state", async () => {
    mockHasPerm.mockImplementation((key) => key === "spk.view_own" || key === "nav.spk" || key === "spk.update_progress");
    const { container, root } = render(<SpkDashboard />);
    await flush();
    expect(container.querySelector('[data-testid="spk-tab-all"]')).toBeNull();
    expect(container.querySelector('[data-testid="spk-tab-division"]')).toBeNull();
    expect(container.querySelector('[data-testid="spk-tab-mine"]')).not.toBeNull();
    expect(container.querySelector('[data-testid="spk-create"]')).toBeNull();
    const overdue = container.querySelector('[data-testid="spk-row-spk-overdue"]');
    expect(overdue.className).toContain("tone-overdue");
    expect(container.querySelector('[data-testid="spk-row-spk-verify"]').className).toContain("tone-verify");
    expect(container.querySelector('[data-testid="spk-count-overdue"]').textContent).toBe("2");
    act(() => root.unmount());
  });

  test("create form builds milestones and shows weight total", async () => {
    mockHasPerm.mockImplementation((key) => key === "spk.create" || key === "nav.spk");
    const { container, root } = render(<SpkCreate />);
    await flush();
    expect(container.querySelector('[data-testid="spk-create-page"]')).not.toBeNull();
    expect(container.querySelector('[data-testid="spk-weight-total"]').textContent).toContain("100");
    const title = container.querySelector('[data-testid="spk-ms-title"]');
    const target = container.querySelector('[data-testid="spk-ms-target"]');
    await act(async () => {
      title.value = "Materi iklan baru";
      title.dispatchEvent(new Event("input", { bubbles: true }));
      target.value = "10";
      target.dispatchEvent(new Event("input", { bubbles: true }));
    });
    const add = container.querySelector('[data-testid="spk-add-milestone"]');
    await act(async () => { add.dispatchEvent(new MouseEvent("click", { bubbles: true })); });
    expect(container.querySelectorAll('[data-testid="spk-milestone-row"]').length).toBe(2);
    expect(container.querySelector('[data-testid="spk-publish"]')).not.toBeNull();
    expect(container.querySelector('[data-testid="spk-save-draft"]')).not.toBeNull();
    act(() => root.unmount());
  });

  test("detail progress drawer and verification buttons follow capability", async () => {
    mockApi.getSpk.mockResolvedValue({
      order: {
        id: "spk-1",
        spk_number: "SPK-20260901-004",
        title: "Perbanyak aktivitas iklan Eternal",
        priority: "HIGH",
        workflow_status: "IN_PROGRESS",
        deadline: "2026-09-10",
        deadline_state: "DUE_SOON",
        progress_percent: "60.00",
        doc_version: 4,
        instruction: "Naikkan iklan",
        source_division_label: "Direksi",
        target_division_label: "Penjualan",
        pic_label: "Fahdo",
        milestones: [
          {
            id: "ms-1",
            title: "Campaign aktif",
            type: "QUANTITY",
            weight: "100",
            target_value: "5",
            actual_value: "3",
            unit: "campaign",
            status: "IN_PROGRESS",
            progress_percent: "60.00",
            evidence_count: 1,
            requires_evidence: true,
          },
        ],
        events: [],
        attachments: [],
        capabilities: {
          acknowledge: false,
          start: false,
          update_progress: true,
          request_verification: false,
          verify: false,
          cancel: false,
        },
      },
    });
    const { container, root } = render(<SpkDetail />);
    await flush();
    expect(container.querySelector('[data-testid="spk-detail-tab-progress"]')).not.toBeNull();
    expect(container.querySelector('[data-testid="spk-approve"]')).toBeNull();
    expect(container.querySelector('[data-testid="spk-revision"]')).toBeNull();
    expect(container.querySelector('[data-testid="spk-cancel"]')).toBeNull();
    expect(container.querySelector('[data-testid="spk-ack"]')).toBeNull();
    const update = container.querySelector('[data-testid="spk-update-ms-1"]');
    expect(update).not.toBeNull();
    await act(async () => { update.dispatchEvent(new MouseEvent("click", { bubbles: true })); });
    expect(container.querySelector('[data-testid="spk-progress-drawer"]')).not.toBeNull();
    const input = container.querySelector('[data-testid="spk-new-actual"]');
    await act(async () => {
      const setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, "value").set;
      setter.call(input, "4");
      input.dispatchEvent(new Event("input", { bubbles: true }));
    });
    expect(container.querySelector('[data-testid="spk-progress-preview"]').textContent).toContain("60%");
    expect(container.querySelector('[data-testid="spk-progress-preview"]').textContent).toContain("80%");
    expect(container.querySelector('[data-testid="spk-save-progress"]')).not.toBeNull();
    act(() => root.unmount());
  });
});
