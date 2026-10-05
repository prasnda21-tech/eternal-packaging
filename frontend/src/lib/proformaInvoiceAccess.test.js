import { openProformaInvoicePdf, proformaRowVisible } from "./proformaInvoiceAccess";

describe("proforma row visibility", () => {
  const eligible = {
    onOrderMasuk: true,
    cancelled: false,
    hasView: true,
    available: true,
  };

  test("no invoice hides the row", () => {
    expect(proformaRowVisible({ ...eligible, available: false })).toBe(false);
  });

  test("draft invoice plus permission shows the row", () => {
    expect(proformaRowVisible(eligible)).toBe(true);
  });

  test("finalized invoice stays visible", () => {
    expect(proformaRowVisible(eligible)).toBe(true);
  });

  test("posted accounting stays visible", () => {
    expect(proformaRowVisible(eligible)).toBe(true);
  });

  test("paid status stays visible", () => {
    expect(proformaRowVisible(eligible)).toBe(true);
  });

  test("cancelled invoice only hides the row", () => {
    expect(proformaRowVisible({ ...eligible, available: false })).toBe(false);
  });

  test("multiple active invoices hide the row", () => {
    expect(proformaRowVisible({ ...eligible, available: false })).toBe(false);
  });

  test("permission absent hides the row", () => {
    expect(proformaRowVisible({ ...eligible, hasView: false })).toBe(false);
  });

  test("cancelled PO hides the row", () => {
    expect(proformaRowVisible({ ...eligible, cancelled: true })).toBe(false);
  });

  test("row is Order Masuk only", () => {
    expect(proformaRowVisible({ ...eligible, onOrderMasuk: false })).toBe(false);
  });
});

describe("openProformaInvoicePdf", () => {
  test("downloads the blob and opens it in a new tab", async () => {
    const blob = new Blob(["%PDF-proforma"], { type: "application/pdf" });
    const download = jest.fn().mockResolvedValue(blob);
    const openWindow = jest.fn();
    const created = [];
    const originalCreate = URL.createObjectURL;
    const originalRevoke = URL.revokeObjectURL;
    URL.createObjectURL = (value) => {
      created.push(value);
      return "blob:proforma";
    };
    URL.revokeObjectURL = jest.fn();
    try {
      await openProformaInvoicePdf("inv-draft-1", { download, openWindow });
      expect(download).toHaveBeenCalledWith("inv-draft-1");
      expect(created[0]).toBe(blob);
      expect(openWindow).toHaveBeenCalledWith("blob:proforma", "_blank");
    } finally {
      URL.createObjectURL = originalCreate;
      URL.revokeObjectURL = originalRevoke;
    }
  });
});
