import fs from "fs";
import path from "path";
import { STAGES } from "../lib/constants";
import {
  getStagePresentation,
  resolveOperationalFocus,
  shouldExpandStageByDefault,
} from "../lib/poDetailPresentation";

describe("Detail PO industrial information architecture", () => {
  const detail = fs.readFileSync(path.join(__dirname, "PODetail.jsx"), "utf8");
  const css = fs.readFileSync(path.join(__dirname, "../App.css"), "utf8");
  const bar = fs.readFileSync(path.join(__dirname, "../components/StageBar.jsx"), "utf8");
  const timeline = fs.readFileSync(path.join(__dirname, "../components/Timeline.jsx"), "utf8");
  const stageSection = fs.readFileSync(
    path.join(__dirname, "../components/poDetail/PoStageSection.jsx"),
    "utf8"
  );
  const accordionCtx = fs.readFileSync(
    path.join(__dirname, "../components/poDetail/PoStageAccordionContext.jsx"),
    "utf8"
  );
  const focus = fs.readFileSync(
    path.join(__dirname, "../components/poDetail/PoDetailFocusPanel.jsx"),
    "utf8"
  );
  const presentation = fs.readFileSync(
    path.join(__dirname, "../lib/poDetailPresentation.js"),
    "utf8"
  );

  test("preserves primary actions and permissions wiring", () => {
    expect(detail).toMatch(/data-testid="back-button"/);
    expect(detail).toMatch(/data-testid="po-refresh-button"/);
    expect(detail).toMatch(/data-testid="toggle-urgent-button"/);
    expect(detail).toMatch(/data-testid="toggle-buffer-stock-button"/);
    expect(detail).toMatch(/data-testid="po-edit-info"/);
    expect(detail).toMatch(/data-testid="po-atur-lokasi"/);
    expect(detail).toMatch(/data-testid="copy-tracking-link"/);
    expect(detail).toMatch(/data-testid="btn-proses-pengiriman-pesanan"/);
    expect(detail).toMatch(/data-testid="btn-repeat-order-detail"/);
    expect(detail).toMatch(/data-testid="btn-buat-faktur-penjualan"/);
    expect(detail).toMatch(/data-testid="rollback-button"/);
    expect(detail).toMatch(/data-testid="cancel-po-button"/);
    expect(detail).toMatch(/data-testid="delete-po-button"/);
    expect(detail).toMatch(/hasPerm\("workflow\.rollback"\)/);
    expect(detail).toMatch(/hasPerm\("po\.cancel"\)/);
    expect(detail).toMatch(/hasPerm\("po\.delete"\)/);
    expect(detail).toMatch(/hasPerm\("po\.edit"\)/);
    expect(detail).toMatch(/ConfirmActionModal/);
    expect(detail).toMatch(/delete-po-modal/);
  });

  test("dangerous actions live in expandable secondary area", () => {
    expect(detail).toMatch(/po-actions-danger-toggle/);
    expect(detail).toMatch(/po-actions-danger-body/);
    expect(detail).toMatch(/Aksi berbahaya/);
    const admin = detail.slice(detail.indexOf("po-actions-admin"));
    expect(admin.indexOf("rollback-button")).toBeGreaterThan(admin.indexOf("po-actions-danger-body"));
  });

  test("focus panel is presentational and jumps by display-only stage selection", () => {
    expect(detail).toMatch(/PoDetailFocusPanel/);
    expect(focus).toMatch(/Tahap aktif saat ini/);
    expect(focus).toMatch(/resolveOperationalFocus/);
    expect(focus).toMatch(/onGotoStage/);
    expect(focus).not.toMatch(/api\./);
    expect(focus).not.toMatch(/current_stage/);
    expect(presentation).toMatch(/resolveStageState/);
    expect(presentation).not.toMatch(/api\./);
  });

  test("stage sections use aria-expanded, stay collapsed by default, keep children via hidden", () => {
    expect(stageSection).toMatch(/aria-expanded=\{open\}/);
    expect(stageSection).toMatch(/hidden=\{!open\}/);
    expect(stageSection).toMatch(/po-stage-toggle-/);
    expect(stageSection).toMatch(/usePoStageAccordion/);
    expect(accordionCtx).toMatch(/PoStageAccordionProvider/);
    expect(accordionCtx).toMatch(/openStageIndex/);
    expect(stageSection).not.toMatch(/shouldExpandStageByDefault/);
    expect(stageSection).not.toMatch(/keepExpanded/);
    expect(detail).toMatch(/PoStageAccordionProvider/);
    expect(detail).not.toMatch(/keepExpanded=/);
  });

  test("authoritative Tanggal Pengiriman remains; Deadline UI absent", () => {
    expect(detail).toMatch(/data-testid="detail-tanggal-pengiriman"/);
    expect(detail).toMatch(/Tanggal Pengiriman/);
    expect(detail).toMatch(/header-tanggal-pengiriman/);
    expect(detail).not.toMatch(/DeliveryDeadlinePanel/);
    expect(detail).not.toMatch(/data-testid="detail-deadline"/);
    expect(detail).not.toMatch(/>Edit Deadline</);
    expect(detail).not.toMatch(/>Jadwal Manual</);
    expect(detail).not.toMatch(/OVERDUE — deadline telah terlewat/);
  });

  test("workflow order and numbering unchanged", () => {
    expect(STAGES).toHaveLength(15);
    expect(STAGES[0]).toBe("Order Masuk");
    expect(STAGES[11]).toBe("Packing");
    expect(STAGES[13]).toBe("Loading");
    expect(STAGES[14]).toBe("Pengiriman");
    for (let i = 0; i < 15; i++) {
      expect(detail).toMatch(new RegExp(`data-workflow-stage="${i}"`));
    }
  });

  test("line items use compact table with Ganti Barang control", () => {
    expect(detail).toMatch(/line-items-table/);
    expect(detail).toMatch(/LineProductReplaceControl/);
    expect(detail).toMatch(/line-item-process-/);
    expect(detail).toMatch(/Cetak|Non-Cetak/);
    expect(css).toMatch(/line-items-table/);
  });

  test("grouped workflow labels A–D with single Persiapan Produksi header", () => {
    expect(detail).toMatch(/A · DESIGN &amp; PAYMENT/);
    expect(detail).toMatch(/B · PERSIAPAN PRODUKSI/);
    expect(detail).toMatch(/C · PRODUKSI/);
    expect(detail).toMatch(/D · QC DAN PENGIRIMAN/);
    expect(detail).not.toMatch(/D · FULFILLMENT/);
    expect(detail).not.toMatch(/B · Screener/);
    expect(detail).not.toMatch(/B · SCREENER/);
    expect((detail.match(/B · PERSIAPAN PRODUKSI/g) || []).length).toBe(1);
    expect(css).toMatch(/\.po-detail-industrial \.workflow-group-label/);
    expect(css).toMatch(/#0b3d40/);
  });

  test("history defaults collapsed with accessible toggle", () => {
    expect(detail).toMatch(/defaultOpen=\{false\}/);
    expect(timeline).toMatch(/order-timeline-toggle/);
    expect(timeline).toMatch(/aria-expanded=\{open\}/);
    expect(timeline).toMatch(/hidden=\{!open\}/);
    expect(timeline).toMatch(/Alur Pekerjaan/);
  });

  test("StageBar labeled rail uses presentation tones without changing indexes", () => {
    expect(bar).toMatch(/getStagePresentation/);
    expect(bar).toMatch(/stageBarClassForTone/);
    expect(bar).toMatch(/data-stage-tone/);
    expect(css).toMatch(/stage-bar-seg\.waiting/);
    expect(css).toMatch(/stage-bar-seg\.not-required/);
  });

  test("presentation tones remain; default expand helper always false", () => {
    const printActive = {
      requires_printing: true,
      current_stage: 10,
      sample_approved: true,
      print_status: "BELUM SELESAI CETAK",
      print_completed_qty: 1,
      print_qty_target: 10,
      operator_splits: [{ quantity: 10, qty_completed: 1 }],
      helper_prep: { qty_prepared: 10 },
      screener_info: { screen_qty: 10 },
      design_status: "Approved",
      payment_status: "Approved",
      design_files: [{}],
      payment_files: [{}],
      sample_files: [{}],
    };
    expect(getStagePresentation(10, printActive, 10).tone).toBe("active");
    expect(shouldExpandStageByDefault(10, printActive, 10)).toBe(false);
    expect(getStagePresentation(14, printActive, 10).tone).toBe("upcoming");
    expect(shouldExpandStageByDefault(14, printActive, 10)).toBe(false);
    expect(getStagePresentation(3, printActive, 10).tone).toBe("completed");
    expect(shouldExpandStageByDefault(3, printActive, 10)).toBe(false);

    const nonPrint = {
      requires_printing: false,
      current_stage: 11,
      design_status: "Approved",
      payment_status: "Approved",
      design_files: [{}],
      payment_files: [{}],
      operator_splits: [],
      packing_info: { qty_packed: 0 },
    };
    expect(getStagePresentation(6, nonPrint, 11).tone).toBe("not-required");
    expect(shouldExpandStageByDefault(6, nonPrint, 11)).toBe(false);
    expect(shouldExpandStageByDefault(6, nonPrint, 11, { hasError: true })).toBe(false);

    const focus = resolveOperationalFocus({ ...printActive, hold: true, hold_reasons: ["x"] });
    expect(focus.tone).toBe("blocked");
    expect(focus.holdReasons).toEqual(["x"]);
  });

  test("production/packing/SJ/loading/delivery panels remain mounted", () => {
    expect(detail).toMatch(/AttachmentsPanel/);
    expect(detail).toMatch(/approve-design-button/);
    expect(detail).toMatch(/approve-payment-button/);
    expect(detail).toMatch(/OperatorSplitPanel/);
    expect(detail).toMatch(/PackingPanel/);
    expect(detail).toMatch(/SuratJalanPanel/);
    expect(detail).toMatch(/allowUpload=\{false\}/);
    expect(detail).toMatch(/D · QC DAN PENGIRIMAN/);
    expect(detail).toMatch(/LoadingPanel/);
    expect(detail).toMatch(/DeliveryProofPanel/);
    expect(detail).toMatch(/HelperPrepPanel/);
    expect(detail).toMatch(/ProductionRoutePanel/);
  });
});
