import { act } from "react";
import { createRoot } from "react-dom/client";
import BranchScopeSelect from "./BranchScopeSelect";

global.IS_REACT_ACT_ENVIRONMENT = true;

describe("BranchScopeSelect", () => {
  const branches = [
    { id: "br-ygy", code: "YGY", name: "Yogyakarta", active: true },
    { id: "br-smg", code: "SMG", name: "Semarang", active: true },
  ];

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

  test("shows fixed label for normal users without switch", () => {
    act(() => {
      root.render(
        <BranchScopeSelect
          branches={branches}
          value="br-ygy"
          canSwitch={false}
          effectiveCode="YGY"
          testId="stock-branch-select"
        />,
      );
    });
    expect(container.querySelector('[data-testid="stock-branch-select-fixed"]')?.textContent).toMatch(/Cabang: YGY/);
    expect(container.querySelector('[data-testid="stock-branch-select-input"]')).toBeNull();
  });

  test("shows selector for HOLDING / unrestricted and emits change", () => {
    const onChange = jest.fn();
    act(() => {
      root.render(
        <BranchScopeSelect
          branches={branches}
          value="br-ygy"
          canSwitch
          onChange={onChange}
          testId="stock-branch-select"
        />,
      );
    });
    const select = container.querySelector('[data-testid="stock-branch-select-input"]');
    expect(select).toBeTruthy();
    act(() => {
      select.value = "br-smg";
      select.dispatchEvent(new Event("change", { bubbles: true }));
    });
    expect(onChange).toHaveBeenCalledWith("br-smg");
  });
});
