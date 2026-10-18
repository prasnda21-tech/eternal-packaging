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
    Pembelian: [{ key: "purchase_hpp.view", label: "Lihat HPP Pembelian" }],
    Persediaan: [{ key: "master_items.view", label: "Lihat Barang/Jasa" }],
    "Akses Menu": [
      { key: "nav.dashboard", label: "Menu Dashboard" },
      { key: "nav.purchasing", label: "Menu Pembelian" },
    ],
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
    expect(container.querySelector('[data-testid="role-perm-count"]').textContent).toMatch(/1 \/ 10 aktif/);
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

  test("expand all / collapse all module accordions", () => {
    const { container, unmount } = mount();
    expect(container.querySelector('[data-testid="role-perm-accordion-tools"]')).toBeTruthy();
    expect(container.querySelector('[data-testid="role-perm-search-hint"]')).toBeTruthy();
    act(() => {
      container.querySelector('[data-testid="role-perm-expand-all"]')
        .dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });
    expect(container.querySelector('[data-testid="role-cat-toggle-pengaturan"]').getAttribute("aria-expanded")).toBe("true");
    expect(container.querySelector('[data-testid="role-cat-grid-pengaturan"]')).toBeTruthy();
    act(() => {
      container.querySelector('[data-testid="role-perm-collapse-all"]')
        .dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });
    expect(container.querySelector('[data-testid="role-cat-toggle-po"]').getAttribute("aria-expanded")).toBe("false");
    expect(container.querySelector('[data-testid="role-cat-grid-po"]')).toBeNull();
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

  test("Role & Hak Akses lists Catatan Produksi permissions for CRM", () => {
    const { container, unmount } = mount({
      permissionCatalog: {
        modules: {
          "Catatan Produksi": [
            { key: "production_note.view", label: "Lihat Catatan Produksi" },
            { key: "production_note.create", label: "Tambah Catatan Produksi" },
            { key: "production_note.edit", label: "Edit Catatan Produksi" },
          ],
          "Akses Menu": [{ key: "nav.production", label: "Menu Produksi" }],
        },
      },
      permsDraft: new Set(),
      initialPerms: new Set(),
    });
    expect(container.querySelector('[data-testid="role-cat-catatan-produksi"]')).toBeTruthy();
    expect(container.textContent).toContain("Lihat Catatan Produksi");
    expect(container.textContent).toContain("Tambah Catatan Produksi");
    expect(container.textContent).toContain("Edit Catatan Produksi");
    expect(container.querySelector('[data-testid="role-perm-crm-production_note.view"]')).toBeTruthy();
    expect(container.querySelector('[data-testid="role-perm-crm-production_note.create"]')).toBeTruthy();
    expect(container.querySelector('[data-testid="role-perm-crm-production_note.edit"]')).toBeTruthy();
    expect(container.textContent).not.toContain("Menu Produksi");
    expect(container.textContent).not.toContain("production_note.delete");
    unmount();
  });

  test("Direktur shows full-access lock and hides permission editor", () => {
    const { container, unmount } = mount({
      selected: "direktur_utama",
      isAdminRole: false,
      isDirectorRole: true,
      currentRole: {
        role: "direktur_utama",
        label: "Direktur",
        status: "active",
        is_builtin: true,
        user_count: 1,
      },
      permsDraft: new Set(["po.view"]),
      initialPerms: new Set(["po.view"]),
    });
    expect(container.querySelector('[data-testid="role-director-lock"]')).toBeTruthy();
    expect(container.textContent).toMatch(/Akses penuh sistem/);
    expect(container.querySelector('[data-testid="role-full-access-notice"]')).toBeTruthy();
    expect(container.querySelector('[data-testid="role-save-permissions"]')).toBeNull();
    expect(container.querySelector('[data-testid="role-package-confirm-panel"]')).toBeNull();
    expect(container.querySelector('[data-testid="role-perm-direktur_utama-po.view"]')).toBeNull();
    unmount();
  });

  test("CRM shows package lock banner; confirm panel only for Holding", () => {
    const onConfirmPackageOptInChange = jest.fn();
    const onConfirmReviewKeysChange = jest.fn();
    const crmMount = mount({
      permsDraft: new Set(["po.view", "purchase_hpp.view"]),
      initialPerms: new Set(["po.view", "purchase_hpp.view"]),
      confirmedPackageKeys: [],
      confirmPackageOptIn: false,
      onConfirmPackageOptInChange,
      confirmReviewKeys: [],
      onConfirmReviewKeysChange,
      // Expand matching categories so package lock badges are in the DOM.
      permQuery: "HPP",
    });
    expect(crmMount.container.querySelector('[data-testid="role-package-lock-banner"]')).toBeTruthy();
    expect(crmMount.container.querySelector('[data-testid="role-package-stored-ineffective"]')).toBeTruthy();
    expect(crmMount.container.querySelector('[data-testid="role-package-confirm-panel"]')).toBeNull();
    expect(crmMount.container.querySelector('[data-testid="role-perm-package-ineffective-purchase_hpp.view"]')).toBeTruthy();
    crmMount.unmount();

    const blockedMount = mount({
      permsDraft: new Set(["po.view"]),
      initialPerms: new Set(["po.view"]),
      permQuery: "Barang",
    });
    expect(blockedMount.container.querySelector('[data-testid="role-perm-package-blocked-master_items.view"]')).toBeTruthy();
    const blockedInput = blockedMount.container.querySelector(
      '[data-testid="role-perm-crm-master_items.view"] input',
    );
    expect(blockedInput).toBeTruthy();
    expect(blockedInput.disabled).toBe(true);
    blockedMount.unmount();

    const holdingRole = {
      role: "holding_team",
      label: "Holding",
      status: "active",
      is_builtin: true,
      user_count: 1,
    };
    const { container, rerender, unmount } = mount({
      currentRole: holdingRole,
      selected: "holding_team",
      labelDraft: "Holding",
      permsDraft: new Set(["po.view", "purchase_hpp.view"]),
      initialPerms: new Set(["po.view", "purchase_hpp.view"]),
      confirmedPackageKeys: [],
      confirmPackageOptIn: false,
      onConfirmPackageOptInChange,
      confirmReviewKeys: [],
      onConfirmReviewKeysChange,
    });
    expect(container.querySelector('[data-testid="role-package-lock-banner"]')).toBeNull();
    expect(container.querySelector('[data-testid="role-package-confirm-panel"]')).toBeTruthy();
    rerender({
      confirmPackageOptIn: true,
      confirmReviewKeys: ["purchase_hpp.view"],
      permsDraft: new Set(["po.view", "purchase_hpp.view"]),
      initialPerms: new Set(["po.view", "purchase_hpp.view"]),
      confirmedPackageKeys: [],
      onConfirmPackageOptInChange,
      onConfirmReviewKeysChange,
    });
    expect(container.querySelector('[data-testid="role-package-confirm-list"]')).toBeTruthy();
    expect(container.querySelector('[data-testid="role-package-review-purchase_hpp.view"]')).toBeTruthy();
    expect(container.querySelector('[data-testid="role-package-activation-preview"]').textContent).toMatch(/1 key/);
    expect(container.querySelector('[data-testid="role-save-bar"]').classList.contains("show")).toBe(true);
    unmount();
  });

  test("Role & Hak Akses lists Lihat Proforma Invoice", () => {
    const { container, unmount } = mount({
      permissionCatalog: {
        modules: {
          Penjualan: [
            { key: "proforma_invoice.view", label: "Lihat Proforma Invoice" },
            { key: "sales.invoice.view", label: "Lihat Faktur Penjualan" },
          ],
          "Akses Menu": [{ key: "nav.sales.sales_invoice", label: "Menu Faktur Penjualan" }],
        },
      },
      permsDraft: new Set(),
      initialPerms: new Set(),
    });
    expect(container.querySelector('[data-testid="role-cat-penjualan"]')).toBeTruthy();
    expect(container.textContent).toContain("Lihat Proforma Invoice");
    expect(container.querySelector('[data-testid="role-perm-crm-proforma_invoice.view"]')).toBeTruthy();
    expect(container.textContent).not.toContain("Menu Faktur Penjualan");
    expect(container.textContent).not.toContain("nav.sales.sales_invoice");
    unmount();
  });
});
