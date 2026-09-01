import { currentStepLabel, displayValue, eligibleTypeOptions, emptyEligibility, emptyField, emptyStep, statusLabel } from "./employeeAdmin";

describe("employeeAdmin helpers", () => {
  test("status labels are Indonesian", () => {
    expect(statusLabel("DRAFT")).toBe("Draft");
    expect(statusLabel("DIAJUKAN")).toBe("Diajukan");
    expect(statusLabel("MENUNGGU_PERSETUJUAN")).toBe("Menunggu Persetujuan");
    expect(statusLabel("DISETUJUI")).toBe("Disetujui");
    expect(statusLabel("DITOLAK")).toBe("Ditolak");
  });

  test("ineligible and inactive types hidden from create options", () => {
    const rows = eligibleTypeOptions([
      { id: "1", name: "Kasbon", active: true },
      { id: "2", name: "Off", active: false },
    ]);
    expect(rows.map((r) => r.id)).toEqual(["1"]);
  });

  test("dynamic field display", () => {
    expect(displayValue({ type: "checkbox" }, true)).toBe("Ya");
    expect(displayValue({ type: "file" }, { name: "nota.jpg" })).toBe("nota.jpg");
    expect(displayValue({ type: "currency" }, 15000)).toMatch(/15/);
  });

  test("form builder defaults have no executable formula", () => {
    const field = emptyField(1);
    expect(field).not.toHaveProperty("formula");
    expect(emptyStep(2).label).toBe("Level 2");
    expect(emptyEligibility().mode).toBe("any");
  });

  test("current step label", () => {
    expect(currentStepLabel({ status: "DIAJUKAN", current_step_label: "Level 1" })).toBe("Level 1");
    expect(currentStepLabel({ status: "DISETUJUI", current_step_label: "Level 2" })).toBe("-");
  });
});
