import fs from "fs";
import path from "path";
import { activeMobileNav } from "../lib/mobileWorkspaces";
import { loginPathForLocation, postLoginPath } from "../lib/mobileAuthRedirect";

describe("mobile route wiring", () => {
  const app = fs.readFileSync(path.join(__dirname, "../App.js"), "utf8");
  const layout = fs.readFileSync(path.join(__dirname, "../mobile/MobileOperationalLayout.jsx"), "utf8");
  const detail = fs.readFileSync(path.join(__dirname, "../mobile/MobileOrderDetailPage.jsx"), "utf8");
  const delivery = fs.readFileSync(path.join(__dirname, "../mobile/workspaces/MobileDeliveryWorkspace.jsx"), "utf8");
  const screener = fs.readFileSync(path.join(__dirname, "../mobile/workspaces/MobileScreenerWorkspace.jsx"), "utf8");
  const packing = fs.readFileSync(path.join(__dirname, "../mobile/workspaces/MobilePackingWorkspace.jsx"), "utf8");
  const print = fs.readFileSync(path.join(__dirname, "../mobile/workspaces/MobilePrintWorkspace.jsx"), "utf8");
  const loading = fs.readFileSync(path.join(__dirname, "../mobile/workspaces/MobileLoadingWorkspace.jsx"), "utf8");

  test("mobile namespace is outside AppShell", () => {
    expect(app).toMatch(/path="\/m\/tasks"/);
    expect(app).toMatch(/path="\/m\/orders"/);
    expect(app).toMatch(/path="\/m\/orders\/:id"/);
    expect(app).toMatch(/path="\/m\/notifications"/);
    expect(app).toMatch(/path="\/m\/profile"/);
    expect(app).toMatch(/MobileOperationalLayout/);
    expect(app).toMatch(/loginPathForLocation/);
    expect(app).toMatch(/postLoginPath/);
    const mobileIdx = app.indexOf("MobileOperationalLayout");
    const shellIdx = app.indexOf("<AppShell");
    expect(mobileIdx).toBeGreaterThan(-1);
    expect(shellIdx).toBeGreaterThan(mobileIdx);
  });

  test("desktop routes remain unchanged", () => {
    expect(app).toMatch(/path="\/po\/:id"/);
    expect(app).toMatch(/path="\/tasks\/helper"/);
    expect(app).not.toMatch(/path="\/" element=\{<Navigate to="\/m/);
  });

  test("unauthorized workspace cannot render another role action panel", () => {
    expect(detail).toMatch(/selectMobileWorkspace/);
    expect(detail).toMatch(/hasPerm\(selected\.permission\)/);
    expect(detail).toMatch(/WORKSPACE_VIEWS\[selected\.id\]/);
  });

  test("bottom nav active helper", () => {
    expect(activeMobileNav("/m/tasks")).toBe("tasks");
    expect(layout).toMatch(/MobileBottomNav/);
  });

  test("delivery confirm uses existing contract", () => {
    expect(delivery).toMatch(/confirmDeliveredBlockReason/);
    expect(delivery).toMatch(/uploadDeliveryRecipientSignature/);
    expect(delivery).toMatch(/uploadDeliveryDriverSignature/);
    expect(delivery).toMatch(/confirmDelivered/);
    expect(delivery).not.toMatch(/\/delivered\?/);
  });

  test("screener metrics omit Sisa while other workspaces keep it", () => {
    expect(screener).toMatch(/label: "Target"/);
    expect(screener).toMatch(/label: "Selesai"/);
    expect(screener).not.toMatch(/label: "Sisa"/);
    expect(screener).toMatch(/mop-metrics--2col/);
    expect(packing).toMatch(/label: "Sisa"/);
    expect(print).toMatch(/label: "Sisa"/);
    expect(loading).toMatch(/Remaining Qty/);
    expect(loading).toMatch(/Sisa \{Number\(line\.available/);
  });

  test("auth redirect helpers used by router", () => {
    expect(loginPathForLocation("/m/tasks", "")).toContain("next=");
    expect(postLoginPath("/m/profile")).toBe("/m/profile");
    expect(postLoginPath("/dashboard")).toBe("/");
  });
});
