import {
  ROW_INTERACTIVE_SELECTOR,
  clickableDocumentRowProps,
  isRowInteractiveTarget,
} from "./clickableListRow";

describe("clickableListRow", () => {
  test("exposes interactive selector covering nested controls", () => {
    expect(ROW_INTERACTIVE_SELECTOR).toContain("button");
    expect(ROW_INTERACTIVE_SELECTOR).toContain("input");
    expect(ROW_INTERACTIVE_SELECTOR).toContain("select");
    expect(ROW_INTERACTIVE_SELECTOR).toContain("a");
    expect(ROW_INTERACTIVE_SELECTOR).toContain("[data-no-row-nav]");
  });

  test("detects interactive targets inside the row", () => {
    const row = document.createElement("tr");
    const td = document.createElement("td");
    const button = document.createElement("button");
    button.type = "button";
    td.appendChild(button);
    row.appendChild(td);
    document.body.appendChild(row);
    expect(isRowInteractiveTarget(button, row)).toBe(true);
    expect(isRowInteractiveTarget(td, row)).toBe(false);
    row.remove();
  });

  test("clickableDocumentRowProps wires open once and ignores interactive clicks", () => {
    const opens = [];
    const props = clickableDocumentRowProps({
      onOpen: () => opens.push("open"),
      "data-testid": "doc-row-1",
      ariaLabel: "Buka dokumen",
    });
    expect(props.role).toBe("link");
    expect(props.tabIndex).toBe(0);
    expect(props.className).toContain("sd-clickable-row");
    expect(props["data-testid"]).toBe("doc-row-1");

    const row = document.createElement("tr");
    const cell = document.createElement("td");
    const btn = document.createElement("button");
    cell.appendChild(btn);
    row.appendChild(cell);
    document.body.appendChild(row);

    props.onClick({ target: cell, currentTarget: row });
    expect(opens).toEqual(["open"]);
    props.onClick({ target: btn, currentTarget: row });
    expect(opens).toEqual(["open"]);

    props.onKeyDown({ key: "Enter", target: row, currentTarget: row, preventDefault() {} });
    expect(opens).toEqual(["open", "open"]);
    props.onKeyDown({ key: "Enter", target: btn, currentTarget: row, preventDefault() {} });
    expect(opens).toEqual(["open", "open"]);
    row.remove();
  });

  test("disabled rows do not navigate", () => {
    const props = clickableDocumentRowProps({
      onOpen: () => {},
      disabled: true,
      "data-testid": "doc-row-disabled",
    });
    expect(props.onClick).toBeUndefined();
    expect(props.tabIndex).toBeUndefined();
    expect(props["data-testid"]).toBe("doc-row-disabled");
  });
});
