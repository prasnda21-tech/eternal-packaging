import { act } from "react";
import { createRoot } from "react-dom/client";
import api from "../lib/api";
import ChatPage from "./ChatPage";
import MobileChatPage from "../mobile/MobileChatPage";
import MobileChatRoomPage from "../mobile/MobileChatRoomPage";
import { MessageBubble, WorkContextCard, WorkPickerModal, EditMembersModal } from "../components/chat/ChatShared";
import { NAV_KEYS } from "../lib/navAccess";
import { visibleGlobalWorkspaceCatalog } from "../lib/globalWorkspace";
import { activeMobileNav, visibleMobileNavItems } from "../lib/mobileWorkspaces";

const mockNavigate = jest.fn();
let mockRoomId = "r1";

jest.mock("react-router-dom", () => ({
  Link: ({ to, children, ...props }) => <a href={to} {...props}>{children}</a>,
  useNavigate: () => mockNavigate,
  useParams: () => ({ roomId: mockRoomId }),
}), { virtual: true });

jest.mock("../lib/api", () => ({
  __esModule: true,
  default: {
    chatListRooms: jest.fn(),
    chatGetRoom: jest.fn(),
    chatListMessages: jest.fn(),
    chatMarkRead: jest.fn(),
    chatUnreadSummary: jest.fn(),
    chatSendMessage: jest.fn(),
    chatUploadAttachment: jest.fn(),
    chatAttachmentDownloadUrl: jest.fn((id) => `/api/chat/attachments/${id}/download`),
    chatSearchUsers: jest.fn(),
    chatSearchWorkOrders: jest.fn(),
    chatWorkOrderLines: jest.fn(),
    chatWorkOrderTasks: jest.fn(),
    chatCreateDm: jest.fn(),
    chatCreateCustomGroup: jest.fn(),
    chatCreateWorkRoom: jest.fn(),
    chatAddMembers: jest.fn(),
    chatRemoveMembers: jest.fn(),
    chatEditMessage: jest.fn(),
    chatDeleteMessage: jest.fn(),
  },
  api: undefined,
}));

// Keep named `api` export in sync with default for ChatPage import { api }
const apiModule = require("../lib/api");
apiModule.api = apiModule.default;

jest.mock("../lib/auth", () => ({
  useAuth: () => ({
    user: { id: "u1", name: "Admin", permissions: ["nav.chat", "chat.use", "chat.group.manage", "chat.moderate"] },
    hasPerm: (p) => ["nav.chat", "chat.use", "chat.group.manage", "chat.moderate"].includes(p),
  }),
}));

jest.mock("../lib/chatRealtime", () => ({
  createChatRealtime: () => ({
    start: jest.fn(),
    stop: jest.fn(),
    subscribe: jest.fn(),
    status: () => "online",
  }),
}));

global.IS_REACT_ACT_ENVIRONMENT = true;

const flush = async () => {
  await act(async () => {
    await new Promise((resolve) => setTimeout(resolve, 0));
  });
};

function seedApi() {
  api.chatListRooms.mockResolvedValue({
    rooms: [
      {
        id: "r1",
        room_id: "r1",
        name: "DM Peer",
        room_type: "dm",
        type: "dm",
        unread_count: 2,
        last_activity_at: "2026-09-10T01:00:00Z",
        last_message_preview: "Halo",
      },
      {
        id: "r2",
        room_id: "r2",
        name: "Operator",
        room_type: "system_group",
        type: "system_group",
        unread_count: 0,
      },
    ],
  });
  api.chatGetRoom.mockResolvedValue({
    id: "r1",
    room_id: "r1",
    name: "Chat",
    room_type: "dm",
    type: "dm",
    members: [
      { user_id: "u1", name: "Admin", role: "admin" },
      { user_id: "d6cc9b04-a27e-4b84-b8c4-14e0da2dbe95", display_name: "Peer Helper", role: "helper" },
    ],
  });
  api.chatListMessages.mockResolvedValue({
    messages: [
      {
        id: "m1",
        message_id: "m1",
        room_id: "r1",
        sender_user_id: "u2",
        content: "Halo <script>alert(1)</script>",
        sequence: 1,
        created_at: "2026-09-10T01:00:00Z",
      },
    ],
  });
  api.chatMarkRead.mockResolvedValue({ ok: true });
  api.chatSendMessage.mockResolvedValue({
    id: "m2",
    message_id: "m2",
    room_id: "r1",
    sender_user_id: "u1",
    content: "Balasan",
    sequence: 2,
    client_message_id: "cm-1",
    created_at: "2026-09-10T01:01:00Z",
  });
}

describe("Chat web UI", () => {
  let container;
  let root;

  beforeEach(() => {
    container = document.createElement("div");
    document.body.appendChild(container);
    root = createRoot(container);
    jest.clearAllMocks();
    mockRoomId = "r1";
    seedApi();
  });

  afterEach(() => {
    act(() => root.unmount());
    container.remove();
  });

  test("sidebar catalog includes Chat", () => {
    const tabs = visibleGlobalWorkspaceCatalog((p) => p === NAV_KEYS.chat || p === "chat.use");
    expect(tabs.some((t) => t.path === "/chat")).toBe(true);
  });

  test("room list and conversation render XSS-safe text", async () => {
    await act(async () => {
      root.render(<ChatPage />);
    });
    await flush();
    expect(container.querySelector('[data-testid="chat-page"]')).toBeTruthy();
    expect(container.querySelector('[data-testid="chat-inbox"]')).toBeTruthy();
    expect(container.textContent).toContain("DM Peer");
    expect(container.textContent).toContain("Operator");
    expect(container.textContent).toContain("Halo <script>alert(1)</script>");
    expect(container.querySelector("script")).toBeNull();
  });

  test("member names are human-readable and DM title resolves peer", async () => {
    await act(async () => {
      root.render(<ChatPage />);
    });
    await flush();
    const memberNames = Array.from(container.querySelectorAll('[data-testid="chat-member-name"]')).map((n) => n.textContent);
    expect(memberNames).toContain("Peer Helper");
    expect(memberNames.join(" ")).not.toMatch(/d6cc9b04-a27e-4b84-b8c4-14e0da2dbe95/);
    expect(container.querySelector('[data-testid="chat-member-role"]')?.textContent).toMatch(/helper|admin/i);
    expect(container.querySelector('[data-testid="chat-conversation-title"]')?.textContent).toBe("Peer Helper");
    expect(container.querySelector('[data-testid="chat-messages"]')).toBeTruthy();
    expect(container.querySelector('[data-testid="chat-composer"]')).toBeTruthy();
  });

  test("inbox header keeps + Baru visible with filters and functional actions", async () => {
    await act(async () => {
      root.render(<ChatPage />);
    });
    await flush();
    const head = container.querySelector('[data-testid="chat-inbox-head"]');
    const newBtn = container.querySelector('[data-testid="chat-new-btn"]');
    expect(head).toBeTruthy();
    expect(head.contains(newBtn)).toBe(true);
    expect(newBtn.className).toMatch(/chat-btn-primary/);
    expect(container.querySelector('[data-testid="chat-filter-all"]')).toBeTruthy();
    expect(container.querySelector('[data-testid="chat-info-btn"]')?.className).toMatch(/chat-btn/);
    expect(container.querySelector('[data-testid="chat-tag-work-btn"]')?.className).toMatch(/chat-btn/);
    expect(container.querySelector('[data-testid="chat-attach-btn"]')?.className).toMatch(/chat-btn/);
    expect(container.querySelector('[data-testid="chat-send-btn"]')?.className).toMatch(/chat-btn-primary/);
  });

  test("Tag Pekerjaan modal renders selectable rows and footer actions", async () => {
    jest.useFakeTimers();
    const onConfirm = jest.fn();
    const searchOrders = jest.fn().mockResolvedValue({
      items: [{ id: "ord-1", po_number: "PO-1", customer_name: "Acme" }],
    });
    const loadLines = jest.fn().mockResolvedValue({
      items: [{ line_id: "L1", line_idx: 0, name: "Cup 12oz", qty: 100 }],
    });
    const loadTasks = jest.fn().mockResolvedValue({ items: [] });
    await act(async () => {
      root.render(
        <WorkPickerModal
          open
          onClose={() => {}}
          onConfirm={onConfirm}
          searchOrders={searchOrders}
          loadLines={loadLines}
          loadTasks={loadTasks}
        />,
      );
    });
    await act(async () => {
      jest.advanceTimersByTime(300);
      await Promise.resolve();
    });
    expect(container.querySelector('[data-testid="chat-work-picker"]')).toBeTruthy();
    expect(container.querySelector('[data-testid="chat-work-close"]')).toBeTruthy();
    expect(container.querySelector('[data-testid="chat-work-po-list"] button')).toBeTruthy();
    await act(async () => {
      container.querySelector('[data-testid="chat-work-po-list"] button')?.click();
      await Promise.resolve();
    });
    expect(loadLines).toHaveBeenCalled();
    expect(container.querySelector('[data-testid="chat-work-line-option"]')).toBeTruthy();
    expect(container.querySelector('[data-testid="chat-work-picker-actions"]')).toBeTruthy();
    expect(container.querySelector('[data-testid="chat-work-po-only"]')?.className).toMatch(/chat-btn-primary/);
    expect(container.querySelector('[data-testid="chat-work-pick-task-po"]')?.className).toMatch(/chat-btn-secondary/);
    jest.useRealTimers();
  });

  test("Tag Pekerjaan shows loading then empty state", async () => {
    jest.useFakeTimers();
    let resolveSearch;
    const searchOrders = jest.fn().mockImplementation(
      () => new Promise((resolve) => { resolveSearch = resolve; }),
    );
    await act(async () => {
      root.render(
        <WorkPickerModal
          open
          onClose={() => {}}
          onConfirm={() => {}}
          searchOrders={searchOrders}
          loadLines={async () => ({ items: [] })}
          loadTasks={async () => ({ items: [] })}
        />,
      );
    });
    await act(async () => {
      jest.advanceTimersByTime(300);
      await Promise.resolve();
    });
    expect(container.querySelector('[data-testid="chat-work-loading"]')?.textContent).toMatch(/Memuat/);
    await act(async () => {
      resolveSearch({ items: [] });
      await Promise.resolve();
    });
    expect(container.querySelector('[data-testid="chat-work-empty"]')?.textContent).toMatch(/Belum ada PO|ketik|Tidak ada/i);
    jest.useRealTimers();
  });

  test("Tag Pekerjaan no-results state after search", async () => {
    jest.useFakeTimers();
    const searchOrders = jest.fn().mockResolvedValue({ items: [] });
    await act(async () => {
      root.render(
        <WorkPickerModal
          open
          onClose={() => {}}
          onConfirm={() => {}}
          searchOrders={searchOrders}
          loadLines={async () => ({ items: [] })}
          loadTasks={async () => ({ items: [] })}
        />,
      );
    });
    await act(async () => {
      jest.advanceTimersByTime(300);
      await Promise.resolve();
    });
    const input = container.querySelector('[data-testid="chat-work-po-search"]');
    const setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, "value").set;
    await act(async () => {
      setter.call(input, "ZZZ-NONE");
      input.dispatchEvent(new Event("input", { bubbles: true }));
    });
    await act(async () => {
      jest.advanceTimersByTime(300);
      await Promise.resolve();
    });
    expect(container.querySelector('[data-testid="chat-work-empty"]')?.textContent).toMatch(/Tidak ada PO\/pekerjaan yang cocok/);
    jest.useRealTimers();
  });

  test("work card renders flat snapshot fields from message payload", () => {
    act(() => {
      root.render(
        <WorkContextCard
          workRef={{
            order_id: "ord-flat",
            po_number: "PO-FLAT",
            customer_name: "Renea",
            item_name: "Cup",
            qty: 12,
          }}
        />,
      );
    });
    expect(container.querySelector('[data-testid="chat-work-card"]')?.textContent).toMatch(/PO-FLAT/);
    expect(container.querySelector('[data-testid="chat-work-card"]')?.textContent).toMatch(/Renea/);
    expect(container.querySelector('[data-testid="chat-work-open-link"]')?.getAttribute("href")).toBe("/po/ord-flat");
  });

  test("Edit Anggota modal toggles members with human-readable names", async () => {
    jest.useFakeTimers();
    const onSave = jest.fn().mockResolvedValue(undefined);
    const searchUsers = jest.fn().mockResolvedValue({
      items: [{ id: "u9", name: "New Helper", role: "helper" }],
    });
    await act(async () => {
      root.render(
        <EditMembersModal
          open
          onClose={() => {}}
          members={[
            { user_id: "u1", display_name: "Admin Eternal", role: "admin" },
            { user_id: "u2", display_name: "Peer Helper", role: "helper" },
          ]}
          searchUsers={searchUsers}
          onSave={onSave}
        />,
      );
    });
    await act(async () => {
      jest.advanceTimersByTime(250);
      await Promise.resolve();
    });
    const names = Array.from(container.querySelectorAll('[data-testid="chat-edit-member-name"]')).map((n) => n.textContent);
    expect(names).toEqual(expect.arrayContaining(["Admin Eternal", "Peer Helper", "New Helper"]));
    expect(names.join(" ")).not.toMatch(/u1|u2|u9/);
    await act(async () => {
      const opt = Array.from(container.querySelectorAll('[data-testid="chat-edit-member-option"]'))
        .find((el) => el.getAttribute("data-user-id") === "u9");
      opt?.click();
    });
    await act(async () => {
      container.querySelector('[data-testid="chat-edit-members-save"]')?.click();
      await Promise.resolve();
    });
    expect(onSave).toHaveBeenCalled();
    const arg = onSave.mock.calls[0][0];
    expect(arg.addIds).toContain("u9");
    expect(arg.selectedIds).toEqual(expect.arrayContaining(["u1", "u2", "u9"]));
    jest.useRealTimers();
  });

  test("Edit Anggota control visible for custom_group managers only", async () => {
    api.chatGetRoom.mockResolvedValue({
      id: "r-group",
      room_id: "r-group",
      name: "Ops",
      room_type: "custom_group",
      type: "custom_group",
      members: [
        { user_id: "u1", display_name: "Admin", role: "admin" },
        { user_id: "u2", display_name: "Peer Helper", role: "helper" },
      ],
    });
    mockRoomId = "r-group";
    await act(async () => {
      root.render(<ChatPage />);
    });
    await flush();
    expect(container.querySelector('[data-testid="chat-edit-members-btn"]')).toBeTruthy();
  });

  test("system_group Info has no Edit Anggota control", async () => {
    api.chatGetRoom.mockResolvedValue({
      id: "r-sys",
      room_id: "r-sys",
      name: "Operator",
      room_type: "system_group",
      type: "system_group",
      members: [{ user_id: "u1", display_name: "Admin", role: "admin" }],
    });
    mockRoomId = "r-sys";
    await act(async () => {
      root.render(<ChatPage />);
    });
    await flush();
    expect(container.querySelector('[data-testid="chat-edit-members-btn"]')).toBeNull();
    expect(container.querySelector('[data-testid="chat-member-list"]')).toBeTruthy();
  });

  test("send keeps draft on failure", async () => {
    await act(async () => {
      root.render(<ChatPage />);
    });
    await flush();
    const input = container.querySelector('[data-testid="chat-composer-input"]');
    const setter = Object.getOwnPropertyDescriptor(window.HTMLTextAreaElement.prototype, "value").set;
    await act(async () => {
      setter.call(input, "Balasan");
      input.dispatchEvent(new Event("input", { bubbles: true }));
      container.querySelector('[data-testid="chat-send-btn"]').click();
    });
    await flush();
    expect(api.chatSendMessage).toHaveBeenCalled();
    expect(api.chatSendMessage.mock.calls[0][1].client_message_id).toBeTruthy();

    api.chatSendMessage.mockRejectedValueOnce({ response: { data: { detail: "Gagal" } } });
    await act(async () => {
      setter.call(input, "Draft tetap");
      input.dispatchEvent(new Event("input", { bubbles: true }));
      container.querySelector('[data-testid="chat-send-btn"]').click();
    });
    await flush();
    expect(container.querySelector('[data-testid="chat-send-error"]')?.textContent || "").toMatch(/Gagal|Draft/);
    expect(container.querySelector('[data-testid="chat-composer-input"]').value).toBe("Draft tetap");
  });

  test("Tag Pekerjaan button present", async () => {
    await act(async () => {
      root.render(<ChatPage />);
    });
    await flush();
    expect(container.querySelector('[data-testid="chat-tag-work-btn"]')).toBeTruthy();
  });

  test("deleted message placeholder and edited label", () => {
    act(() => {
      root.render(
        <MessageBubble
          message={{ message_id: "x", deleted: true, content: "secret" }}
          self
          attachmentUrl={() => "#"}
        />,
      );
    });
    expect(container.querySelector('[data-testid="chat-message-deleted"]')?.textContent).toBe("Pesan dihapus");
    act(() => {
      root.render(
        <MessageBubble
          message={{ message_id: "x", content: "hi", edited_at: "2026-09-10T01:02:00Z" }}
          self
          attachmentUrl={() => "#"}
        />,
      );
    });
    expect(container.querySelector('[data-testid="chat-edited-label"]')?.textContent).toBe("Diedit");
  });

  test("work card links to existing PO route", () => {
    act(() => {
      root.render(
        <WorkContextCard
          workRef={{
            order_id: "ord-9",
            snapshot: { po_number: "PO-9", customer_name: "Renea", item_name: "Cup" },
          }}
        />,
      );
    });
    expect(container.querySelector('[data-testid="chat-work-open-link"]')?.getAttribute("href")).toBe("/po/ord-9");
  });

  test("custom group create button when authorized", async () => {
    mockRoomId = undefined;
    await act(async () => {
      root.render(<ChatPage />);
    });
    await flush();
    act(() => container.querySelector('[data-testid="chat-new-btn"]').click());
    await flush();
    expect(container.querySelector('[data-testid="chat-new-group"]')).toBeTruthy();
  });
});

describe("Chat PWA", () => {
  let container;
  let root;

  beforeEach(() => {
    container = document.createElement("div");
    document.body.appendChild(container);
    root = createRoot(container);
    jest.clearAllMocks();
    mockRoomId = "r1";
    seedApi();
  });

  afterEach(() => {
    act(() => root.unmount());
    container.remove();
  });

  test("nav includes chat and unread path active", () => {
    const items = visibleMobileNavItems((p) => p === NAV_KEYS.chat || p === "chat.use");
    expect(items.some((i) => i.id === "chat")).toBe(true);
    expect(activeMobileNav("/m/chat/r1")).toBe("chat");
  });

  test("mobile room list", async () => {
    await act(async () => {
      root.render(<MobileChatPage />);
    });
    await flush();
    expect(container.querySelector('[data-testid="mobile-chat-page"]')).toBeTruthy();
    expect(container.textContent).toContain("DM Peer");
  });

  test("mobile conversation send", async () => {
    await act(async () => {
      root.render(<MobileChatRoomPage />);
    });
    await flush();
    expect(container.querySelector('[data-testid="mobile-chat-room-page"]')).toBeTruthy();
    const input = container.querySelector('[data-testid="chat-composer-input"]');
    const setter = Object.getOwnPropertyDescriptor(window.HTMLTextAreaElement.prototype, "value").set;
    await act(async () => {
      setter.call(input, "PWA hi");
      input.dispatchEvent(new Event("input", { bubbles: true }));
      container.querySelector('[data-testid="chat-send-btn"]').click();
    });
    await flush();
    expect(api.chatSendMessage).toHaveBeenCalled();
  });
});
