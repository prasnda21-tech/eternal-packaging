import { act } from "react";
import { createRoot } from "react-dom/client";
import GlobalMenuSearch from "./GlobalMenuSearch";

global.IS_REACT_ACT_ENVIRONMENT = true;

const SAMPLE = [
  {
    key: "status-po",
    to: "/laporan/status-po",
    label: "Status PO",
    group: "sales",
    section: "",
    groupLabel: "Penjualan",
  },
  {
    key: "sales-order",
    to: "/penjualan/pesanan-penjualan",
    label: "Pesanan Penjualan",
    group: "sales",
    section: "",
    groupLabel: "Penjualan",
  },
  {
    key: "receipt",
    to: "/kas-bank/penerimaan",
    label: "Penerimaan",
    group: "keuangan",
    section: "Kas & Bank",
    groupLabel: "Keuangan",
  },
];

describe("GlobalMenuSearch interaction", () => {
  let container;
  let root;
  let trigger;

  beforeEach(() => {
    container = document.createElement("div");
    document.body.appendChild(container);
    root = createRoot(container);
    trigger = document.createElement("button");
    trigger.setAttribute("data-testid", "global-menu-search-trigger");
    document.body.appendChild(trigger);
  });

  afterEach(() => {
    act(() => root.unmount());
    container.remove();
    trigger.remove();
  });

  const renderOpen = async (props = {}) => {
    const onClose = props.onClose || jest.fn();
    const onSelect = props.onSelect || jest.fn();
    await act(async () => {
      root.render(
        <GlobalMenuSearch
          open
          items={SAMPLE}
          onClose={onClose}
          onSelect={onSelect}
          triggerRef={{ current: trigger }}
          {...props}
        />,
      );
    });
    await act(async () => {
      await Promise.resolve();
    });
    return { onClose, onSelect };
  };

  test("opens with empty query and autofocused input", async () => {
    await renderOpen();
    const input = container.querySelector('[data-testid="global-menu-search-input"]');
    expect(input).toBeTruthy();
    expect(input.value).toBe("");
    await act(async () => {
      await new Promise((r) => setTimeout(r, 0));
    });
    expect(document.activeElement).toBe(input);
    expect(container.querySelector('[data-testid="global-menu-search-results"]')).toBeTruthy();
  });

  test("filters Status PO and shows group secondary text", async () => {
    await renderOpen();
    const input = container.querySelector('[data-testid="global-menu-search-input"]');
    await act(async () => {
      const setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, "value").set;
      setter.call(input, "Status PO");
      input.dispatchEvent(new Event("input", { bubbles: true }));
    });
    const options = [...container.querySelectorAll(".ep-menu-search-option")];
    expect(options.length).toBe(1);
    expect(options[0].textContent).toMatch(/Status PO/);
    expect(options[0].textContent).toMatch(/Penjualan/);
  });

  test("empty match shows empty state copy", async () => {
    await renderOpen();
    const input = container.querySelector('[data-testid="global-menu-search-input"]');
    await act(async () => {
      const setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, "value").set;
      setter.call(input, "zzzz-nope");
      input.dispatchEvent(new Event("input", { bubbles: true }));
    });
    expect(container.querySelector('[data-testid="global-menu-search-empty"]').textContent)
      .toBe("Tidak ada menu yang cocok");
  });

  test("continuous typing ABCDE then 123 keeps focus and value", async () => {
    await renderOpen();
    const input = container.querySelector('[data-testid="global-menu-search-input"]');
    await act(async () => {
      input.focus();
      await new Promise((r) => setTimeout(r, 0));
    });
    expect(document.activeElement).toBe(input);
    const setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, "value").set;
    for (const ch of "ABCDE") {
      await act(async () => {
        setter.call(input, `${input.value}${ch}`);
        input.dispatchEvent(new Event("input", { bubbles: true }));
      });
      expect(document.activeElement).toBe(input);
    }
    for (const ch of "123") {
      await act(async () => {
        setter.call(input, `${input.value}${ch}`);
        input.dispatchEvent(new Event("input", { bubbles: true }));
      });
      expect(document.activeElement).toBe(input);
    }
    expect(input.value).toBe("ABCDE123");
    expect(container.querySelectorAll('[data-testid="global-menu-search-input"]')).toHaveLength(1);
  });

  test("ArrowDown/Enter selects highlighted result", async () => {
    const { onSelect, onClose } = await renderOpen();
    await act(async () => {
      window.dispatchEvent(new KeyboardEvent("keydown", { key: "ArrowDown", bubbles: true }));
    });
    await act(async () => {
      window.dispatchEvent(new KeyboardEvent("keydown", { key: "Enter", bubbles: true }));
    });
    expect(onSelect).toHaveBeenCalledWith(expect.objectContaining({ to: SAMPLE[1].to }));
    expect(onClose).toHaveBeenCalled();
  });

  test("Escape closes and restores focus to trigger", async () => {
    const onSelect = jest.fn();
    let onClose;
    onClose = jest.fn(async () => {
      await act(async () => {
        root.render(
          <GlobalMenuSearch
            open={false}
            items={SAMPLE}
            onClose={onClose}
            onSelect={onSelect}
            triggerRef={{ current: trigger }}
          />,
        );
      });
    });
    await act(async () => {
      root.render(
        <GlobalMenuSearch
          open
          items={SAMPLE}
          onClose={onClose}
          onSelect={onSelect}
          triggerRef={{ current: trigger }}
        />,
      );
    });
    await act(async () => {
      window.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape", bubbles: true }));
    });
    expect(onClose).toHaveBeenCalled();
    await act(async () => {
      await new Promise((r) => requestAnimationFrame(() => r()));
      await new Promise((r) => setTimeout(r, 0));
    });
    expect(document.activeElement).toBe(trigger);
  });

  test("reopen resets query", async () => {
    const onClose = jest.fn();
    const onSelect = jest.fn();
    await act(async () => {
      root.render(
        <GlobalMenuSearch
          open
          items={SAMPLE}
          onClose={onClose}
          onSelect={onSelect}
          triggerRef={{ current: trigger }}
        />,
      );
    });
    const input = container.querySelector('[data-testid="global-menu-search-input"]');
    await act(async () => {
      const setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, "value").set;
      setter.call(input, "Status");
      input.dispatchEvent(new Event("input", { bubbles: true }));
    });
    expect(input.value).toBe("Status");
    await act(async () => {
      root.render(
        <GlobalMenuSearch
          open={false}
          items={SAMPLE}
          onClose={onClose}
          onSelect={onSelect}
          triggerRef={{ current: trigger }}
        />,
      );
    });
    await act(async () => {
      root.render(
        <GlobalMenuSearch
          open
          items={SAMPLE}
          onClose={onClose}
          onSelect={onSelect}
          triggerRef={{ current: trigger }}
        />,
      );
    });
    const reopened = container.querySelector('[data-testid="global-menu-search-input"]');
    expect(reopened.value).toBe("");
  });
});
