import {
  canApproveDesign,
  canUploadSample,
  defaultWorkDivision,
  makeHasPerm,
  permittedWorkDivisions,
} from "./permissions";

function assertEqual(actual: unknown, expected: unknown, label: string) {
  if (actual !== expected) {
    throw new Error(`${label}: expected ${String(expected)}, got ${String(actual)}`);
  }
}

{
  const has = makeHasPerm(["operator.sample_upload", "operator.update", "helper.prepare"]);
  assertEqual(canUploadSample(has), true, "has sample perm");
  assertEqual(canApproveDesign(has), false, "no design role check");
  const ids = permittedWorkDivisions(has).map((d) => d.id);
  assertEqual(ids.includes("operator"), true, "operator division stays");
  assertEqual(ids.includes("helper"), true, "helper division stays");
  assertEqual(ids.includes("sample_upload"), false, "sample is not exclusive division");
}

{
  const has = makeHasPerm(["packing.update"]);
  assertEqual(canUploadSample(has), false, "no sample perm");
  assertEqual(defaultWorkDivision(has), "packing", "packing default");
}

{
  const has = makeHasPerm(["operator.sample_upload"]);
  assertEqual(defaultWorkDivision(has), "sample_upload", "sample-only default");
  assertEqual(defaultWorkDivision(has, "sample_upload"), "sample_upload", "sample preferred");
}

console.log("permissions tests ok");
