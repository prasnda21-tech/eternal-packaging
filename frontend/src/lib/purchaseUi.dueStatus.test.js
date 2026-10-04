import { invoiceDueDisplayStatus } from "./purchaseUi";

describe("invoiceDueDisplayStatus", () => {
  const today = "2026-09-28";

  test("formula G: paid invoice is LUNAS regardless of due date", () => {
    expect(invoiceDueDisplayStatus({
      payment_status: "LUNAS",
      due_date: "2026-09-01",
    }, today)).toBe("LUNAS");
  });

  test("formula H: future due date is BELUM JATUH TEMPO", () => {
    expect(invoiceDueDisplayStatus({
      payment_status: "BELUM DIBAYAR",
      due_date: "2026-09-29",
    }, today)).toBe("BELUM JATUH TEMPO");
  });

  test("formula I: due date today is JATUH TEMPO HARI INI", () => {
    expect(invoiceDueDisplayStatus({
      payment_status: "BELUM DIBAYAR",
      due_date: today,
    }, today)).toBe("JATUH TEMPO HARI INI");
  });

  test("formula J: past due date is TERLAMBAT", () => {
    expect(invoiceDueDisplayStatus({
      payment_status: "DP",
      due_date: "2026-09-27",
    }, today)).toBe("TERLAMBAT");
  });

  test("missing due date is explicit", () => {
    expect(invoiceDueDisplayStatus({
      payment_status: "BELUM DIBAYAR",
      due_date: null,
    }, today)).toBe("BELUM ADA JATUH TEMPO");
  });
});
