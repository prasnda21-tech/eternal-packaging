import { act } from "react";
import { createRoot } from "react-dom/client";
import HeaderSelectFilter from "./HeaderSelectFilter";

jest.mock("./ui/popover", () => ({
  Popover: ({ children, open, onOpenChange }) => (
    <div data-testid="popover-root" data-open={open ? "1" : "0"}>
      {typeof children === "function" ? null : children}
      <button type="button" data-testid="force-close" onClick={() => onOpenChange?.(false)}>x</button>
    </div>
  ),
  PopoverTrigger: ({ children }) => children,
  PopoverContent: ({ children }) => <div data-testid="popover-content">{children}</div>,
}));

jest.mock("lucide-react", () => ({
  Filter: (props) => <span data-icon="Filter" {...props} />,
}));

global.IS_REACT_ACT_ENVIRONMENT = true;

describe("HeaderSelectFilter", () => {
  let container;
  let root;

  beforeEach(() => {
    container = document.createElement("div");
    document.body.appendChild(container);
    root = createRoot(container);
  });

  afterEach(() => {
    act(() => root.unmount());
    container.remove();
  });

  test("single-select applies immediately and shows active clear", async () => {
    const onChange = jest.fn();
    await act(async () => {
      root.render(
        <HeaderSelectFilter
          label="Tanggal"
          testid="demo-filter"
          options={[
            { value: "all", label: "Semua" },
            { value: "today", label: "Hari ini" },
          ]}
          value="all"
          emptyValue="all"
          onChange={onChange}
        />
      );
    });
    const trigger = container.querySelector('[data-testid="demo-filter"]');
    expect(trigger.getAttribute("aria-label")).toBe("Filter Tanggal");
    expect(trigger.getAttribute("aria-haspopup")).toBe("listbox");
    expect(container.querySelector('[data-testid="demo-filter-active-dot"]')).toBeNull();

    await act(async () => {
      container.querySelector('[data-testid="demo-filter-option-today"]').click();
    });
    expect(onChange).toHaveBeenCalledWith("today");
  });

  test("active value shows clear action", async () => {
    const onChange = jest.fn();
    await act(async () => {
      root.render(
        <HeaderSelectFilter
          label="Sales"
          testid="sales-filter"
          options={[
            { value: "", label: "Semua" },
            { value: "Andi", label: "Andi" },
          ]}
          value="Andi"
          emptyValue=""
          onChange={onChange}
        />
      );
    });
    expect(container.querySelector('[data-testid="sales-filter-active-dot"]')).toBeTruthy();
    await act(async () => {
      container.querySelector('[data-testid="sales-filter-clear"]').click();
    });
    expect(onChange).toHaveBeenCalledWith("");
  });
});
