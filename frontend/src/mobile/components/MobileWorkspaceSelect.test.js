import { act } from "react";
import { createRoot } from "react-dom/client";
import MobileWorkspaceSelect from "./MobileWorkspaceSelect";

global.IS_REACT_ACT_ENVIRONMENT = true;

describe("MobileWorkspaceSelect", () => {
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

  test("renders compact Pilih Tugas dropdown with authorized workspaces only", async () => {
    const onChange = jest.fn();
    const workspaces = [
      { id: "prep", label: "Persiapan Cup" },
      { id: "packing", label: "Packing" },
      { id: "loading", label: "Loading" },
    ];
    await act(async () => {
      root.render(
        <MobileWorkspaceSelect workspaces={workspaces} value="prep" onChange={onChange} />,
      );
    });
    expect(container.querySelector('[data-testid="mobile-workspace-select"]')).toBeTruthy();
    expect(container.textContent).toContain("Pilih Tugas");
    const select = container.querySelector('[data-testid="mobile-workspace-select-input"]');
    expect(select).toBeTruthy();
    expect([...select.options].map((o) => o.value)).toEqual(["prep", "packing", "loading"]);
    expect(container.querySelector(".mop-ws-tabs")).toBeFalsy();
    await act(async () => {
      select.value = "packing";
      select.dispatchEvent(new Event("change", { bubbles: true }));
    });
    expect(onChange).toHaveBeenCalledWith("packing");
  });

  test("single workspace shows current label without select", async () => {
    await act(async () => {
      root.render(
        <MobileWorkspaceSelect workspaces={[{ id: "screener", label: "Screener" }]} value="screener" />,
      );
    });
    expect(container.querySelector('[data-testid="mobile-workspace-select-current"]').textContent).toBe("Screener");
    expect(container.querySelector("select")).toBeFalsy();
  });

  test("Role Aktif label lists only provided workspaces", async () => {
    await act(async () => {
      root.render(
        <MobileWorkspaceSelect
          label="Role Aktif"
          testid="mop-active-role"
          workspaces={[
            { id: "prep", label: "Persiapan Cup" },
            { id: "packing", label: "Packing" },
          ]}
          value="packing"
        />,
      );
    });
    expect(container.querySelector('[data-testid="mop-active-role"]')).toBeTruthy();
    expect(container.textContent).toContain("Role Aktif");
    const select = container.querySelector('[data-testid="mop-active-role-input"]');
    expect([...select.options].map((o) => o.value)).toEqual(["prep", "packing"]);
    expect(select.value).toBe("packing");
  });
});
