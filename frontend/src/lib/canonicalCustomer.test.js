import { CANONICAL_CUSTOMER_PLACEHOLDER, getCanonicalCustomerName } from "./canonicalCustomer";

describe("getCanonicalCustomerName", () => {
  test("prefers customer over customer_name and never uses outlet", () => {
    expect(getCanonicalCustomerName({
      customer: "PT ABC",
      customer_name: "Other",
      outlet: "Yogyakarta",
    })).toBe("PT ABC");
  });

  test("falls back to customer_name when customer is missing", () => {
    expect(getCanonicalCustomerName({
      customer_name: "PT ABC",
      outlet: "Yogyakarta",
    })).toBe("PT ABC");
  });

  test("does not substitute outlet or region when canonical name is missing", () => {
    expect(getCanonicalCustomerName({
      outlet: "Yogyakarta",
      region: "DIY",
      location_name: "Yogyakarta",
      outlet_name: "Yogyakarta",
    })).toBe(CANONICAL_CUSTOMER_PLACEHOLDER);
  });

  test("blank strings are treated as missing", () => {
    expect(getCanonicalCustomerName({
      customer: "   ",
      customer_name: "",
      outlet: "Yogyakarta",
    })).toBe("—");
  });
});
