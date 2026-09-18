import fs from "fs";
import path from "path";
import { STAGES } from "../lib/constants";

describe("PO Detail compact header + sticky StageFlow", () => {
  const detail = fs.readFileSync(path.join(__dirname, "PODetail.jsx"), "utf8");
  const bar = fs.readFileSync(path.join(__dirname, "../components/StageBar.jsx"), "utf8");
  const card = fs.readFileSync(path.join(__dirname, "../components/POCard.jsx"), "utf8");
  const css = fs.readFileSync(path.join(__dirname, "../App.css"), "utf8");
  const nav = fs.readFileSync(path.join(__dirname, "../lib/poDetailStageNav.js"), "utf8");

  test("Back lives in the global command bar and is not a full-width first row", () => {
    const commandBar = fs.readFileSync(
      path.join(__dirname, "../components/poDetail/PoGlobalCommandBar.jsx"),
      "utf8"
    );
    expect(commandBar).toMatch(/data-testid="back-button"/);
    expect(commandBar).toMatch(/Kembali ke Daftar PO/);
    expect(detail).toMatch(/resolvePoBackTarget/);
    expect(detail).not.toMatch(/navigate\(-1\)/);
    expect((detail + commandBar).split("data-testid=\"back-button\"").length - 1).toBe(1);
  });

  test("Detail PO uses a dedicated sticky StageFlow wrapper", () => {
    expect(detail).toMatch(/po-stage-bar-sticky/);
    expect(css).toMatch(/\.po-stage-bar-sticky\{[^}]*position:sticky/);
    expect(css).toMatch(/--po-stageflow-sticky-top/);
    expect(css).toMatch(/scroll-margin-top/);
  });

  test("shared StageBar default remains non-sticky and POCard does not opt in", () => {
    expect(bar).not.toMatch(/position:\s*sticky/);
    expect(card).toMatch(/<StageBar stage=\{order\.current_stage\} order=\{order\} \/>/);
    expect(card).not.toMatch(/po-stage-bar-sticky/);
    expect(card).not.toMatch(/onStageClick/);
  });

  test("stage click selects a display-only workspace and does not open a stage drawer or call API", () => {
    expect(detail).toMatch(/selectWorkflowStage/);
    expect(detail).toMatch(/syncPoDetailStageQuery/);
    expect(detail).not.toMatch(/setStageDrawer/);
    expect(detail).not.toMatch(/StageDetailDrawer/);
    expect(nav).not.toMatch(/api\./);
    expect(nav).not.toMatch(/current_stage/);
    expect(nav).toMatch(/scrollIntoView/);
  });

  test("Payment and Approval Payment resolve to the shared payment section id", () => {
    expect(detail).toMatch(/id="po-stage-payment"/);
    expect(nav).toMatch(/Payment:\s*"po-stage-payment"/);
    expect(nav).toMatch(/"Approval Payment":\s*"po-stage-payment"/);
  });

  test("later stages have explicit anchors", () => {
    expect(detail).toMatch(/id="po-stage-packing"/);
    expect(detail).toMatch(/id="po-stage-surat-jalan"/);
    expect(detail).toMatch(/id="po-stage-loading"/);
    expect(detail).toMatch(/id="po-stage-pengiriman"/);
  });

  test("15-stage order is unchanged", () => {
    expect(STAGES).toEqual([
      "Order Masuk",
      "Design",
      "Approval Design",
      "Payment",
      "Approval Payment",
      "Penjadwalan",
      "Screener",
      "Persiapan Helper",
      "Operator",
      "Konfirmasi Sample",
      "Cetak",
      "Packing",
      "Surat Jalan",
      "Loading",
      "Pengiriman",
    ]);
  });
});
