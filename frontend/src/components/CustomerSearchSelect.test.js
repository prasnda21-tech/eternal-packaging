/**
 * @jest-environment jsdom
 */
import React from "react";
import fs from "fs";
import path from "path";
import { act } from "react-dom/test-utils";
import { createRoot } from "react-dom/client";
import CustomerSearchSelect, {
  CUSTOMER_OPTIONS_LOAD_ERROR,
  CUSTOMER_SEARCH_DEBOUNCE_MS,
  filterActiveMasterCustomers,
  isActiveMasterCustomer,
} from "./CustomerSearchSelect";

global.IS_REACT_ACT_ENVIRONMENT = true;

const src = fs.readFileSync(path.join(__dirname, "CustomerSearchSelect.jsx"), "utf8");

const ACTIVE = [
  { id: "c1", name: "Alpha Packaging", phone: "0811", city: "Jakarta", pic: "Andi", is_active: true },
  { id: "c2", name: "Beta Box", phone: "0812", city: "Bandung", pic: "Budi", is_active: true },
  { id: "c3", name: "Gamma Goods", phone: "0813", city: "Surabaya", pic: "Cici", is_active: true },
];
const INACTIVE = { id: "c9", name: "Zeta Dead", phone: "0899", city: "Medan", pic: "Zed", is_active: false };

function setInputValue(input, value) {
  const setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, "value").set;
  setter.call(input, value);
  input.dispatchEvent(new Event("input", { bubbles: true }));
}

describe("CustomerSearchSelect helpers", () => {
  test("isActiveMasterCustomer requires id+name and excludes inactive", () => {
    expect(isActiveMasterCustomer(ACTIVE[0])).toBe(true);
    expect(isActiveMasterCustomer({ id: "x", name: "Legacy" })).toBe(true);
    expect(isActiveMasterCustomer(INACTIVE)).toBe(false);
    expect(isActiveMasterCustomer({ id: "", name: "NoId" })).toBe(false);
    expect(isActiveMasterCustomer({ id: "x", name: "  " })).toBe(false);
  });

  test("empty query returns all active masters; typed query filters haystack", () => {
    const all = [...ACTIVE, INACTIVE];
    expect(filterActiveMasterCustomers(all, "")).toHaveLength(3);
    expect(filterActiveMasterCustomers(all, "bandung").map((c) => c.id)).toEqual(["c2"]);
    expect(filterActiveMasterCustomers(all, "0813").map((c) => c.id)).toEqual(["c3"]);
    expect(filterActiveMasterCustomers(all, "andi").map((c) => c.id)).toEqual(["c1"]);
    expect(filterActiveMasterCustomers(all, "zeta")).toHaveLength(0);
  });
});

describe("CustomerSearchSelect open-on-focus contract", () => {
  test("focus applies initial filter sync and portals the list", () => {
    expect(src).toMatch(/openList/);
    expect(src).toMatch(/applyFilterNow/);
    expect(src).toMatch(/placeMasterItemListBox/);
    expect(src).toMatch(/createPortal/);
    expect(src).toMatch(/data-overlay="portal"/);
    expect(src).not.toMatch(/position:\s*"absolute"/);
    expect(src).not.toMatch(/Ketik untuk mencari customer aktif/);
    expect(src).toMatch(/CUSTOMER_SEARCH_DEBOUNCE_MS/);
  });

  test("empty query path skips debounce; typing still debounces", () => {
    expect(src).toMatch(/if \(!q\) \{\s*applyFilterNow\(query\)/s);
    expect(CUSTOMER_SEARCH_DEBOUNCE_MS).toBe(150);
  });
});

describe("CustomerSearchSelect focus interaction", () => {
  let container;
  let root;

  beforeEach(() => {
    container = document.createElement("div");
    document.body.appendChild(container);
    root = createRoot(container);
  });

  afterEach(async () => {
    await act(async () => {
      root.unmount();
    });
    container.remove();
  });

  test("focus with 3 active masters shows 3 options immediately without false empty", async () => {
    await act(async () => {
      root.render(
        <CustomerSearchSelect
          customers={[...ACTIVE, INACTIVE]}
          value=""
          testId="input-customer"
        />
      );
    });

    const input = document.querySelector('[data-testid="input-customer"]');
    await act(async () => {
      input.focus();
    });

    const list = document.querySelector('[data-testid="input-customer-list"]');
    expect(list).toBeTruthy();
    expect(list.getAttribute("data-overlay")).toBe("portal");
    expect(document.querySelector('[data-testid="input-customer-empty"]')).toBeNull();
    expect(document.querySelector('[data-testid="input-customer-loading"]')).toBeNull();
    expect(document.querySelector('[data-testid="input-customer-option-c1"]')).toBeTruthy();
    expect(document.querySelector('[data-testid="input-customer-option-c2"]')).toBeTruthy();
    expect(document.querySelector('[data-testid="input-customer-option-c3"]')).toBeTruthy();
    expect(document.querySelector('[data-testid="input-customer-option-c9"]')).toBeNull();
  });

  test("selecting a master binds canonical id via onSelectCustomer", async () => {
    const onSelect = jest.fn();
    await act(async () => {
      root.render(
        <CustomerSearchSelect
          customers={ACTIVE}
          value=""
          onSelectCustomer={onSelect}
          testId="input-customer"
        />
      );
    });
    const input = document.querySelector('[data-testid="input-customer"]');
    await act(async () => {
      input.focus();
    });
    await act(async () => {
      document.querySelector('[data-testid="input-customer-option-c2"]').dispatchEvent(
        new MouseEvent("click", { bubbles: true })
      );
    });
    expect(onSelect).toHaveBeenCalledTimes(1);
    expect(onSelect.mock.calls[0][0].id).toBe("c2");
    expect(onSelect.mock.calls[0][0].name).toBe("Beta Box");
  });

  test("typing filters results after debounce; free text still fires onTextChange", async () => {
    jest.useFakeTimers();
    const onText = jest.fn();
    await act(async () => {
      root.render(
        <CustomerSearchSelect
          customers={ACTIVE}
          value=""
          onTextChange={onText}
          testId="input-customer"
        />
      );
    });
    const input = document.querySelector('[data-testid="input-customer"]');
    await act(async () => {
      input.focus();
    });
    expect(document.querySelectorAll('[data-testid^="input-customer-option-"]').length).toBe(3);

    await act(async () => {
      setInputValue(input, "Gamma");
    });
    expect(onText).toHaveBeenCalledWith("Gamma");

    await act(async () => {
      jest.advanceTimersByTime(CUSTOMER_SEARCH_DEBOUNCE_MS + 20);
    });
    expect(document.querySelector('[data-testid="input-customer-option-c3"]')).toBeTruthy();
    expect(document.querySelector('[data-testid="input-customer-option-c1"]')).toBeNull();
    expect(document.querySelector('[data-testid="input-customer-option-c2"]')).toBeNull();
    jest.useRealTimers();
  });

  test("API load failure is distinct from empty search and keeps free typing + retry", async () => {
    const onRetry = jest.fn();
    const onText = jest.fn();
    await act(async () => {
      root.render(
        <CustomerSearchSelect
          customers={[]}
          value=""
          loadError={CUSTOMER_OPTIONS_LOAD_ERROR}
          onRetryLoad={onRetry}
          onTextChange={onText}
          testId="input-customer"
        />
      );
    });
    expect(document.querySelector('[data-testid="input-customer-load-error-hint"]')).toBeTruthy();
    expect(document.querySelector('[data-testid="input-customer-load-error-hint"]').textContent).toMatch(
      /Gagal memuat daftar pelanggan/
    );

    const input = document.querySelector('[data-testid="input-customer"]');
    expect(input.disabled).toBe(false);
    await act(async () => {
      input.focus();
    });
    expect(document.querySelector('[data-testid="input-customer-load-error"]')).toBeTruthy();
    expect(document.querySelector('[data-testid="input-customer-empty"]')).toBeNull();

    await act(async () => {
      document.querySelector('[data-testid="input-customer-retry-inline"]').dispatchEvent(
        new MouseEvent("click", { bubbles: true })
      );
    });
    expect(onRetry).toHaveBeenCalledTimes(1);

    await act(async () => {
      setInputValue(input, "Pelanggan Baru PT");
    });
    expect(onText).toHaveBeenCalledWith("Pelanggan Baru PT");
    expect(input.disabled).toBe(false);
  });
});

describe("PO / Pra-PO customer picker wiring", () => {
  test("PesananPenjualan and PraPo wire load error + retry via existing loadOptions", () => {
    const po = fs.readFileSync(path.join(__dirname, "../pages/PesananPenjualan.jsx"), "utf8");
    const pra = fs.readFileSync(path.join(__dirname, "../pages/PraPo.jsx"), "utf8");
    expect(po).toMatch(/CustomerSearchSelect/);
    expect(po).toMatch(/handleCustomerSelect/);
    expect(po).toMatch(/master_customers_state/);
    expect(po).toMatch(/CUSTOMER_OPTIONS_LOAD_ERROR/);
    expect(po).toMatch(/onRetryLoad=\{\(\) => loadOptions/);
    expect(pra).toMatch(/CustomerSearchSelect/);
    expect(pra).toMatch(/testId="pra-po-customer"/);
    expect(pra).toMatch(/customerOptionsState/);
    expect(pra).toMatch(/CUSTOMER_OPTIONS_LOAD_ERROR/);
    expect(pra).toMatch(/onRetryLoad=\{loadOptions\}/);
  });
});
