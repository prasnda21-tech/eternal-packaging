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
  spkFilters: jest.fn(),
  publishSpk: jest.fn(),
  deleteSpkDraft: jest.fn(),
  returnSpk: jest.fn(),
  addSpkNote: jest.fn(),
  correctSpkValue: jest.fn(),
  downloadSpkFile: jest.fn(),
  updateSpkDraft: jest.fn(),
};

jest.mock("react-router-dom", () => ({
  useNavigate: () => mockNavigate,
  useParams: () => ({ id: "spk-1" }),
  useLocation: () => ({ state: null, pathname: "/spk/spk-1" }),
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
    spkFilters: (...args) => mockApi.spkFilters(...args),
    publishSpk: (...args) => mockApi.publishSpk(...args),
    deleteSpkDraft: (...args) => mockApi.deleteSpkDraft(...args),
    returnSpk: (...args) => mockApi.returnSpk(...args),
    addSpkNote: (...args) => mockApi.addSpkNote(...args),
    correctSpkValue: (...args) => mockApi.correctSpkValue(...args),
    downloadSpkFile: (...args) => mockApi.downloadSpkFile(...args),
    updateSpkDraft: (...args) => mockApi.updateSpkDraft(...args),
  },
}));

global.IS_REACT_ACT_ENVIRONMENT = true;

const flush = async () => {
  await act(async () => {
    await new Promise((resolve) => setTimeout(resolve, 0));
  });
};

function setControl(element, value) {
  const proto = element instanceof HTMLTextAreaElement
    ? window.HTMLTextAreaElement.prototype
    : element instanceof HTMLSelectElement
      ? window.HTMLSelectElement.prototype
      : window.HTMLInputElement.prototype;
  Object.getOwnPropertyDescriptor(proto, "value").set.call(element, value);
  element.dispatchEvent(new Event(element instanceof HTMLSelectElement ? "change" : "input", { bubbles: true }));
}

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
    mockApi.spkFilters.mockResolvedValue({
      filters: {
        divisions: [{ id: "penjualan", label: "Penjualan" }],
        pics: [{ id: "pic-1", name: "Fahdo" }],
      },
    });
    mockApi.listSpk.mockResolvedValue({
      orders: ORDERS,
      summary: { active: 4, due_soon: 1, overdue: 2, waiting_verification: 1, completed_this_month: 3 },
      tabs: ["mine", "overdue", "verification"],
      page: 1,
      page_size: 20,
      total: 2,
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

  test("active card asks for the same statuses as the summary", async () => {
    mockHasPerm.mockImplementation((key) => key === "spk.view_all" || key === "nav.spk");
    const { container, root } = render(<SpkDashboard />);
    await flush();
    mockApi.listSpk.mockClear();
    await act(async () => {
      container.querySelector('[data-testid="spk-card-active"]').dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });
    await flush();
    const params = mockApi.listSpk.mock.calls.at(-1)[0];
    expect(params.status).toBe("PUBLISHED,ACKNOWLEDGED,IN_PROGRESS,REVISION_REQUIRED");
    expect(params.tab).toBe("all");
    expect(container.querySelector('[data-testid="spk-pagination"]')).not.toBeNull();
    act(() => root.unmount());
  });

  test("published PIC sees accept and return, and return dialog explains the whole SPK", async () => {
    mockApi.getSpk.mockResolvedValue({
      order: {
        id: "spk-1",
        spk_number: "SPK-202609-0001",
        title: "Tugas umum",
        priority: "NORMAL",
        workflow_status: "PUBLISHED",
        progress_percent: "0.00",
        doc_version: 2,
        instruction: "Kerjakan",
        source_division_label: "Direksi",
        target_division_label: "Penjualan",
        assigned_pics: [{ id: "a", name: "Fahdo" }, { id: "b", name: "Rina" }],
        milestones: [],
        events: [],
        attachments: [{ id: "file-1", kind: "file", name: "bukti.pdf" }],
        capabilities: { acknowledge: true, return_spk: true, start: false, update_progress: false, verify: false, cancel: false },
      },
    });
    const { container, root } = render(<SpkDetail />);
    await flush();
    expect(container.querySelector('[data-testid="spk-ack"]')).not.toBeNull();
    expect(container.querySelector('[data-testid="spk-return"]')).not.toBeNull();
    expect(container.querySelector('[data-testid="spk-start"]')).toBeNull();
    await act(async () => {
      container.querySelector('[data-testid="spk-return"]').dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });
    expect(container.querySelector('[data-testid="spk-confirm-return"]').textContent).toContain("seluruh SPK");
    const submit = container.querySelector('[data-testid="spk-confirm-submit"]');
    expect(submit.disabled).toBe(true);
    act(() => root.unmount());
  });

  test("draft actions and over-target label follow the document", async () => {
    mockApi.getSpk.mockResolvedValue({
      order: {
        id: "spk-1",
        spk_number: "SPK-202609-0002",
        title: "Draft tugas",
        priority: "LOW",
        workflow_status: "DRAFT",
        progress_percent: "0.00",
        doc_version: 1,
        milestones: [{ id: "ms-9", title: "Lebih", type: "QUANTITY", weight: "100", target_value: "5", actual_value: "8", unit: "unit", progress_percent: "100.00", exceeds_target: true }],
        events: [{ id: "e1", event_type: "CREATE", actor_name: "Dimas", created_at: "2026-09-01T00:00:00Z", note: "Draft tugas" }],
        attachments: [],
        capabilities: { edit_draft: true, publish: true, delete_draft: true, update_progress: false, verify: false, cancel: false },
      },
    });
    const { container, root } = render(<SpkDetail />);
    await flush();
    expect(container.querySelector('[data-testid="spk-edit-draft"]')).not.toBeNull();
    expect(container.querySelector('[data-testid="spk-publish-draft"]')).not.toBeNull();
    expect(container.querySelector('[data-testid="spk-delete-draft"]')).not.toBeNull();
    expect(container.querySelector('[data-testid="spk-exceeds-ms-9"]').textContent).toBe("Melebihi Target");
    await act(async () => {
      container.querySelector('[data-testid="spk-detail-tab-activity"]').dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });
    expect(container.querySelector('[data-testid="spk-activity"]').textContent).toContain("Draft dibuat");
    act(() => root.unmount());
  });

  test("progress save stays busy and does not send a second request", async () => {
    let release;
    mockApi.updateSpkProgress.mockImplementation(() => new Promise((resolve) => { release = resolve; }));
    mockApi.getSpk.mockResolvedValue({
      order: {
        id: "spk-1",
        spk_number: "SPK-202609-0004",
        title: "Berjalan",
        priority: "HIGH",
        workflow_status: "IN_PROGRESS",
        progress_percent: "60.00",
        doc_version: 4,
        milestones: [{ id: "ms-1", title: "Campaign aktif", type: "QUANTITY", weight: "100", target_value: "5", actual_value: "3", unit: "campaign", progress_percent: "60.00" }],
        events: [],
        attachments: [],
        capabilities: { update_progress: true, add_note: true, add_evidence: true, correct: true },
      },
    });
    const { container, root } = render(<SpkDetail />);
    await flush();
    await act(async () => {
      container.querySelector('[data-testid="spk-update-ms-1"]').dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });
    const save = container.querySelector('[data-testid="spk-save-progress"]');
    await act(async () => { save.dispatchEvent(new MouseEvent("click", { bubbles: true })); });
    expect(container.querySelector('[data-testid="spk-save-progress"]').disabled).toBe(true);
    await act(async () => {
      container.querySelector('[data-testid="spk-save-progress"]').dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });
    expect(mockApi.updateSpkProgress).toHaveBeenCalledTimes(1);
    await act(async () => { release({ order: { id: "spk-1", milestones: [], capabilities: {}, events: [], attachments: [], progress_percent: "80", workflow_status: "IN_PROGRESS", priority: "HIGH", title: "Berjalan", spk_number: "SPK" } }); });
    act(() => root.unmount());
  });

  test("correction dialog requires a reason and download uses the file endpoint", async () => {
    mockApi.downloadSpkFile.mockResolvedValue(new Blob(["pdf"]));
    const createObjectURL = jest.fn(() => "blob:local");
    const revoke = jest.fn();
    global.URL.createObjectURL = createObjectURL;
    global.URL.revokeObjectURL = revoke;
    mockApi.getSpk.mockResolvedValue({
      order: {
        id: "spk-1",
        spk_number: "SPK-202609-0005",
        title: "Koreksi",
        priority: "NORMAL",
        workflow_status: "IN_PROGRESS",
        progress_percent: "100.00",
        doc_version: 3,
        milestones: [{ id: "ms-1", title: "Unit", type: "QUANTITY", weight: "100", target_value: "5", actual_value: "8", unit: "unit", progress_percent: "100.00", exceeds_target: true }],
        events: [],
        attachments: [{ id: "file-9", kind: "file", name: "bukti.pdf" }],
        capabilities: { correct: true, update_progress: true },
      },
    });
    const { container, root } = render(<SpkDetail />);
    await flush();
    await act(async () => {
      container.querySelector('[data-testid="spk-correct-ms-1"]').dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });
    expect(container.querySelector('[data-testid="spk-correct-save"]').disabled).toBe(true);
    await act(async () => {
      container.querySelector('[data-testid="spk-detail-tab-files"]').dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });
    await act(async () => {
      container.querySelector('[data-testid="spk-download-file-9"]').dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });
    await flush();
    expect(mockApi.downloadSpkFile).toHaveBeenCalledWith("spk-1", "file-9");
    act(() => root.unmount());
  });

  test("non PIC including admin does not see return or progress actions", async () => {
    mockApi.getSpk.mockResolvedValue({
      order: {
        id: "spk-1",
        spk_number: "SPK-202609-0008",
        title: "Bukan tugas saya",
        priority: "NORMAL",
        workflow_status: "PUBLISHED",
        progress_percent: "0.00",
        doc_version: 2,
        assigned_pics: [{ id: "pic-1", name: "Fahdo" }, { id: "pic-2", name: "Rina" }],
        milestones: [{ id: "ms-1", title: "Unit", type: "QUANTITY", weight: "100", target_value: "5", actual_value: "0", unit: "unit", progress_percent: "0.00" }],
        events: [],
        attachments: [],
        capabilities: {
          acknowledge: false,
          return_spk: false,
          start: false,
          update_progress: false,
          add_evidence: false,
          correct: false,
        },
      },
    });
    const { container, root } = render(<SpkDetail />);
    await flush();
    await act(async () => {
      container.querySelector('[data-testid="spk-detail-tab-summary"]').dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });
    expect(container.querySelector('[data-testid="spk-pic-list"]').textContent).toContain("Fahdo, Rina");
    expect(container.querySelector('[data-testid="spk-return"]')).toBeNull();
    expect(container.querySelector('[data-testid="spk-ack"]')).toBeNull();
    expect(container.querySelector('[data-testid="spk-start"]')).toBeNull();
    expect(container.querySelector('[data-testid="spk-update-ms-1"]')).toBeNull();
    expect(container.querySelector('[data-testid="spk-correct-ms-1"]')).toBeNull();
    act(() => root.unmount());
  });

  test("accept reloads the document so the next action appears", async () => {
    mockApi.getSpk
      .mockResolvedValueOnce({
        order: {
          id: "spk-1",
          spk_number: "SPK-202609-0009",
          title: "Diterima",
          priority: "NORMAL",
          workflow_status: "PUBLISHED",
          progress_percent: "0.00",
          doc_version: 2,
          milestones: [],
          events: [],
          attachments: [],
          capabilities: { acknowledge: true, return_spk: true, start: false },
        },
      })
      .mockResolvedValueOnce({
        order: {
          id: "spk-1",
          spk_number: "SPK-202609-0009",
          title: "Diterima",
          priority: "NORMAL",
          workflow_status: "ACKNOWLEDGED",
          progress_percent: "0.00",
          doc_version: 3,
          milestones: [],
          events: [],
          attachments: [],
          capabilities: { acknowledge: false, return_spk: false, start: true },
        },
      });
    mockApi.acknowledgeSpk.mockResolvedValue({ order: { id: "spk-1", workflow_status: "ACKNOWLEDGED", capabilities: {} } });
    const { container, root } = render(<SpkDetail />);
    await flush();
    await act(async () => {
      container.querySelector('[data-testid="spk-ack"]').dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });
    await act(async () => {
      container.querySelector('[data-testid="spk-confirm-submit"]').dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });
    await flush();
    expect(mockApi.acknowledgeSpk).toHaveBeenCalledWith("spk-1", { doc_version: 2 });
    expect(mockApi.getSpk).toHaveBeenCalledTimes(2);
    expect(container.querySelector('[data-testid="spk-start"]')).not.toBeNull();
    expect(container.querySelector('[data-testid="spk-return"]')).toBeNull();
    act(() => root.unmount());
  });

  test("note and progress payloads carry an idempotency key and reload", async () => {
    mockApi.getSpk.mockResolvedValue({
      order: {
        id: "spk-1",
        spk_number: "SPK-202609-0010",
        title: "Catatan",
        priority: "NORMAL",
        workflow_status: "IN_PROGRESS",
        progress_percent: "0.00",
        doc_version: 4,
        milestones: [{ id: "ms-1", title: "Unit", type: "QUANTITY", weight: "100", target_value: "5", actual_value: "1", unit: "unit", progress_percent: "20.00" }],
        events: [],
        attachments: [],
        capabilities: { update_progress: true, add_note: true, add_evidence: true, correct: true },
      },
    });
    mockApi.addSpkNote.mockResolvedValue({ order: { id: "spk-1" } });
    mockApi.updateSpkProgress.mockResolvedValue({ order: { id: "spk-1" } });
    const { container, root } = render(<SpkDetail />);
    await flush();
    await act(async () => {
      container.querySelector('[data-testid="spk-add-note"]').dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });
    await act(async () => {
      setControl(container.querySelector('[data-testid="spk-note-text"]'), "Masih menyiapkan bahan");
    });
    await act(async () => {
      container.querySelector('[data-testid="spk-note-save"]').dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });
    await flush();
    const notePayload = mockApi.addSpkNote.mock.calls[0][1];
    expect(notePayload.note).toBe("Masih menyiapkan bahan");
    expect(notePayload.idempotency_key).toEqual(expect.any(String));
    expect(notePayload.idempotency_key.length).toBeGreaterThan(8);
    await act(async () => {
      container.querySelector('[data-testid="spk-update-ms-1"]').dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });
    await act(async () => {
      setControl(container.querySelector('[data-testid="spk-new-actual"]'), "3");
    });
    await act(async () => {
      container.querySelector('[data-testid="spk-save-progress"]').dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });
    await flush();
    const progressPayload = mockApi.updateSpkProgress.mock.calls[0][1];
    expect(progressPayload.new_actual).toBe("3");
    expect(progressPayload.milestone_id).toBe("ms-1");
    expect(progressPayload.idempotency_key).toEqual(expect.any(String));
    expect(mockApi.getSpk.mock.calls.length).toBeGreaterThanOrEqual(3);
    act(() => root.unmount());
  });

  test("filters combine and the next page requests a different page", async () => {
    mockHasPerm.mockImplementation((key) => key === "spk.view_all" || key === "nav.spk");
    mockApi.listSpk.mockResolvedValue({
      orders: ORDERS,
      summary: { active: 4, due_soon: 1, overdue: 2, waiting_verification: 1, completed_this_month: 3 },
      tabs: ["mine", "division", "all"],
      page: 1,
      page_size: 20,
      total: 40,
    });
    const { container, root } = render(<SpkDashboard />);
    await flush();
    await act(async () => {
      setControl(container.querySelector('[data-testid="spk-search"]'), "FILTER-KHUSUS");
      setControl(container.querySelector('[data-testid="spk-filter-status"]'), "PUBLISHED");
      setControl(container.querySelector('[data-testid="spk-filter-division"]'), "penjualan");
      setControl(container.querySelector('[data-testid="spk-filter-pic"]'), "pic-1");
      setControl(container.querySelector('[data-testid="spk-filter-priority"]'), "HIGH");
      setControl(container.querySelector('[data-testid="spk-filter-deadline"]'), "OVERDUE");
    });
    await flush();
    const params = mockApi.listSpk.mock.calls.at(-1)[0];
    expect(params.q).toBe("FILTER-KHUSUS");
    expect(params.status).toBe("PUBLISHED");
    expect(params.division).toBe("penjualan");
    expect(params.pic).toBe("pic-1");
    expect(params.priority).toBe("HIGH");
    expect(params.deadline_state).toBe("OVERDUE");
    expect(params.page).toBe(1);
    await act(async () => {
      container.querySelector('[data-testid="spk-page-next"]').dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });
    await flush();
    expect(mockApi.listSpk.mock.calls.at(-1)[0].page).toBe(2);
    await act(async () => {
      container.querySelector('[data-testid="spk-card-done"]').dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });
    await flush();
    const done = mockApi.listSpk.mock.calls.at(-1)[0];
    expect(done.completed_month).toBe("1");
    expect(done.status).toBe("");
    expect(done.page).toBe(1);
    act(() => root.unmount());
  });

  test("create keeps one idempotency key and submits every selected PIC", async () => {
    mockHasPerm.mockImplementation((key) => key === "spk.create" || key === "nav.spk");
    mockApi.spkContext.mockResolvedValue({
      context: {
        divisions: [{ id: "penjualan", label: "Penjualan" }],
        pics: [
          { id: "pic-1", name: "Fahdo", divisions: ["penjualan"] },
          { id: "pic-2", name: "Rina", divisions: ["penjualan"] },
        ],
        branch_name: "Surabaya",
      },
    });
    let release;
    mockApi.createSpk.mockImplementation(() => new Promise((resolve) => { release = resolve; }));
    const { container, root } = render(<SpkCreate />);
    await flush();
    expect(container.querySelector('[data-testid="spk-pic-summary"]').textContent).toBe("Pilih PIC");
    expect(document.querySelector('[data-testid="spk-pic-option"]')).toBeNull();
    await act(async () => {
      container.querySelector('[data-testid="spk-pic-trigger"]').dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });
    const boxes = document.querySelectorAll('[data-testid="spk-pic-option"] input');
    expect(boxes.length).toBe(2);
    await act(async () => {
      boxes[0].dispatchEvent(new MouseEvent("click", { bubbles: true }));
      boxes[1].dispatchEvent(new MouseEvent("click", { bubbles: true }));
      setControl(container.querySelector('[data-testid="spk-title"]'), "Tugas dua PIC");
    });
    const save = container.querySelector('[data-testid="spk-save-draft"]');
    await act(async () => { save.dispatchEvent(new MouseEvent("click", { bubbles: true })); });
    await act(async () => { save.dispatchEvent(new MouseEvent("click", { bubbles: true })); });
    expect(mockApi.createSpk).toHaveBeenCalledTimes(1);
    const payload = mockApi.createSpk.mock.calls[0][0];
    expect(payload.assigned_user_ids).toEqual(["pic-1", "pic-2"]);
    expect(payload.publish).toBe(false);
    expect(payload.idempotency_key).toEqual(expect.any(String));
    expect(payload.idempotency_key.length).toBeGreaterThan(8);
    await act(async () => { release({ order: { id: "spk-9", workflow_status: "DRAFT" } }); });
    act(() => root.unmount());
  });

  test("milestone rows stay in the page scroll and keep weight rules", async () => {
    mockHasPerm.mockImplementation((key) => key === "spk.create" || key === "nav.spk");
    mockApi.createSpk.mockResolvedValue({ order: { id: "spk-ms", workflow_status: "PUBLISHED" } });
    const { container, root } = render(<SpkCreate />);
    await flush();
    const wrap = container.querySelector(".spk-milestone-scroll");
    const add = container.querySelector('[data-testid="spk-add-milestone"]');
    expect(wrap).not.toBeNull();
    expect(wrap.className).toBe("spk-milestone-scroll");
    expect(wrap.getAttribute("style")).toBeNull();
    expect(wrap.contains(add)).toBe(false);
    expect(wrap.compareDocumentPosition(add) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(container.querySelectorAll('[data-testid="spk-milestone-row"]').length).toBe(1);
    expect(container.querySelector('[aria-label="Hapus milestone"]').disabled).toBe(true);

    await act(async () => {
      setControl(container.querySelector('[data-testid="spk-ms-title"]'), "Baris awal");
    });
    for (const count of [2, 5, 10, 12]) {
      while (container.querySelectorAll('[data-testid="spk-milestone-row"]').length < count) {
        await act(async () => {
          container.querySelector('[data-testid="spk-add-milestone"]').dispatchEvent(new MouseEvent("click", { bubbles: true }));
        });
      }
      const rows = container.querySelectorAll('[data-testid="spk-milestone-row"]');
      expect(rows.length).toBe(count);
      expect(rows[0].querySelector('[data-testid="spk-ms-title"]').value).toBe("Baris awal");
      expect(wrap.contains(rows[count - 1])).toBe(true);
      const addButton = container.querySelector('[data-testid="spk-add-milestone"]');
      expect(wrap.contains(addButton)).toBe(false);
      expect(wrap.compareDocumentPosition(addButton) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    }

    const titles = [...container.querySelectorAll('[data-testid="spk-ms-title"]')];
    await act(async () => {
      setControl(titles[1], "Baris hapus");
      setControl(titles[2], "Baris tetap");
    });
    const remove = [...container.querySelectorAll('[aria-label="Hapus milestone"]')][1];
    await act(async () => { remove.dispatchEvent(new MouseEvent("click", { bubbles: true })); });
    const after = [...container.querySelectorAll('[data-testid="spk-ms-title"]')].map((input) => input.value);
    expect(after).toHaveLength(11);
    expect(after[0]).toBe("Baris awal");
    expect(after).not.toContain("Baris hapus");
    expect(after[1]).toBe("Baris tetap");
    expect(container.querySelector('[data-testid="spk-weight-total"]').textContent).toContain("100");

    const weights = [...container.querySelectorAll('[data-testid="spk-ms-weight"]')];
    await act(async () => { setControl(weights[0], "70"); });
    expect(container.querySelector('[data-testid="spk-weight-total"]').textContent).toContain("70 / 100");
    expect(container.querySelector('[data-testid="spk-publish"]').disabled).toBe(true);
    await act(async () => { setControl(container.querySelector('[data-testid="spk-ms-weight"]'), "100"); });
    expect(container.querySelector('[data-testid="spk-weight-total"]').textContent).toContain("100 / 100");
    expect(container.querySelector('[data-testid="spk-publish"]').disabled).toBe(false);

    await act(async () => {
      container.querySelector('[data-testid="spk-ms-evidence"]').dispatchEvent(new MouseEvent("click", { bubbles: true }));
      setControl(container.querySelector('[data-testid="spk-ms-target"]'), "8");
      setControl(container.querySelector('[data-testid="spk-ms-unit"]'), "pcs");
      setControl(container.querySelector('[data-testid="spk-title"]'), "SPK milestone");
      setControl(container.querySelector('[data-testid="spk-priority"]'), "HIGH");
      setControl(container.querySelector('[data-testid="spk-start"]'), "2026-09-01");
      setControl(container.querySelector('[data-testid="spk-deadline"]'), "2026-09-30");
      setControl(container.querySelector('[data-testid="spk-instruction"]'), "Kerjakan bertahap");
    });
    expect(container.querySelector('[data-testid="spk-branch"]').textContent).toBe("Surabaya");
    await act(async () => {
      container.querySelector('[data-testid="spk-publish"]').dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });
    expect(container.querySelector('[data-testid="spk-publish-confirm"]')).not.toBeNull();
    await act(async () => {
      container.querySelector('[data-testid="spk-publish-confirm-submit"]').dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });
    await flush();
    const payload = mockApi.createSpk.mock.calls[0][0];
    expect(Object.keys(payload).sort()).toEqual([
      "assigned_user_ids",
      "deadline",
      "idempotency_key",
      "instruction",
      "milestones",
      "priority",
      "publish",
      "start_date",
      "target_division",
      "title",
    ]);
    expect(payload.publish).toBe(true);
    expect(payload.title).toBe("SPK milestone");
    expect(payload.priority).toBe("HIGH");
    expect(payload.target_division).toBe("penjualan");
    expect(payload.start_date).toBe("2026-09-01");
    expect(payload.deadline).toBe("2026-09-30");
    expect(payload.instruction).toBe("Kerjakan bertahap");
    expect(payload.assigned_user_ids).toEqual([]);
    expect(payload.milestones[0]).toEqual({
      id: undefined,
      title: "Baris awal",
      type: "QUANTITY",
      weight: "100",
      target_value: "8",
      unit: "pcs",
      requires_evidence: false,
    });
    expect(payload.milestones).toHaveLength(11);
    expect(payload.milestones.slice(1).every((row) => row.requires_evidence === true)).toBe(true);
    act(() => root.unmount());
  });

  test("PIC field is a checkbox dropdown capped at 12", async () => {
    mockHasPerm.mockImplementation((key) => key === "spk.create" || key === "nav.spk");
    mockApi.createSpk.mockResolvedValue({ order: { id: "spk-pic", workflow_status: "DRAFT" } });
    const penjualan = [
      { id: "pic-1", name: "Fahdo", divisions: ["penjualan"] },
      { id: "pic-2", name: "Rina", divisions: ["penjualan"] },
      ...Array.from({ length: 11 }, (_, index) => ({
        id: `pic-x-${index + 1}`,
        name: `Anggota ${String(index + 1).padStart(2, "0")}`,
        divisions: ["penjualan"],
      })),
    ];
    mockApi.spkContext.mockResolvedValue({
      context: {
        divisions: [
          { id: "penjualan", label: "Penjualan" },
          { id: "produksi", label: "Produksi" },
        ],
        pics: [...penjualan, { id: "pic-3", name: "Budi", divisions: ["produksi"] }],
        branch_name: "Surabaya",
      },
    });
    const { container, root } = render(<SpkCreate />);
    await flush();
    const trigger = () => container.querySelector('[data-testid="spk-pic-trigger"]');
    const summary = () => container.querySelector('[data-testid="spk-pic-summary"]').textContent;
    const options = () => [...document.querySelectorAll('[data-testid="spk-pic-option"]')];
    expect(summary()).toBe("Pilih PIC");
    expect(options()).toHaveLength(0);

    await act(async () => { trigger().dispatchEvent(new MouseEvent("click", { bubbles: true })); });
    expect(trigger().getAttribute("aria-expanded")).toBe("true");
    expect(document.getElementById("spk-pic-panel")).not.toBeNull();
    let names = options().map((option) => option.textContent.trim());
    expect(names).toContain("Fahdo");
    expect(names).toContain("Rina");
    expect(names).not.toContain("Budi");
    expect(new Set(names).size).toBe(names.length);
    expect(options().every((option) => option.querySelector('input[type="checkbox"]'))).toBe(true);

    const box = (name) => options().find((option) => option.textContent.includes(name)).querySelector("input");
    await act(async () => { box("Fahdo").dispatchEvent(new MouseEvent("click", { bubbles: true })); });
    expect(document.getElementById("spk-pic-panel")).not.toBeNull();
    expect(box("Fahdo").checked).toBe(true);
    expect(summary()).toBe("Fahdo");
    expect(container.querySelector('[data-testid="spk-pic-chip"]').textContent).toContain("Fahdo");

    await act(async () => { box("Rina").dispatchEvent(new MouseEvent("click", { bubbles: true })); });
    expect(document.getElementById("spk-pic-panel")).not.toBeNull();
    expect(summary()).toBe("2 PIC dipilih");
    expect(box("Fahdo").checked).toBe(true);
    expect(box("Rina").checked).toBe(true);
    expect(container.querySelectorAll('[data-testid="spk-pic-chip"]')).toHaveLength(2);

    await act(async () => { box("Fahdo").dispatchEvent(new MouseEvent("click", { bubbles: true })); });
    expect(summary()).toBe("Rina");
    expect(box("Fahdo").checked).toBe(false);
    await act(async () => { box("Fahdo").dispatchEvent(new MouseEvent("click", { bubbles: true })); });
    expect(summary()).toBe("2 PIC dipilih");
    expect(container.querySelectorAll('[data-testid="spk-pic-chip"]')).toHaveLength(2);

    const rinaChip = [...container.querySelectorAll('[data-testid="spk-pic-chip"]')].find((chip) => chip.textContent.includes("Rina"));
    expect(rinaChip.querySelector("button").getAttribute("aria-label")).toBe("Hapus Rina");
    await act(async () => { rinaChip.querySelector("button").dispatchEvent(new MouseEvent("click", { bubbles: true })); });
    expect(summary()).toBe("Fahdo");
    expect(box("Rina").checked).toBe(false);

    const search = document.querySelector('[data-testid="spk-pic-search"]');
    await act(async () => { setControl(search, "rina"); });
    expect(options().map((option) => option.textContent.trim())).toEqual(["Rina"]);
    await act(async () => { setControl(search, "ANGGOTA 01"); });
    expect(options().map((option) => option.textContent.trim())).toEqual(["Anggota 01"]);
    await act(async () => { setControl(search, "tidak-ada"); });
    expect(document.querySelector('[data-testid="spk-pic-empty"]').textContent).toBe("PIC tidak ditemukan");
    await act(async () => { setControl(search, ""); });

    let guard = 0;
    while (container.querySelectorAll('[data-testid="spk-pic-chip"]').length < 12) {
      const next = [...document.querySelectorAll('[data-testid="spk-pic-option"] input')].find((input) => !input.checked);
      if (!next || guard > 20) throw new Error("PIC selection did not reach 12");
      await act(async () => { next.dispatchEvent(new MouseEvent("click", { bubbles: true })); });
      guard += 1;
    }
    expect(summary()).toBe("12 PIC dipilih");
    expect(document.querySelector('[data-testid="spk-pic-max"]').textContent).toBe("Maksimal 12 PIC");
    const blocked = options().map((option) => option.querySelector("input")).find((input) => !input.checked);
    expect(blocked.disabled).toBe(true);
    await act(async () => { blocked.dispatchEvent(new MouseEvent("click", { bubbles: true })); });
    expect(summary()).toBe("12 PIC dipilih");
    expect(container.querySelectorAll('[data-testid="spk-pic-chip"]')).toHaveLength(12);

    const extraChip = container.querySelector('[data-testid="spk-pic-chip"] button');
    await act(async () => { extraChip.dispatchEvent(new MouseEvent("click", { bubbles: true })); });
    expect(summary()).toBe("11 PIC dipilih");
    expect(document.querySelector('[data-testid="spk-pic-max"]')).toBeNull();
    const reopened = options().map((option) => option.querySelector("input")).find((input) => !input.checked);
    expect(reopened.disabled).toBe(false);
    await act(async () => { reopened.dispatchEvent(new MouseEvent("click", { bubbles: true })); });
    expect(summary()).toBe("12 PIC dipilih");

    await act(async () => {
      document.body.dispatchEvent(new MouseEvent("mousedown", { bubbles: true }));
    });
    expect(document.getElementById("spk-pic-panel")).toBeNull();
    expect(trigger().getAttribute("aria-expanded")).toBe("false");
    await act(async () => { trigger().dispatchEvent(new MouseEvent("click", { bubbles: true })); });
    await act(async () => {
      document.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape", bubbles: true }));
    });
    expect(document.getElementById("spk-pic-panel")).toBeNull();

    const rowsBefore = container.querySelectorAll('[data-testid="spk-milestone-row"]').length;
    await act(async () => {
      container.querySelector('[data-testid="spk-add-milestone"]').dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });
    expect(container.querySelectorAll('[data-testid="spk-milestone-row"]').length).toBe(rowsBefore + 1);
    await act(async () => {
      [...container.querySelectorAll('[aria-label="Hapus milestone"]')].at(-1).dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });
    expect(container.querySelectorAll('[data-testid="spk-milestone-row"]').length).toBe(rowsBefore);

    await act(async () => { setControl(container.querySelector('[data-testid="spk-title"]'), "SPK PIC"); });
    await act(async () => {
      container.querySelector('[data-testid="spk-save-draft"]').dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });
    await flush();
    const payload = mockApi.createSpk.mock.calls[0][0];
    expect(payload.publish).toBe(false);
    expect(payload.target_division).toBe("penjualan");
    expect(payload.assigned_user_ids).toHaveLength(12);
    expect(new Set(payload.assigned_user_ids).size).toBe(12);
    expect(payload.assigned_user_ids).not.toContain("pic-3");
    expect(payload.assigned_user_ids.every((id) => penjualan.some((pic) => pic.id === id))).toBe(true);
    expect(payload.milestones).toHaveLength(1);
    expect(payload.milestones[0].requires_evidence).toBe(true);
    act(() => root.unmount());
  });

  test("changing target division drops PIC from the previous division", async () => {
    mockHasPerm.mockImplementation((key) => key === "spk.create" || key === "nav.spk");
    mockApi.createSpk
      .mockResolvedValueOnce({ order: { id: "spk-draft", workflow_status: "DRAFT" } })
      .mockResolvedValueOnce({ order: { id: "spk-pub", workflow_status: "PUBLISHED" } });
    mockApi.spkContext.mockResolvedValue({
      context: {
        divisions: [
          { id: "penjualan", label: "Penjualan" },
          { id: "produksi", label: "Produksi" },
        ],
        pics: [
          { id: "pic-a1", name: "Fahdo", divisions: ["penjualan"] },
          { id: "pic-a2", name: "Rina", divisions: ["penjualan"] },
          { id: "pic-b1", name: "Budi", divisions: ["produksi"] },
          { id: "pic-b2", name: "Sari", divisions: ["produksi"] },
          { id: "pic-c1", name: "Lintas", divisions: ["keuangan"] },
          { id: "pic-x", name: "Tanpa" },
        ],
        branch_name: "Surabaya",
      },
    });
    const { container, root } = render(<SpkCreate />);
    await flush();
    const trigger = () => container.querySelector('[data-testid="spk-pic-trigger"]');
    const summary = () => container.querySelector('[data-testid="spk-pic-summary"]').textContent;
    const optionNames = () => [...document.querySelectorAll('[data-testid="spk-pic-option"]')].map((option) => option.textContent.trim());
    const box = (name) => [...document.querySelectorAll('[data-testid="spk-pic-option"]')]
      .find((option) => option.textContent.includes(name))
      .querySelector("input");

    await act(async () => { setControl(container.querySelector('[data-testid="spk-target-division"]'), "penjualan"); });
    await act(async () => { trigger().dispatchEvent(new MouseEvent("click", { bubbles: true })); });
    await act(async () => {
      box("Fahdo").dispatchEvent(new MouseEvent("click", { bubbles: true }));
      box("Rina").dispatchEvent(new MouseEvent("click", { bubbles: true }));
      setControl(document.querySelector('[data-testid="spk-pic-search"]'), "rina");
    });
    expect(summary()).toBe("2 PIC dipilih");
    expect(container.querySelectorAll('[data-testid="spk-pic-chip"]')).toHaveLength(2);

    await act(async () => { setControl(container.querySelector('[data-testid="spk-target-division"]'), "produksi"); });
    expect(summary()).toBe("Pilih PIC");
    expect(container.querySelector('[data-testid="spk-pic-chip"]')).toBeNull();
    expect(document.getElementById("spk-pic-panel")).toBeNull();
    expect(trigger().getAttribute("aria-expanded")).toBe("false");

    await act(async () => { trigger().dispatchEvent(new MouseEvent("click", { bubbles: true })); });
    expect(document.querySelector('[data-testid="spk-pic-search"]').value).toBe("");
    expect(optionNames().sort()).toEqual(["Budi", "Sari"]);
    expect(optionNames()).not.toContain("Fahdo");
    expect(optionNames()).not.toContain("Rina");
    expect(optionNames()).not.toContain("Lintas");
    expect(optionNames()).not.toContain("Tanpa");

    await act(async () => { box("Budi").dispatchEvent(new MouseEvent("click", { bubbles: true })); });
    await act(async () => { setControl(container.querySelector('[data-testid="spk-title"]'), "SPK ganti divisi"); });
    await act(async () => {
      container.querySelector('[data-testid="spk-save-draft"]').dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });
    await flush();
    const draft = mockApi.createSpk.mock.calls[0][0];
    expect(draft.publish).toBe(false);
    expect(draft.target_division).toBe("produksi");
    expect(draft.assigned_user_ids).toEqual(["pic-b1"]);
    expect(draft.milestones[0]).toMatchObject({
      type: "QUANTITY",
      weight: "100",
      requires_evidence: true,
    });

    await act(async () => { setControl(container.querySelector('[data-testid="spk-target-division"]'), "penjualan"); });
    expect(summary()).toBe("Pilih PIC");
    expect(container.querySelector('[data-testid="spk-pic-chip"]')).toBeNull();
    await act(async () => { trigger().dispatchEvent(new MouseEvent("click", { bubbles: true })); });
    await act(async () => { box("Fahdo").dispatchEvent(new MouseEvent("click", { bubbles: true })); });
    await act(async () => {
      container.querySelector('[data-testid="spk-publish"]').dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });
    await act(async () => {
      container.querySelector('[data-testid="spk-publish-confirm-submit"]').click();
    });
    await flush();
    const published = mockApi.createSpk.mock.calls[1][0];
    expect(published.publish).toBe(true);
    expect(published.target_division).toBe("penjualan");
    expect(published.assigned_user_ids).toEqual(["pic-a1"]);
    expect(published.assigned_user_ids).not.toEqual(expect.arrayContaining(["pic-a2", "pic-b1", "pic-b2"]));
    expect(Object.keys(published.milestones[0]).sort()).toEqual([
      "id",
      "requires_evidence",
      "target_value",
      "title",
      "type",
      "unit",
      "weight",
    ]);

    await act(async () => { setControl(container.querySelector('[data-testid="spk-target-division"]'), ""); });
    expect(container.querySelector('[data-testid="spk-pic-need-division"]').textContent).toBe("Pilih divisi tujuan terlebih dahulu");
    expect(trigger().disabled).toBe(true);
    expect(summary()).toBe("Pilih PIC");
    expect(document.querySelector('[data-testid="spk-pic-option"]')).toBeNull();
    await act(async () => { trigger().dispatchEvent(new MouseEvent("click", { bubbles: true })); });
    expect(document.getElementById("spk-pic-panel")).toBeNull();
    expect(optionNames()).toEqual([]);

    await act(async () => { setControl(container.querySelector('[data-testid="spk-target-division"]'), "penjualan"); });
    await act(async () => { trigger().dispatchEvent(new MouseEvent("click", { bubbles: true })); });
    expect(document.getElementById("spk-pic-panel")).not.toBeNull();
    await act(async () => {
      container.querySelector(".spk-create-scroll").dispatchEvent(new Event("scroll"));
    });
    expect(document.getElementById("spk-pic-panel")).toBeNull();
    await act(async () => { trigger().dispatchEvent(new MouseEvent("click", { bubbles: true })); });
    expect(document.getElementById("spk-pic-panel")).not.toBeNull();
    await act(async () => { window.dispatchEvent(new Event("resize")); });
    expect(document.getElementById("spk-pic-panel")).toBeNull();
    act(() => root.unmount());
  });

  test("backend error is shown in plain language", async () => {
    mockApi.getSpk.mockRejectedValue({ response: { data: { detail: "Hanya PIC yang ditugaskan yang dapat mengembalikan SPK." } } });
    const { container, root } = render(<SpkDetail />);
    await flush();
    expect(container.querySelector(".spk-msg.is-error").textContent).toContain("Hanya PIC");
    act(() => root.unmount());
  });
});
