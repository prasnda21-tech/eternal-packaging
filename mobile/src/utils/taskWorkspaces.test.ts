const fs = require("fs") as typeof import("fs");
const path = require("path") as typeof import("path");
import { makeHasPerm } from "./permissions";
import { isActionableDivisionTask, showsMergedTaskList, taskBelongsToWorkspace } from "./taskEligibility";
import {
  availableTaskWorkspaces,
  canRenderWorkPanel,
  hasSemuaMenuLabel,
  persistSelectedWorkspace,
  resolveNavTaskContext,
  scopedWorkPanelIds,
  taskContextBreadcrumb,
  taskEmptyMessage,
  tasksForSelectedWorkspace,
} from "./taskWorkspaces";

function assertEqual(actual: unknown, expected: unknown, label: string) {
  if (actual !== expected) {
    throw new Error(`${label}: expected ${String(expected)}, got ${String(actual)}`);
  }
}

{
  const has = makeHasPerm(["helper.prepare", "packing.update"]);
  const menus = availableTaskWorkspaces(has).map((w) => w.id);
  assertEqual(menus.includes("helper"), true, "1 helper cup menu");
  assertEqual(menus.includes("packing"), true, "1 helper packing menu");
  assertEqual(menus.includes("loading"), false, "1 helper no loading without perm");
  assertEqual(hasSemuaMenuLabel(availableTaskWorkspaces(has).map((w) => w.label)), false, "7 no Semua label");
}

{
  const mixed = [
    { division: "helper", remaining_qty: 10 },
    { division: "packing", remaining_qty: 4 },
    { division: "loading", remaining_qty: 2 },
  ];
  const packing = tasksForSelectedWorkspace(mixed, "packing");
  assertEqual(packing.length, 1, "2 packing only count");
  assertEqual(packing[0].division, "packing", "2 packing only id");
  const cup = tasksForSelectedWorkspace(mixed, "helper");
  assertEqual(cup.length, 1, "3 cup only count");
  assertEqual(cup[0].division, "helper", "3 cup only id");
}

{
  const has = makeHasPerm(["loading.update", "delivery.update"]);
  const menus = availableTaskWorkspaces(has).map((w) => w.id);
  assertEqual(menus.includes("loading"), true, "4 driver loading");
  assertEqual(menus.includes("terkirim"), true, "4 driver pengiriman");
  const rows = [
    { division: "loading", remaining_qty: 8 },
    { division: "terkirim", status: "siap" },
  ];
  assertEqual(tasksForSelectedWorkspace(rows, "loading").every((t) => t.division === "loading"), true, "5 loading excludes pengiriman");
  assertEqual(tasksForSelectedWorkspace(rows, "terkirim").every((t) => t.division === "terkirim"), true, "6 pengiriman excludes loading");
}

{
  assertEqual(showsMergedTaskList(["helper", "packing"]), false, "7 no merge helper");
  assertEqual(taskBelongsToWorkspace({ division: "helper" }, "all"), false, "7 all not a workspace");
}

{
  const emptyPacking = tasksForSelectedWorkspace([{ division: "helper", remaining_qty: 3 }], "packing");
  assertEqual(emptyPacking.length, 0, "8 empty packing no fallback");
  assertEqual(taskEmptyMessage("packing"), "Belum ada tugas Packing", "8 packing empty copy");
}

{
  const helperRows = [{ division: "helper", remaining_qty: 3 }];
  const before = isActionableDivisionTask(helperRows[0]);
  const crafted = resolveNavTaskContext("task", "packing", ["helper", "packing"]);
  assertEqual(crafted, "packing", "9 nav context presentational");
  assertEqual(isActionableDivisionTask(helperRows[0]), before, "9 eligibility unchanged by route");
  assertEqual(taskBelongsToWorkspace(helperRows[0], "packing"), false, "9 packing route does not admit helper row");
}

{
  const has = makeHasPerm(["screen.update"]);
  const menus = availableTaskWorkspaces(has);
  assertEqual(menus.length, 1, "10 screener single menu");
  assertEqual(menus[0].id, "screener", "10 screener id");
  assertEqual(persistSelectedWorkspace("all", ["screener"]), "screener", "10 ignore all");
  assertEqual(resolveNavTaskContext("task", "screener", ["screener"]), "screener", "10 valid crumb context");
  assertEqual(taskContextBreadcrumb("screener"), "Tugas Saya › Screener", "10 breadcrumb");
}

{
  const hasOp = makeHasPerm(["operator.update", "operator.sample_upload"]);
  const opMenus = availableTaskWorkspaces(hasOp).map((w) => w.id);
  assertEqual(opMenus.includes("operator"), true, "operator cetak");
  assertEqual(opMenus.includes("sample_upload"), true, "operator sample");

  const hasCrm = makeHasPerm(["design.approve"]);
  assertEqual(availableTaskWorkspaces(hasCrm).map((w) => w.id).join(","), "design_approve", "crm approve only");

  assertEqual(resolveNavTaskContext(undefined, "packing", ["packing"]), null, "no fake from search");
  assertEqual(resolveNavTaskContext("task", "packing", ["helper"]), null, "no escalate packing");
}

{
  const scheduleSrc = fs.readFileSync(path.join(__dirname, "../../app/(app)/schedule.tsx"), "utf8");
  if (scheduleSrc.includes(">Semua<") || scheduleSrc.includes('"Semua"')) {
    throw new Error("7 schedule still has Semua");
  }
}

{
  const helperAllowed = ["helper", "packing", "loading"];
  assertEqual(scopedWorkPanelIds("packing", helperAllowed).join(","), "packing", "po packing only");
  assertEqual(canRenderWorkPanel("packing", "packing", helperAllowed), true, "po packing visible");
  assertEqual(canRenderWorkPanel("helper", "packing", helperAllowed), false, "po helper hidden");
  assertEqual(canRenderWorkPanel("loading", "packing", helperAllowed), false, "po loading hidden");

  assertEqual(scopedWorkPanelIds("helper", helperAllowed).join(","), "helper", "po cup only");
  assertEqual(canRenderWorkPanel("helper", "helper", helperAllowed), true, "po cup visible");
  assertEqual(canRenderWorkPanel("packing", "helper", helperAllowed), false, "po packing hidden on cup");
  assertEqual(canRenderWorkPanel("loading", "helper", helperAllowed), false, "po loading hidden on cup");

  assertEqual(scopedWorkPanelIds("loading", helperAllowed).join(","), "loading", "po loading only");
  assertEqual(canRenderWorkPanel("loading", "loading", helperAllowed), true, "po loading visible");
  assertEqual(canRenderWorkPanel("helper", "loading", helperAllowed), false, "po helper hidden on loading");
  assertEqual(canRenderWorkPanel("packing", "loading", helperAllowed), false, "po packing hidden on loading");
}

{
  const driverAllowed = ["loading", "terkirim"];
  assertEqual(canRenderWorkPanel("loading", "loading", driverAllowed), true, "driver loading visible");
  assertEqual(canRenderWorkPanel("terkirim", "loading", driverAllowed), false, "driver pengiriman hidden");
  assertEqual(canRenderWorkPanel("terkirim", "terkirim", driverAllowed), true, "driver pengiriman visible");
  assertEqual(canRenderWorkPanel("loading", "terkirim", driverAllowed), false, "driver loading hidden");
}

{
  const opAllowed = ["operator", "sample_upload"];
  assertEqual(canRenderWorkPanel("operator", "operator", opAllowed), true, "op cetak visible");
  assertEqual(canRenderWorkPanel("sample_upload", "operator", opAllowed), false, "op sample hidden");
  assertEqual(canRenderWorkPanel("sample_upload", "sample_upload", opAllowed), true, "op sample visible");
  assertEqual(canRenderWorkPanel("operator", "sample_upload", opAllowed), false, "op cetak hidden");
}

{
  const helperOnly = ["helper", "packing"];
  assertEqual(canRenderWorkPanel("packing", "packing", ["helper"]), false, "unpermitted packing hidden");
  assertEqual(scopedWorkPanelIds("packing", ["helper"]).join(","), "helper", "unpermitted falls back");
  assertEqual(canRenderWorkPanel("packing", "packing", helperOnly) && !canRenderWorkPanel("helper", "packing", helperOnly), true, "general open still scoped");
  assertEqual(scopedWorkPanelIds(undefined, helperOnly).join(","), "helper", "general open first workspace only");
  assertEqual(scopedWorkPanelIds("all", helperOnly).join(","), "helper", "no Semua merge");
  assertEqual(scopedWorkPanelIds(undefined, helperOnly).length, 1, "never all panels");
}

{
  const poSrc = fs.readFileSync(path.join(__dirname, "../../app/(app)/po/[id].tsx"), "utf8");
  if (!poSrc.includes("scopedWorkPanelIds")) {
    throw new Error("po detail must scope work panels");
  }
  if (poSrc.includes("[focus, ...ids.filter")) {
    throw new Error("po detail must not prepend-and-keep all panels");
  }
}

console.log("taskWorkspaces tests ok");
