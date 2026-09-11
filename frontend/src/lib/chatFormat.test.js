import {
  canEditMessage,
  extractClipboardImageFiles,
  isAcceptedChatImageMime,
  memberDisplayName,
  memberRoleLabel,
  previewText,
  replyQuotePreview,
  roomDisplayTitle,
  roomTypeFilter,
  roomInitials,
} from "./chatFormat";
import { workContextHref, workContextLabel } from "./chatWorkLinks";

describe("chatFormat", () => {
  test("room filters map correctly", () => {
    expect(roomTypeFilter("personal", { room_type: "dm" })).toBe(true);
    expect(roomTypeFilter("group", { type: "system_group" })).toBe(true);
    expect(roomTypeFilter("group", { type: "custom_group" })).toBe(true);
    expect(roomTypeFilter("work", { room_type: "work" })).toBe(true);
    expect(roomTypeFilter("personal", { room_type: "work" })).toBe(false);
  });

  test("deleted preview and initials", () => {
    expect(previewText({ deleted: true, content: "x" })).toBe("Pesan dihapus");
    expect(roomInitials("Renea Coffee")).toBe("RC");
  });

  test("edit window 15 minutes", () => {
    const now = Date.parse("2026-09-10T10:00:00Z");
    expect(canEditMessage({ created_at: "2026-09-10T09:50:00Z" }, now)).toBe(true);
    expect(canEditMessage({ created_at: "2026-09-10T09:40:00Z" }, now)).toBe(false);
    expect(canEditMessage({ created_at: "2026-09-10T09:50:00Z", deleted: true }, now)).toBe(false);
  });

  test("member display prefers human name and hides raw UUID", () => {
    expect(memberDisplayName({
      user_id: "d6cc9b04-a27e-4b84-b8c4-14e0da2dbe95",
      user_name: "Dimas Aji Prasetya",
      user_role: "admin",
    })).toBe("Dimas Aji Prasetya");
    expect(memberDisplayName({
      user_id: "u-admin-482bc16d",
      name: "u-admin-482bc16d",
      email: "admin@example.com",
    })).toBe("admin@example.com");
    expect(memberRoleLabel({ role: "admin" })).toBe("admin");
  });

  test("DM title uses peer name; system/custom/work titles stay", () => {
    expect(roomDisplayTitle(
      { type: "dm", name: "Chat" },
      { meId: "u1", members: [{ user_id: "u1", name: "Admin" }, { user_id: "u2", name: "Andi" }] },
    )).toBe("Andi");
    expect(roomDisplayTitle({ type: "system_group", name: "Operator" }, { meId: "u1" })).toBe("Operator");
    expect(roomDisplayTitle({ type: "custom_group", name: "Ops Tim" }, { meId: "u1" })).toBe("Ops Tim");
    expect(roomDisplayTitle({ type: "work", name: "PO-1 · Line" }, { meId: "u1" })).toBe("PO-1 · Line");
  });

  test("clipboard image extraction accepts png/jpeg/webp only once", () => {
    expect(isAcceptedChatImageMime("image/png")).toBe(true);
    expect(isAcceptedChatImageMime("image/gif")).toBe(false);
    const png = new File([new Uint8Array([1, 2, 3])], "a.png", { type: "image/png" });
    const items = [
      { kind: "file", type: "image/png", getAsFile: () => png },
      { kind: "file", type: "image/png", getAsFile: () => png },
      { kind: "string", type: "text/plain", getAsFile: () => null },
    ];
    const files = extractClipboardImageFiles({ items });
    expect(files).toHaveLength(1);
    expect(files[0].type).toBe("image/png");
  });

  test("reply quote preview fallback", () => {
    expect(replyQuotePreview({ unavailable: true }).text).toBe("Pesan tidak tersedia");
    expect(replyQuotePreview({ sender_name: "Budi", content_preview: "halo", unavailable: false }).sender).toBe("Budi");
    expect(replyQuotePreview({ sender_name: "Budi", has_image: true, content_preview: "" }).text).toBe("[Gambar]");
  });
});

describe("chatWorkLinks", () => {
  test("uses existing PO routes and keeps line identity in label", () => {
    const ref = {
      order_id: "ord-1",
      line_id: "line-a",
      snapshot: { po_number: "PO-1", item_name: "Cup 12oz", customer_name: "Renea" },
    };
    const ref2 = {
      order_id: "ord-1",
      line_id: "line-b",
      snapshot: { po_number: "PO-1", item_name: "Cup 12oz" },
    };
    expect(workContextHref(ref)).toBe("/po/ord-1");
    expect(workContextHref(ref, { mobile: true })).toBe("/m/orders/ord-1");
    expect(workContextLabel(ref)).toContain("Cup 12oz");
    expect(ref.line_id).not.toBe(ref2.line_id);
  });
});
