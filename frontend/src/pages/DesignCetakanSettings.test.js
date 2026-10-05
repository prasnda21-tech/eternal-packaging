import fs from "fs";
import path from "path";

const settingsSrc = fs.readFileSync(path.join(__dirname, "Settings.jsx"), "utf8");
const pageSrc = fs.readFileSync(path.join(__dirname, "DesignCetakanSettings.jsx"), "utf8");
const apiSrc = fs.readFileSync(path.join(__dirname, "../lib/api.js"), "utf8");
const navSrc = fs.readFileSync(path.join(__dirname, "../lib/navAccess.js"), "utf8");

describe("Design Cetakan settings wiring", () => {
  test("Settings hub exposes Design Cetakan card and panel", () => {
    expect(settingsSrc).toMatch(/id:\s*"design-cetakan"/);
    expect(settingsSrc).toMatch(/settings-tab-design-cetakan/);
    expect(settingsSrc).toMatch(/canViewDesignCetakan/);
    expect(settingsSrc).toMatch(/canEditDesignCetakan/);
    expect(settingsSrc).toMatch(/tab === "design-cetakan" && canViewDesignCetakan/);
    expect(settingsSrc).toMatch(/DesignCetakanSettings/);
    expect(settingsSrc).toMatch(/settings\.design_cetakan\.view/);
    expect(settingsSrc).toMatch(/settings\.design_cetakan\.manage/);
  });

  test("page is text-only with four sections and no upload", () => {
    expect(pageSrc).toMatch(/data-testid="design-cetakan-settings"/);
    expect(pageSrc).toMatch(/design-cetakan-tab-umum/);
    expect(pageSrc).toMatch(/design-cetakan-tab-po/);
    expect(pageSrc).toMatch(/design-cetakan-tab-invoice/);
    expect(pageSrc).toMatch(/design-cetakan-tab-sj/);
    expect(pageSrc).toMatch(/design-cetakan-save/);
    expect(pageSrc).not.toMatch(/type=["']file["']/);
    expect(pageSrc).not.toMatch(/FormData|uploadLogo|signatureUpload/);
  });

  test("api and navAccess include design-cetakan", () => {
    expect(apiSrc).toMatch(/designCetakanSettings:\s*\(\)\s*=>\s*client\.get\(`\/settings\/design-cetakan`/);
    expect(apiSrc).toMatch(/updateDesignCetakanSettings:/);
    expect(navSrc).toMatch(/"design-cetakan"/);
    expect(navSrc).toMatch(/settings\.design_cetakan\.view/);
    expect(navSrc).toMatch(/"Design Cetakan"/);
  });
});
