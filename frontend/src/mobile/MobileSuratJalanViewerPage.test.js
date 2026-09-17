import fs from "fs";
import path from "path";
import { act } from "react";
import { createRoot } from "react-dom/client";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { fetchSuratJalanBlob, mobileSuratJalanViewerHref } from "../lib/suratJalan";
import MobileSuratJalanViewerPage, { safeMobileReturn } from "./MobileSuratJalanViewerPage";

jest.mock("../lib/suratJalan", () => {
  const actual = jest.requireActual("../lib/suratJalan");
  return { ...actual, fetchSuratJalanBlob: jest.fn() };
});
jest.mock("../sales/SalesPwaPdfViewer", () => function PdfMock() {
  return <div data-testid="internal-pdf-canvas">PDF internal</div>;
});

global.IS_REACT_ACT_ENVIRONMENT = true;

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
  expect(safeMobileReturn("/m/orders/po-1?ws=loading")).toBe("/m/orders/po-1?ws=loading");
  expect(safeMobileReturn("https://evil.example/")).toBe("");
  expect(safeMobileReturn("//evil.example/")).toBe("");
});

test("loads the authorized SJ blob into the internal PDF canvas and returns to task", async () => {
  const blob = new Blob(["%PDF"], { type: "application/pdf" });
  fetchSuratJalanBlob.mockResolvedValue(blob);
  const originalCreate = URL.createObjectURL;
  const originalRevoke = URL.revokeObjectURL;
  URL.createObjectURL = jest.fn(() => "blob:surat-jalan");
  URL.revokeObjectURL = jest.fn();

  const container = document.createElement("div");
  const root = createRoot(container);
  await act(async () => {
    root.render(
      <MemoryRouter initialEntries={["/m/orders/po-1/surat-jalan/sj-1?ws=delivery&return_to=%2Fm%2Fdelivery-schedule%3Fws%3Ddelivery"]}>
        <Routes>
          <Route path="/m/orders/:id/surat-jalan/:fileId" element={<MobileSuratJalanViewerPage />} />
          <Route path="/m/delivery-schedule" element={<div data-testid="returned-task">Task Pengiriman</div>} />
        </Routes>
      </MemoryRouter>,
    );
    await Promise.resolve();
    await Promise.resolve();
  });

  expect(fetchSuratJalanBlob).toHaveBeenCalledWith("po-1", "sj-1", { filename: "surat-jalan.pdf" });
  expect(container.querySelector('[data-testid="internal-pdf-canvas"]')).toBeTruthy();
  await act(async () => {
    container.querySelector('[data-testid="mobile-sj-document-back"]').click();
  });
  expect(container.querySelector('[data-testid="returned-task"]')).toBeTruthy();

  act(() => root.unmount());
  URL.createObjectURL = originalCreate;
  URL.revokeObjectURL = originalRevoke;
});
