import { act } from "react";
import { createRoot } from "react-dom/client";
import RolePermissionWorkspace from "./RolePermissionWorkspace";

global.IS_REACT_ACT_ENVIRONMENT = true;

const catalog = {
  modules: {
    PO: [
      { key: "po.view", label: "Lihat PO" },
      { key: "po.edit", label: "Ubah PO" },
      { key: "po.cancel", label: "Batalkan PO" },
      { key: "po.delete", label: "Hapus permanen PO" },
    ],
    Penjualan: [
      { key: "sales_order.view", label: "Lihat Pesanan Penjualan" },
      { key: "sales.invoice.delete", label: "Hapus Faktur Penjualan" },
    ],
    "Akses Menu": [{ key: "nav.dashboard", label: "Menu Dashboard" }],
    Pengaturan: [
      { key: "settings.hpp.view", label: "Lihat Pengaturan HPP" },
      { key: "settings.hpp.manage", label: "Ubah Pengaturan HPP" },
    ],
  },
};

const role = {
  role: "crm",
  label: "CRM",
  status: "active",
  is_builtin: true,
  user_count: 2,
};

function mount(overrides = {}) {
  const container = document.createElement("div");
  document.body.appendChild(container);
  const root = createRoot(container);
  const props = {
    currentRole: role,
    selected: "crm",
    isAdminRole: false,
    canManagePerms: true,
    permissionCatalog: catalog,
    permsDraft: new Set(["po.view"]),
    initialPerms: new Set(["po.view"]),
    labelDraft: "CRM",
    descDraft: "",
    onLabelChange: jest.fn(),
    onDescChange: jest.fn(),
    onTogglePerm: jest.fn(),
    onToggleCategoryKeys: jest.fn(),
    permQuery: "",
    onPermQueryChange: jest.fn(),
    stateFilter: "all",
    onStateFilterChange: jest.fn(),
    metaDirty: false,
    busy: false,
    err: "",
    onSave: jest.fn(),
    onDiscard: jest.fn(),
    onToggleActive: jest.fn(),
    onDelete: jest.fn(),
    ...overrides,
  };
  act(() => {
    root.render(<RolePermissionWorkspace {...props} />);
  });
  return {
    container,
    props,
    rerender(next) {
      act(() => {
        root.render(<RolePermissionWorkspace {...props} {...next} />);
      });
    },
    unmount() {
      act(() => root.unmount());
      container.remove();
    },
  };
}

describe("RolePermissionWorkspace", () => {
  test("renders single-column categories with accordion and permission keys", () => {
    const { container, unmount } = mount();
    expect(container.querySelector(".role-perm-flow")).toBeTruthy();
    expect(container.querySelector(".perm-modules")).toBeNull();
    expect(container.querySelector('[data-testid="role-cat-po"]')).toBeTruthy();
    expect(container.querySelector('[data-testid="role-cat-penjualan"]')).toBeTruthy();
    expect(container.querySelector('[data-testid="role-cat-pengaturan"]')).toBeTruthy();
    expect(container.querySelector('[data-testid="role-cat-toggle-po"]').getAttribute("aria-expanded")).toBe("true");
    expect(container.querySelector('[data-testid="role-cat-toggle-penjualan"]').getAttribute("aria-expanded")).toBe("true");
    expect(container.querySelector('[data-testid="role-cat-toggle-pengaturan"]').getAttribute("aria-expanded")).toBe("false");
    expect(container.querySelector('[data-testid="role-cat-grid-pengaturan"]')).toBeNull();
    expect(container.querySelector('[data-testid="role-perm-crm-po.view"] .role-perm-key').textContent).toBe("po.view");
    expect(container.querySelector('[data-testid="role-perm-crm-po.view"]').classList.contains("is-on")).toBe(true);
    expect(container.querySelector('[data-testid="role-perm-crm-po.edit"]').classList.contains("is-on")).toBe(false);
    expect(container.querySelector('[data-testid="role-cat-count-po"]').textContent).toMatch(/1 \/ 4 aktif/);
    expect(container.querySelector('[data-testid="role-perm-count"]').textContent).toMatch(/1 \/ 8 aktif/);
    expect(container.querySelector(".role-perm-grid")).toBeTruthy();
    unmount();
  });

  test("accordion expand/collapse", () => {
    const { container, unmount } = mount();
    const toggle = container.querySelector('[data-testid="role-cat-toggle-pengaturan"]');
    expect(toggle.getAttribute("aria-expanded")).toBe("false");
    act(() => {
      toggle.dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });
    expect(toggle.getAttribute("aria-expanded")).toBe("true");
    expect(container.querySelector('[data-testid="role-cat-grid-pengaturan"]')).toBeTruthy();
    act(() => {
      toggle.dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });
    expect(toggle.getAttribute("aria-expanded")).toBe("false");
    unmount();
  });

  test("search filters label, key, and category", () => {
    const { container, rerender, unmount } = mount();
    rerender({ permQuery: "po.delete" });
    expect(container.querySelector('[data-testid="role-perm-crm-po.delete"]')).toBeTruthy();
    expect(container.querySelector('[data-testid="role-perm-crm-po.view"]')).toBeNull();
    expect(container.querySelector('[data-testid="role-cat-penjualan"]')).toBeNull();
    rerender({ permQuery: "Pengaturan" });
    expect(container.querySelector('[data-testid="role-cat-pengaturan"]')).toBeTruthy();
    expect(container.querySelector('[data-testid="role-perm-crm-settings.hpp.view"]')).toBeTruthy();
    expect(container.querySelector('[data-testid="role-cat-po"]')).toBeNull();
    unmount();
  });

  test("Aktif / Tidak Aktif filters work with search", () => {
    const { container, rerender, unmount } = mount();
    rerender({ stateFilter: "active" });
    expect(container.querySelector('[data-testid="role-perm-crm-po.view"]')).toBeTruthy();
    expect(container.querySelector('[data-testid="role-perm-crm-po.edit"]')).toBeNull();
    rerender({ stateFilter: "inactive", permQuery: "faktur" });
    expect(container.querySelector('[data-testid="role-perm-crm-sales.invoice.delete"]')).toBeTruthy();
    expect(container.querySelector('[data-testid="role-perm-crm-po.view"]')).toBeNull();
    unmount();
  });

  test("dirty state shows sticky save bar copy and reuses onSave", () => {
    const onSave = jest.fn();
    const onDiscard = jest.fn();
    const { container, rerender, unmount } = mount({ onSave, onDiscard });
    expect(container.querySelector('[data-testid="role-save-bar"]').classList.contains("show")).toBe(false);
    rerender({ permsDraft: new Set(["po.view", "po.edit"]) });
    const bar = container.querySelector('[data-testid="role-save-bar"]');
    expect(bar.classList.contains("show")).toBe(true);
    expect(container.querySelector('[data-testid="role-unsaved-indicator"]').textContent).toMatch(/1 perubahan belum disimpan/);
    expect(container.querySelector('[data-testid="role-discard"]').textContent).toMatch(/Batal/);
    expect(container.querySelector('[data-testid="role-save-permissions"]').textContent).toMatch(/Simpan Perubahan/);
    act(() => {
      container.querySelector('[data-testid="role-save-permissions"]').dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });
    expect(onSave).toHaveBeenCalledTimes(1);
    act(() => {
      container.querySelector('[data-testid="role-discard"]').dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });
    expect(onDiscard).toHaveBeenCalledTimes(1);
    unmount();
  });

  test("sensitive marker is presentational only and role danger is off the permission grid", () => {
    const onTogglePerm = jest.fn();
    const onToggleActive = jest.fn();
    const { container, unmount } = mount({ onTogglePerm, onToggleActive });
    expect(container.querySelector('[data-testid="role-perm-sensitive-po.delete"]')).toBeTruthy();
    expect(container.querySelector('[data-testid="role-perm-sensitive-po.cancel"]')).toBeTruthy();
    expect(container.querySelector('[data-testid="role-perm-sensitive-po.view"]')).toBeNull();
    expect(container.querySelector('[data-testid="role-danger-zone"]')).toBeTruthy();
    expect(container.querySelector('[data-testid="role-overflow-toggle"]')).toBeTruthy();
    expect(container.querySelector(".role-actions-inline")).toBeNull();
    act(() => {
      container.querySelector('[data-testid="role-overflow-toggle"]').dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });
    act(() => {
      container.querySelector('[data-testid="role-toggle-active"]').dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });
    expect(onToggleActive).toHaveBeenCalledTimes(1);
    expect(onTogglePerm).not.toHaveBeenCalled();
    unmount();
  });
});
