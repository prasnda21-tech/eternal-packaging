import fs from "fs";
import path from "path";
import { mobileSuratJalanViewerHref, safeMobileDocumentReturn } from "../lib/suratJalan";

test("Loading and Pengiriman use the internal SJ route without target blank", () => {
  const loading = fs.readFileSync(path.join(__dirname, "workspaces/MobileLoadingWorkspace.jsx"), "utf8");
  const delivery = fs.readFileSync(path.join(__dirname, "components/MobileDeliverySjModal.jsx"), "utf8");
  expect(loading).toMatch(/mobileSuratJalanViewerHref\(order\.id, activeSj\.id, "loading"\)/);
  expect(delivery).toMatch(/mobileSuratJalanViewerHref\(order\.id, fileId, "delivery"\)/);
  expect(loading).not.toMatch(/target="_blank"/);
  expect(delivery).not.toMatch(/target="_blank"/);
});

test("viewer href preserves exact operational return URL", () => {
  expect(mobileSuratJalanViewerHref("po 1", "sj/2", "delivery", {
    pathname: "/m/delivery-schedule",
    search: "?ws=delivery&shipment_id=sj-2",
  })).toBe(
    "/m/orders/po%201/surat-jalan/sj%2F2?ws=delivery&return_to=%2Fm%2Fdelivery-schedule%3Fws%3Ddelivery%26shipment_id%3Dsj-2",
  );
  expect(safeMobileDocumentReturn("/m/orders/po-1?ws=loading")).toBe("/m/orders/po-1?ws=loading");
  expect(safeMobileDocumentReturn("https://evil.example/")).toBe("");
  expect(safeMobileDocumentReturn("//evil.example/")).toBe("");
});

test("operational viewer fetches the authorized SJ blob and renders PDF.js internally", () => {
  const viewer = fs.readFileSync(path.join(__dirname, "MobileSuratJalanViewerPage.jsx"), "utf8");
  const app = fs.readFileSync(path.join(__dirname, "../App.js"), "utf8");
  expect(viewer).toMatch(/fetchSuratJalanBlob\(id, fileId/);
  expect(viewer).toMatch(/<SalesPwaPdfViewer blob=\{viewer\.blob\}/);
  expect(viewer).toMatch(/window\.history\.pushState/);
  expect(app).toMatch(/path="\/m\/orders\/:id\/surat-jalan\/:fileId"/);
});
