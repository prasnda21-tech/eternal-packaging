import { act } from "react";
import { createRoot } from "react-dom/client";
import { getDocument } from "pdfjs-dist";
import SalesPwaPdfViewer from "./SalesPwaPdfViewer";

jest.mock("pdfjs-dist", () => ({
  GlobalWorkerOptions: {},
  getDocument: jest.fn(),
}));

global.IS_REACT_ACT_ENVIRONMENT = true;

test("renders every PDF page into internal canvases", async () => {
  const render = jest.fn(() => ({ promise: Promise.resolve(), cancel: jest.fn() }));
  const pdf = {
    numPages: 2,
    getPage: jest.fn(async () => ({
      getViewport: ({ scale }) => ({ width: 600 * scale, height: 800 * scale }),
      render,
      cleanup: jest.fn(),
    })),
  };
  getDocument.mockReturnValue({ promise: Promise.resolve(pdf), destroy: jest.fn() });

  const blob = {
    arrayBuffer: jest.fn(async () => new ArrayBuffer(8)),
  };
  const container = document.createElement("div");
  const root = createRoot(container);
  const getContext = jest.spyOn(HTMLCanvasElement.prototype, "getContext").mockReturnValue({});

  await act(async () => {
    root.render(<SalesPwaPdfViewer blob={blob} label="Surat Jalan" />);
    await new Promise((resolve) => setTimeout(resolve, 0));
  });

  expect(getDocument).toHaveBeenCalledWith(expect.objectContaining({
    isEvalSupported: false,
    useWorkerFetch: false,
  }));
  expect(container.querySelectorAll("canvas")).toHaveLength(2);
  expect(pdf.getPage).toHaveBeenCalledTimes(2);
  expect(render).toHaveBeenCalledTimes(2);

  act(() => root.unmount());
  getContext.mockRestore();
});
