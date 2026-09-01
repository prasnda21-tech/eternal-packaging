import { employeeAdminMenuItems, employeeAdminNavVisible, NAV_KEYS, NAV_MENU_FALLBACK } from "../lib/navAccess";

const has = (...keys) => (k) => keys.includes(k);

describe("Administrasi Karyawan nav", () => {
  test("sidebar catalog includes parent and four children", () => {
    const labels = NAV_MENU_FALLBACK.filter((m) => m.parent === NAV_KEYS.employeeAdmin || m.key === NAV_KEYS.employeeAdmin).map((m) => m.label);
    expect(labels).toEqual([
      "Administrasi Karyawan",
      "Pengajuan Saya",
      "Buat Pengajuan",
      "Persetujuan",
      "Arsip Dokumen",
    ]);
  });

  test("hidden without grants", () => {
    const v = employeeAdminNavVisible(has());
    expect(v.parent).toBe(false);
    expect(employeeAdminMenuItems(has()).length).toBe(0);
  });

  test("submit perm shows mine and create", () => {
    const v = employeeAdminNavVisible(has("employee_admin.submit"));
    expect(v.parent).toBe(true);
    expect(v.mine).toBe(true);
    expect(v.create).toBe(true);
    expect(v.approve).toBe(false);
    const items = employeeAdminMenuItems(has("employee_admin.submit"));
    expect(items.map((i) => i.label)).toEqual(["Pengajuan Saya", "Buat Pengajuan"]);
  });

  test("approve and archive are independent", () => {
    const v = employeeAdminNavVisible(has("employee_admin.approve", "employee_admin.document_view"));
    expect(v.approve).toBe(true);
    expect(v.archive).toBe(true);
    expect(v.create).toBe(false);
  });
});
