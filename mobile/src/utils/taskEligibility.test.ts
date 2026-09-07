import {
  cetakTaskReady,
  defaultTaskWorkspace,
  deliveryTaskReady,
  helperTaskReady,
  isActionableDivisionTask,
  loadingTaskReady,
  packingListHasForeignStages,
  packingTaskReady,
  pickSjFile,
  sampleUploadTaskReady,
  screenerTaskReady,
  showsMergedTaskList,
  taskBelongsToWorkspace,
} from "./taskEligibility";
import { helperModel, loadingModel, packingModel, screenerModel } from "./workModels";

function assertEqual(actual: unknown, expected: unknown, label: string) {
  if (actual !== expected) {
    throw new Error(`${label}: expected ${String(expected)}, got ${String(actual)}`);
  }
}

const scheduled = [{ id: "s1", line_idx: 0, quantity: 1000, operator_email: "op@x.com" }];
const cupA = { name: "Cup A", quantity: 1000, production_process: "print" };

{
  const unscheduled = { requires_printing: true, operator_splits: [], workflow_ready: { screener: false, helper: false } };
  assertEqual(screenerTaskReady(unscheduled).actionable, false, "screener unscheduled");
  assertEqual(helperTaskReady(unscheduled).actionable, false, "helper unscheduled");
  assertEqual(screenerModel(unscheduled).gateLocked, true, "screener model locked");
}

{
  const ready = {
    requires_printing: true,
    operator_splits: scheduled,
    workflow_ready: { screener: true, helper: true },
  };
  assertEqual(screenerTaskReady(ready).actionable, true, "screener scheduled");
  assertEqual(helperTaskReady(ready).actionable, true, "helper scheduled");
}

{
  const helperOnly = {
    requires_printing: true,
    operator_splits: scheduled,
    workflow_ready: { screener: false, helper: true },
    screener_info: {},
    helper_prep: { qty_prepared: 100, items: [{ line_idx: 0, qty_prepared: 100 }] },
    line_items: [cupA],
  };
  assertEqual(helperTaskReady(helperOnly).actionable, true, "helper independent of screener");
  assertEqual(helperModel(helperOnly).gateLocked, false, "helper model not blocked by screener");
}

{
  const notReady = {
    requires_printing: true,
    operator_splits: [],
    workflow_ready: { sample_upload: false },
  };
  assertEqual(sampleUploadTaskReady(notReady).actionable, false, "sample prereq incomplete");
}

{
  const ready = {
    requires_printing: true,
    operator_splits: scheduled,
    workflow_ready: { sample_upload: true },
    helper_prep: { qty_prepared: 1000 },
  };
  assertEqual(sampleUploadTaskReady(ready).actionable, true, "sample ready flag");
}

{
  const fallback = {
    requires_printing: true,
    operator_splits: scheduled,
    helper_prep: { qty_prepared: 500 },
  };
  assertEqual(sampleUploadTaskReady(fallback).actionable, true, "sample fallback helper+schedule");
  const noHelper = { requires_printing: true, operator_splits: scheduled, helper_prep: {} };
  assertEqual(sampleUploadTaskReady(noHelper).actionable, false, "sample fallback needs helper");
}

{
  const blocked = { requires_printing: true, sample_approved: false, workflow_ready: { print_qty: false } };
  assertEqual(cetakTaskReady(blocked).actionable, false, "cetak sample not approved");
  const go = {
    requires_printing: true,
    sample_approved: true,
    workflow_ready: { print_qty: true },
  };
  assertEqual(cetakTaskReady(go).actionable, true, "cetak backend ready");
}

{
  const early = { workflow_ready: { packing: false }, packing_prereq_reasons: ["Qty Screen belum diinput."] };
  assertEqual(packingTaskReady(early).actionable, false, "packing prereq");
  const go = { workflow_ready: { packing: true } };
  assertEqual(packingTaskReady(go).actionable, true, "packing eligible");
}

{
  const sameName = packingModel({
    workflow_ready: { packing: true },
    packing_lines: [
      { line_idx: 0, packed: 0, available: 100, remaining: 100 },
      { line_idx: 1, packed: 0, available: 80, remaining: 80 },
    ],
    line_items: [
      { name: "Cup A", quantity: 100, production_process: "print" },
      { name: "Cup A", quantity: 80, production_process: "print" },
    ],
  });
  assertEqual(sameName.lines.length, 2, "packing same-name lines");
  assertEqual(sameName.lines[0].lineIdx, 0, "packing line 0");
  assertEqual(sameName.lines[1].lineIdx, 1, "packing line 1");
  assertEqual(sameName.gateLocked, false, "packing eligible unlocked");
}

{
  const packedNoSj = {
    surat_jalan_files: [],
    packing_info: { lines: [{ line_idx: 0, packed_qty: 100 }] },
    shipment_lines: [{ line_idx: 0, packed: 100, available_loading: 100 }],
    line_items: [cupA],
  };
  assertEqual(loadingTaskReady(packedNoSj).actionable, false, "loading no SJ");
}

{
  const sjNoPack = {
    surat_jalan_files: [{ id: "sj-1", name: "SJ.pdf" }],
    packing_info: { lines: [{ line_idx: 0, packed_qty: 0 }] },
    shipment_lines: [{ line_idx: 0, packed: 0, loaded: 0, available_loading: 0 }],
    line_items: [cupA],
  };
  assertEqual(loadingTaskReady(sjNoPack).actionable, false, "loading SJ packed 0");
  assertEqual(loadingModel(sjNoPack).gateLocked, true, "loading model SJ-only locked");
}

{
  const go = {
    surat_jalan_files: [{ id: "sj-1", name: "SJ.pdf" }],
    packing_info: { lines: [{ line_idx: 0, packed_qty: 80 }] },
    shipment_lines: [{ line_idx: 0, packed: 80, loaded: 0, available_loading: 80 }],
    line_items: [cupA],
  };
  assertEqual(loadingTaskReady(go).actionable, true, "loading SJ + packed");
  assertEqual(loadingModel(go).gateLocked, false, "loading model ready");
}

{
  const multi = {
    surat_jalan_files: [
      { id: "sj-a", name: "A.pdf" },
      { id: "sj-b", name: "B.pdf" },
    ],
    packing_info: { lines: [{ line_idx: 0, packed_qty: 80 }] },
    shipment_lines: [{ line_idx: 0, packed: 80, available_loading: 80 }],
    line_items: [cupA],
  };
  const model = loadingModel(multi, "sj-b");
  assertEqual(model.selectedSjId, "sj-b", "loading selected SJ id");
  assertEqual(pickSjFile(multi.surat_jalan_files, "sj-b")?.id, "sj-b", "pick stable id");
  assertEqual(pickSjFile(multi.surat_jalan_files, "missing")?.id, "sj-a", "pick fallback first id");
}

{
  const blocked = { eligible_delivery_qty: 0, delivery_proofs: [] };
  assertEqual(deliveryTaskReady(blocked).actionable, false, "delivery not eligible");
  const go = { eligible_delivery_qty: 40, delivery_proofs: [] };
  assertEqual(deliveryTaskReady(go).actionable, true, "delivery eligible");
  const proofs = { eligible_delivery_qty: 0, delivery_proofs: [{ id: "p1" }, { id: "p2" }] };
  assertEqual(deliveryTaskReady(proofs).actionable, true, "delivery proofs continue");
}

{
  assertEqual(isActionableDivisionTask({ division: "screener", remaining_qty: 0 }), false, "list screener done");
  assertEqual(isActionableDivisionTask({ division: "screener", remaining_qty: 10 }), true, "list screener open");
  assertEqual(isActionableDivisionTask({ division: "helper", remaining_qty: 5 }), true, "list helper open");
  assertEqual(isActionableDivisionTask({ division: "loading", remaining_qty: 0 }), false, "list loading no packed remain");
  assertEqual(isActionableDivisionTask({ division: "loading", remaining_qty: 20 }), true, "list loading packed remain");
  assertEqual(isActionableDivisionTask({ division: "surat_jalan", remaining_qty: 1 }), false, "list SJ web-only");
  assertEqual(isActionableDivisionTask({ division: "terkirim", status: "selesai" }), false, "list delivered hidden");
  assertEqual(isActionableDivisionTask({ division: "terkirim", status: "siap" }), true, "list delivery siap");
  assertEqual(isActionableDivisionTask({ division: "packing", remaining_qty: 3, hold: true }), false, "list hold hidden");
  assertEqual(isActionableDivisionTask({ division: "sample_upload" }), true, "list sample upload");
}

{
  const mixed = [
    { division: "helper", remaining_qty: 10 },
    { division: "packing", remaining_qty: 4 },
    { division: "loading", remaining_qty: 2 },
  ];
  const packingOnly = mixed.filter((t) => taskBelongsToWorkspace(t, "packing"));
  assertEqual(packingOnly.length, 1, "packing workspace one row");
  assertEqual(packingOnly[0].division, "packing", "packing workspace id");
  assertEqual(packingListHasForeignStages(packingOnly), false, "packing list no cup/loading");
  assertEqual(taskBelongsToWorkspace({ division: "helper" }, "packing"), false, "cup not in packing");
  assertEqual(taskBelongsToWorkspace({ division: "loading" }, "packing"), false, "loading not in packing");
  assertEqual(taskBelongsToWorkspace({ division: "loading" }, "terkirim"), false, "loading not in delivery");
  assertEqual(taskBelongsToWorkspace({ division: "operator" }, "sample_upload"), false, "cetak not in sample");
  assertEqual(taskBelongsToWorkspace({ division: "screener" }, "all"), false, "all is not a merge");
  assertEqual(showsMergedTaskList(["helper", "packing", "loading"]), false, "helper-family no merge tab");
  assertEqual(showsMergedTaskList(["screener", "helper", "packing"]), false, "no merge for other roles");
  assertEqual(showsMergedTaskList(["loading", "terkirim"]), false, "driver no merge");
  assertEqual(defaultTaskWorkspace(["helper", "packing", "loading"]), "helper", "helper default first workspace");
  assertEqual(defaultTaskWorkspace(["screener", "packing"]), "screener", "first workspace not all");
  assertEqual(defaultTaskWorkspace(["loading", "terkirim"]), "loading", "driver default loading");
}

console.log("taskEligibility tests ok");
