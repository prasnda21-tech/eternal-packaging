import {
  canEditMessage,
  previewText,
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
