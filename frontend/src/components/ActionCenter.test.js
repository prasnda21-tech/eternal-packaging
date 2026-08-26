import fs from "fs";
import path from "path";

describe("Tugas Saya routing", () => {
  const src = fs.readFileSync(path.join(__dirname, "ActionCenter.jsx"), "utf8");
  test("uses taskPoHref not raw /po/id for all cards", () => {
    expect(src).toMatch(/taskPoHref/);
    expect(src).not.toMatch(/to=\{`\/po\/\$\{o\.id\}`\}/);
    expect(src).not.toMatch(/& Tim/);
  });

  test("division cards redirect to /tasks submenu", () => {
    expect(src).toMatch(/actionKeyToTaskPath/);
    expect(src).toMatch(/navigate\(href\)/);
  });
});

describe("Action Center maps design and scheduling to division tasks", () => {
  const nav = fs.readFileSync(path.join(__dirname, "../lib/navAccess.js"), "utf8");
  test("design_upload and penjadwalan use existing task paths", () => {
    expect(nav).toMatch(/design_upload:\s*TASK_NAV_PATHS\.designer/);
    expect(nav).toMatch(/penjadwalan:\s*TASK_NAV_PATHS\.kepala_produksi/);
  });
});
