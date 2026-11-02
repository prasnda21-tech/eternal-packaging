import {
  SALES_SETTINGS_AUDIT_ACTIONS,
  filterSalesSettingsAuditLogs,
  normalizeSalesSettingsSection,
  resolveSalesSettingsSection,
  salesSettingsLegacyAlias,
  sectionAllowed,
} from "./salesSettingsHub";
import { settingsMenuItems, settingsNavVisible } from "./navAccess";

describe("salesSettingsHub", () => {
  test("legacy aliases point at pengaturan-sales semua tab", () => {
    expect(salesSettingsLegacyAlias("sales-master")).toEqual({
      tab: "pengaturan-sales",
      section: "semua",
    });
    expect(salesSettingsLegacyAlias("sales-user-mapping")).toEqual({
      tab: "pengaturan-sales",
      section: "semua",
    });
    expect(salesSettingsLegacyAlias("other")).toBeNull();
  });

  test("legacy section ids normalize into unified tabs", () => {
    expect(normalizeSalesSettingsSection("master")).toBe("semua");
    expect(normalizeSalesSettingsSection("mapping")).toBe("semua");
    expect(normalizeSalesSettingsSection("akun-cabang")).toBe("semua");
    expect(normalizeSalesSettingsSection("verifikasi")).toBe("verifikasi");
  });

  test("section permission gates do not escalate manage", () => {
    expect(sectionAllowed("semua", { canViewMaster: true, canViewMapping: false })).toBe(true);
    expect(sectionAllowed("verifikasi", { canViewMaster: true, canViewMapping: false })).toBe(true);
    expect(sectionAllowed("verifikasi", { canViewMaster: false, canViewMapping: true })).toBe(false);
    expect(sectionAllowed("riwayat", { canViewMaster: false, canViewMapping: true })).toBe(true);
  });

  test("resolve falls back to first allowed section", () => {
    expect(resolveSalesSettingsSection("verifikasi", { canViewMaster: false, canViewMapping: true })).toBe("riwayat");
    expect(resolveSalesSettingsSection("master", { canViewMaster: true, canViewMapping: true })).toBe("semua");
  });

  test("audit filter keeps only known sales settings actions", () => {
    const rows = filterSalesSettingsAuditLogs([
      { action: "settings.sales_master.update" },
      { action: "settings.sales_label_candidate.approve_new" },
      { action: "sales_user_mapping.update" },
      { action: "orders.sales.update" },
      { action: "invented.fake.event" },
    ]);
    expect(rows.map((r) => r.action)).toEqual([
      "settings.sales_master.update",
      "settings.sales_label_candidate.approve_new",
      "sales_user_mapping.update",
    ]);
    expect(SALES_SETTINGS_AUDIT_ACTIONS).not.toContain("invented.fake.event");
  });

  test("settings menu exposes Pengaturan Sales but not duplicate legacy cards", () => {
    const hasPerm = () => true;
    const visible = settingsNavVisible(hasPerm);
    expect(visible["pengaturan-sales"]).toBe(true);
    expect(visible["sales-master"]).toBe(false);
    expect(visible["sales-user-mapping"]).toBe(false);
    const keys = settingsMenuItems(hasPerm).map((item) => item.key);
    expect(keys).toContain("pengaturan-sales");
    expect(keys).not.toContain("sales-master");
    expect(keys).not.toContain("sales-user-mapping");
  });
});
