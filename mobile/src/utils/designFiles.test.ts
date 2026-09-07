import {
  canOpenDesignFile,
  currentDesignFile,
  designFileIsImage,
  designFileIsPdf,
  designFileLabel,
  designFileOpenRef,
  designFilePreviewUri,
  designFiles,
  designStatusLabel,
} from "./designFiles";

function assertEqual(actual: unknown, expected: unknown, label: string) {
  if (actual !== expected) {
    throw new Error(`${label}: expected ${String(expected)}, got ${String(actual)}`);
  }
}

{
  assertEqual(designFiles(null).length, 0, "empty null");
  assertEqual(designFiles({}).length, 0, "empty missing");
  assertEqual(currentDesignFile({ design_files: [] }), null, "no current");
}

{
  const order = {
    design_status: "Approved",
    design_files: [
      { id: "d1", name: "v1.png", data_url: "data:image/png;base64,aa" },
      { id: "d2", name: "v2.pdf", content_type: "application/pdf", data_url: "data:application/pdf;base64,bb" },
    ],
  };
  assertEqual(designFiles(order).length, 2, "two files");
  assertEqual(currentDesignFile(order)?.id, "d2", "latest is last");
  assertEqual(designFileLabel(currentDesignFile(order)), "v2.pdf", "label");
  assertEqual(designStatusLabel(order), "Approved", "status existing");
  assertEqual(designFileIsPdf(currentDesignFile(order)), true, "pdf");
  assertEqual(designFileIsImage(order.design_files[0]), true, "png");
  assertEqual(canOpenDesignFile(currentDesignFile(order)), true, "openable");
}

{
  assertEqual(designFilePreviewUri({ data_url: "data:image/jpeg;base64,xx" })?.startsWith("data:image"), true, "preview data");
  assertEqual(designFilePreviewUri({ url: "https://example.test/d.png" }), "https://example.test/d.png", "preview http");
  assertEqual(designFilePreviewUri({ storage_path: "app/design/x.png" }), null, "no raw path preview");
  assertEqual(designFileOpenRef({ storage_path: "app/design/x.png" }).kind, "storage", "storage open");
  assertEqual(designFileOpenRef({}).kind, null, "unopenable");
  assertEqual(designStatusLabel({ design_bypassed: true }), "BYPASS", "bypass label");
}

console.log("designFiles tests ok");
