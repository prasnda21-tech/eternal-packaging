import { makeHasPerm } from "./permissions";
import {
  canShowSamplePanel,
  canShowSampleUploadActions,
  orderRequiresPrinting,
  pickerUserMessage,
  sampleAttachmentPayload,
  sampleFiles,
  sampleGateMessage,
  sampleUploadErrorMessage,
  SAMPLE_KIND,
  SAMPLE_NOT_READY_MSG,
  shouldUploadPicked,
} from "./sampleUpload";
import { operatorPanelModel } from "./workModels";

function assertEqual(actual: unknown, expected: unknown, label: string) {
  if (actual !== expected) {
    throw new Error(`${label}: expected ${String(expected)}, got ${String(actual)}`);
  }
}

const printOrder = {
  requires_printing: true,
  workflow_ready: { sample_upload: true, operator: true },
  screener_info: { screen_qty: 1000 },
  operator_splits: [{ id: "s1", quantity: 1000, qty_completed: 0, operator_email: "op@x.com" }],
  line_items: [{ name: "Cup A", quantity: 1000, production_process: "print" }],
  sample_files: [],
};

const hasSample = makeHasPerm(["operator.sample_upload", "operator.update"]);
const noSample = makeHasPerm(["operator.update"]);

{
  assertEqual(canShowSamplePanel(hasSample, printOrder), true, "A panel");
  assertEqual(canShowSampleUploadActions(hasSample, printOrder), true, "A actions");
  assertEqual(sampleAttachmentPayload("s.jpg", "data:image/jpeg;base64,xx").kind, SAMPLE_KIND, "A kind");
}

{
  assertEqual(canShowSamplePanel(noSample, printOrder), false, "B no panel");
  assertEqual(canShowSampleUploadActions(noSample, printOrder), false, "B no actions");
}

{
  const notReady = {
    ...printOrder,
    workflow_ready: { sample_upload: false, operator: false },
    screener_info: {},
  };
  assertEqual(canShowSamplePanel(hasSample, notReady), true, "C panel visible");
  assertEqual(canShowSampleUploadActions(hasSample, notReady), false, "C actions hidden");
  assertEqual(sampleGateMessage(notReady), SAMPLE_NOT_READY_MSG, "C default gate");
  assertEqual(
    sampleGateMessage({ ...notReady, screener_gate_reason: "Qty Screener harus diisi sebelum Cetak Sample." }),
    "Qty Screener harus diisi sebelum Cetak Sample.",
    "C backend gate"
  );
}

{
  const nonPrint = {
    ...printOrder,
    requires_printing: false,
    workflow_ready: { sample_upload: false },
    line_items: [{ name: "Cup B", quantity: 100, production_process: "non_print" }],
  };
  assertEqual(orderRequiresPrinting(nonPrint), false, "non-print flag");
  assertEqual(canShowSamplePanel(hasSample, nonPrint), false, "non-print no panel");
  assertEqual(canShowSampleUploadActions(hasSample, nonPrint), false, "non-print no actions");
}

{
  assertEqual(shouldUploadPicked({ status: "ok", asset: { uri: "file://x" } as any }), true, "D camera ok");
  assertEqual(shouldUploadPicked({ status: "cancel" }), false, "F camera cancel");
  assertEqual(pickerUserMessage({ status: "cancel" }), null, "F cancel silent");
  assertEqual(
    pickerUserMessage({ status: "denied", message: "Izin kamera ditolak. Aktifkan izin kamera di pengaturan perangkat, lalu coba lagi." }) != null,
    true,
    "D denied message"
  );
}

{
  assertEqual(shouldUploadPicked({ status: "ok", asset: { uri: "file://g" } as any }), true, "E gallery ok");
  assertEqual(shouldUploadPicked({ status: "cancel" }), false, "F gallery cancel");
}

{
  const withFile = {
    ...printOrder,
    sample_files: [{ id: "f1", name: "s.jpg", data_url: "data:image/jpeg;base64,xx" }],
  };
  assertEqual(sampleFiles(withFile).length, 1, "G files visible");
  assertEqual(sampleFiles(withFile)[0].id, "f1", "G file id");
}

{
  assertEqual(
    sampleUploadErrorMessage({ status: 403, detail: "Anda tidak punya permission 'operator.sample_upload'" }),
    "Anda tidak punya permission 'operator.sample_upload'",
    "H 403 detail"
  );
  assertEqual(sampleUploadErrorMessage({ status: 403 }), "Tidak punya izin upload sampel.", "H 403 fallback");
  assertEqual(sampleUploadErrorMessage({ status: 401 }), "Sesi berakhir. Silakan masuk kembali.", "401");
  assertEqual(sampleUploadErrorMessage({ message: "Network request failed" }), "Koneksi gagal. Coba lagi.", "network");
  assertEqual(sampleUploadErrorMessage(new Error("Ukuran file maksimal 4MB")), "Ukuran file maksimal 4MB", "invalid file");
}

{
  const m = operatorPanelModel(
    {
      sample_approved: true,
      requires_printing: true,
      line_items: [{ name: "Cup A", quantity: 1000, production_process: "print" }],
      operator_splits: [
        {
          id: "split-a",
          line_idx: 0,
          operator_email: "op@x.com",
          operator_user_id: "u-op",
          quantity: 1000,
          qty_completed: 0,
          progress_entries: [],
        },
      ],
    },
    { email: "op@x.com", id: "u-op" }
  );
  assertEqual(m.target, 1000, "I target");
  assertEqual(m.done, 0, "I done");
  assertEqual(m.remaining, 1000, "I remaining");
  assertEqual(m.status === "Selesai", false, "I not selesai");
}

console.log("sampleUpload tests ok");
