import fs from "fs";
import path from "path";
import { useState } from "react";
import { act } from "react";
import { createRoot } from "react-dom/client";
import { salesOrderLineColor, salesOrderLineColorsByIndex } from "../lib/salesOrderLineColor";
import LineColorCell from "../components/poDetail/LineColorCell";
import api from "../lib/api";

jest.mock("../lib/api", () => ({
  __esModule: true,
  default: {
    updateSalesOrderLineColor: jest.fn(),
  },
}));

global.IS_REACT_ACT_ENVIRONMENT = true;

describe("PO Detail line color display", () => {
  const detail = fs.readFileSync(path.join(__dirname, "PODetail.jsx"), "utf8");
  const route = fs.readFileSync(path.join(__dirname, "../components/ProductionRoutePanel.jsx"), "utf8");
  const penjualan = fs.readFileSync(path.join(__dirname, "PesananPenjualan.jsx"), "utf8");

  test("line item Warna uses LineColorCell inside the per-line map", () => {
    const cell = fs.readFileSync(path.join(__dirname, "../components/poDetail/LineColorCell.jsx"), "utf8");
    expect(detail).toMatch(/\(order\.line_items \|\| \[\]\)\.map\(\(li, i\) => \{/);
    expect(detail).toMatch(/import LineColorCell from "\.\.\/components\/poDetail\/LineColorCell"/);
    expect(detail).toMatch(/data-testid=\{`line-item-color-\$\{i\}`\}/);
    expect(detail).toMatch(/<LineColorCell/);
    expect(cell).toMatch(/salesOrderLineColor\(line\) \|\| "-"/);
    expect(detail).not.toMatch(/Warna: \{order\.color\}/);
    expect(detail).not.toMatch(/line_items\[0\]\.color/);
    expect(detail).toMatch(/line-items-table/);
  });

  test("production route panel shows each line color independently", () => {
    expect(route).toMatch(/lis\.map\(\(li, idx\) => \{/);
    expect(route).toMatch(/\{li\.color && <div className="muted small">\{li\.color\}<\/div>\}/);
    expect(route).not.toMatch(/order\.color/);
  });

  test("order.notes is saved once and is not duplicated as Keterangan", () => {
    const info = detail.slice(
      detail.indexOf('data-testid="detail-info"'),
      detail.indexOf('data-testid="line-items-panel"'),
    );
    expect(info).not.toMatch(/data-testid="detail-keterangan"/);
    expect(info).not.toMatch(/\{order\.notes\}/);
    expect(info).not.toMatch(/Keterangan/);
    const panel = fs.readFileSync(path.join(__dirname, "../components/poDetail/ProductionNotesPanel.jsx"), "utf8");
    expect(detail).toMatch(/<ProductionNotesPanel/);
    expect(detail.match(/\{order\.notes\}/g)).toEqual(["{order.notes}"]);
    expect(panel).toMatch(/data-testid="notes-panel"/);
    expect(panel).toMatch(/data-testid="notes-saved"/);
    expect(panel).not.toMatch(/\{order\.notes\}/);
  });

  test("Pesanan Penjualan binds Warna input to exact line.color", () => {
    expect(penjualan).toMatch(/value=\{line\.color \|\| ""\}/);
    expect(penjualan).toMatch(/handleLineChange\(originalIndex, "color"/);
    expect(penjualan).toMatch(/data-testid=\{`line-color-\$\{originalIndex\}`\}/);
  });

  test("CASE 1–3: same-name RED/BLUE stay independent on Detail mapping", () => {
    const order = {
      line_items: [
        { name: "Preview Cup", color: "RED" },
        { name: "Preview Cup", color: "BLUE" },
      ],
    };
    const rendered = (order.line_items || []).map((li) => `Warna: ${salesOrderLineColor(li) || "-"}`);
    expect(rendered[0]).toBe("Warna: RED");
    expect(rendered[1]).toBe("Warna: BLUE");
  });

  test("CASE 4–5: multi-color and missing color do not cross-assign", () => {
    const mapped = salesOrderLineColorsByIndex([
      { name: "Preview Cup", color: "RED / WHITE / BLACK" },
      { name: "Preview Cup", color: null },
    ]);
    expect(mapped[0].color).toBe("RED / WHITE / BLACK");
    expect(mapped[1].color).toBe("");
  });

  test("null persisted color renders Warna: -", () => {
    const rendered = ([{ name: "Cup", color: null }]).map(
      (li) => `Warna: ${salesOrderLineColor(li) || "-"}`
    );
    expect(rendered[0]).toBe("Warna: -");
  });

  test("structured Accurate Warna strings render as-is", () => {
    expect(salesOrderLineColor({ color: "PUTIH, HIJAU, PINK" })).toBe("PUTIH, HIJAU, PINK");
    expect(salesOrderLineColor({ color: "BENING" })).toBe("BENING");
  });

  test("CASE 6: re-fetch payload replaces displayed color", () => {
    let order = { line_items: [{ name: "Preview Cup", color: "RED" }] };
    expect(salesOrderLineColor(order.line_items[0])).toBe("RED");
    order = { line_items: [{ name: "Preview Cup", color: "BLUE" }] };
    expect(salesOrderLineColor(order.line_items[0])).toBe("BLUE");
  });
});

const LINES = [
  { name: "Cup 12 oz", item_code: "CUP-12", quantity: 1000, color: "HITAM", line_id: "A", production_process: "print" },
  { name: "Cup 12 oz", item_code: "CUP-12", quantity: 500, color: "PUTIH", line_id: "B", production_process: "print" },
];

function setInputValue(el, value) {
  const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value").set;
  setter.call(el, value);
  el.dispatchEvent(new Event("input", { bubbles: true }));
  el.dispatchEvent(new Event("change", { bubbles: true }));
}

function click(el) {
  act(() => {
    el.dispatchEvent(new MouseEvent("click", { bubbles: true }));
  });
}

function ColorHost({ initial, canEdit = true }) {
  const [lines, setLines] = useState(initial);
  const [active, setActive] = useState(null);
  return (
    <div>
      <button
        type="button"
        data-testid="poll-same"
        onClick={() => setLines((rows) => rows.map((row) => ({ ...row, quantity: row.quantity })))}
      >
        poll
      </button>
      <button
        type="button"
        data-testid="poll-color"
        onClick={() => setLines((rows) => rows.map((row, idx) => (
          idx === 1 ? { ...row, color: "PUTIH-SERVER", quantity: 500 } : row
        )))}
      >
        poll color
      </button>
      <button
        type="button"
        data-testid="poll-mismatch"
        onClick={() => setLines((rows) => rows.map((row, idx) => (
          idx === 1 ? { ...row, line_id: "C", color: "KUNING" } : row
        )))}
      >
        mismatch
      </button>
      <table className="line-items-table">
        <tbody>
          {lines.map((li, i) => (
            <tr key={i}>
              <td className="li-color" data-testid={`line-item-color-${i}`}>
                <LineColorCell
                  orderId="ord-1"
                  line={li}
                  lineIdx={i}
                  canEdit={canEdit}
                  active={active === i}
                  onActivate={setActive}
                  onDeactivate={() => setActive((cur) => (cur === i ? null : cur))}
                  onSaved={(order) => setLines(order.line_items)}
                />
              </td>
              <td className="li-actions" data-testid={`line-actions-${i}`} />
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function mountHost(props) {
  const container = document.createElement("div");
  document.body.appendChild(container);
  const root = createRoot(container);
  act(() => {
    root.render(<ColorHost {...props} />);
  });
  return {
    container,
    unmount() {
      act(() => root.unmount());
      container.remove();
    },
  };
}

describe("PO Detail line color editor", () => {
  const detail = fs.readFileSync(path.join(__dirname, "PODetail.jsx"), "utf8");
  const cell = fs.readFileSync(path.join(__dirname, "../components/poDetail/LineColorCell.jsx"), "utf8");
  const apiSource = fs.readFileSync(path.join(__dirname, "../lib/api.js"), "utf8");
  const css = fs.readFileSync(path.join(__dirname, "../App.css"), "utf8");

  beforeEach(() => {
    api.updateSalesOrderLineColor.mockReset();
  });

  test("shows the current color and a dash when color is null", () => {
    const shown = mountHost({ initial: LINES });
    expect(shown.container.querySelector('[data-testid="line-color-value-0"]').textContent).toBe("HITAM");
    expect(shown.container.querySelector('[data-testid="line-color-value-1"]').textContent).toBe("PUTIH");
    shown.unmount();
    const empty = mountHost({ initial: [{ name: "Cup", color: null, line_id: "A" }] });
    expect(empty.container.querySelector('[data-testid="line-color-value-0"]').textContent).toBe("-");
    empty.unmount();
  });

  test("pencil is visible for po.edit and hidden for view-only", () => {
    expect(detail).toMatch(/const canEditLineColor = hasPerm\("po\.edit"\) && !order\?\.cancelled/);
    expect(detail).toMatch(/canEdit=\{canEditLineColor\}/);
    const allowed = mountHost({ initial: LINES, canEdit: true });
    expect(allowed.container.querySelector('[data-testid="line-color-edit-1"]')).toBeTruthy();
    allowed.unmount();
    const view = mountHost({ initial: LINES, canEdit: false });
    expect(view.container.querySelector('[data-testid="line-color-value-1"]').textContent).toBe("PUTIH");
    expect(view.container.querySelector('[data-testid="line-color-edit-0"]')).toBeNull();
    expect(view.container.querySelector('[data-testid="line-color-edit-1"]')).toBeNull();
    view.unmount();
  });

  test("pencil opens the clicked duplicate row with its own color", () => {
    const { container, unmount } = mountHost({ initial: LINES });
    click(container.querySelector('[data-testid="line-color-edit-1"]'));
    const input = container.querySelector('[data-testid="line-color-input-1"]');
    expect(input.value).toBe("PUTIH");
    expect(container.querySelector('[data-testid="line-color-input-0"]')).toBeNull();
    expect(container.querySelector('[data-testid="line-color-value-0"]').textContent).toBe("HITAM");
    unmount();
  });

  test("save sends only color and the stable line id for that index", async () => {
    api.updateSalesOrderLineColor.mockResolvedValue({
      order: {
        id: "ord-1",
        line_items: [
          LINES[0],
          { ...LINES[1], color: "MERAH" },
        ],
      },
    });
    const { container, unmount } = mountHost({ initial: LINES });
    click(container.querySelector('[data-testid="line-color-edit-1"]'));
    const input = container.querySelector('[data-testid="line-color-input-1"]');
    act(() => setInputValue(input, "MERAH"));
    await act(async () => {
      container.querySelector('[data-testid="line-color-save-1"]').dispatchEvent(
        new MouseEvent("click", { bubbles: true }),
      );
    });
    expect(api.updateSalesOrderLineColor).toHaveBeenCalledTimes(1);
    expect(api.updateSalesOrderLineColor).toHaveBeenCalledWith("ord-1", 1, {
      color: "MERAH",
      line_id: "B",
    });
    const payload = api.updateSalesOrderLineColor.mock.calls[0][2];
    expect(Object.keys(payload).sort()).toEqual(["color", "line_id"]);
    expect(payload).not.toHaveProperty("quantity");
    expect(payload).not.toHaveProperty("master_item_id");
    expect(container.querySelector('[data-testid="line-color-input-1"]')).toBeNull();
    expect(container.querySelector('[data-testid="line-color-value-1"]').textContent).toBe("MERAH");
    expect(container.querySelector('[data-testid="line-color-value-0"]').textContent).toBe("HITAM");
    unmount();
  });

  test("saving disables a second save until the request finishes", async () => {
    let release;
    api.updateSalesOrderLineColor.mockImplementation(() => new Promise((resolve) => {
      release = resolve;
    }));
    const { container, unmount } = mountHost({ initial: LINES });
    click(container.querySelector('[data-testid="line-color-edit-1"]'));
    const save = container.querySelector('[data-testid="line-color-save-1"]');
    await act(async () => {
      save.dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });
    expect(container.querySelector('[data-testid="line-color-save-1"]').disabled).toBe(true);
    expect(container.querySelector('[data-testid="line-color-input-1"]').disabled).toBe(true);
    expect(container.querySelector('[data-testid="line-color-save-1"]').textContent).toBe("Menyimpan");
    await act(async () => {
      container.querySelector('[data-testid="line-color-save-1"]').dispatchEvent(
        new MouseEvent("click", { bubbles: true }),
      );
    });
    expect(api.updateSalesOrderLineColor).toHaveBeenCalledTimes(1);
    await act(async () => {
      release({
        order: { id: "ord-1", line_items: [LINES[0], { ...LINES[1], color: "MERAH" }] },
      });
    });
    unmount();
  });

  test("cancel closes the editor without an API call", () => {
    const { container, unmount } = mountHost({ initial: LINES });
    click(container.querySelector('[data-testid="line-color-edit-1"]'));
    act(() => setInputValue(container.querySelector('[data-testid="line-color-input-1"]'), "MERAH"));
    click(container.querySelector('[data-testid="line-color-cancel-1"]'));
    expect(api.updateSalesOrderLineColor).not.toHaveBeenCalled();
    expect(container.querySelector('[data-testid="line-color-input-1"]')).toBeNull();
    expect(container.querySelector('[data-testid="line-color-value-1"]').textContent).toBe("PUTIH");
    unmount();
  });

  test("backend error and 409 keep the draft open", async () => {
    api.updateSalesOrderLineColor.mockRejectedValueOnce({
      response: { status: 500, data: { detail: "gagal simpan" } },
    });
    const { container, unmount } = mountHost({ initial: LINES });
    click(container.querySelector('[data-testid="line-color-edit-1"]'));
    act(() => setInputValue(container.querySelector('[data-testid="line-color-input-1"]'), "MERAH"));
    await act(async () => {
      container.querySelector('[data-testid="line-color-save-1"]').dispatchEvent(
        new MouseEvent("click", { bubbles: true }),
      );
    });
    expect(container.querySelector('[data-testid="line-color-input-1"]').value).toBe("MERAH");
    expect(container.querySelector('[data-testid="line-color-error-1"]').textContent).toBe("gagal simpan");
    expect(container.querySelector('[data-testid="line-color-value-1"]')).toBeNull();
    api.updateSalesOrderLineColor.mockRejectedValueOnce({
      response: {
        status: 409,
        data: { detail: "Identitas baris berubah. Muat ulang halaman lalu coba lagi." },
      },
    });
    await act(async () => {
      container.querySelector('[data-testid="line-color-save-1"]').dispatchEvent(
        new MouseEvent("click", { bubbles: true }),
      );
    });
    expect(container.querySelector('[data-testid="line-color-error-1"]').textContent).toMatch(/Muat ulang/);
    expect(container.querySelector('[data-testid="line-color-input-1"]').value).toBe("MERAH");
    unmount();
  });

  test("polling the same line keeps the draft, and a new stable id closes it", () => {
    const { container, unmount } = mountHost({ initial: LINES });
    click(container.querySelector('[data-testid="line-color-edit-1"]'));
    act(() => setInputValue(container.querySelector('[data-testid="line-color-input-1"]'), "MERAH"));
    click(container.querySelector('[data-testid="poll-same"]'));
    expect(container.querySelector('[data-testid="line-color-input-1"]').value).toBe("MERAH");
    click(container.querySelector('[data-testid="poll-color"]'));
    expect(container.querySelector('[data-testid="line-color-input-1"]').value).toBe("MERAH");
    click(container.querySelector('[data-testid="poll-mismatch"]'));
    expect(container.querySelector('[data-testid="line-color-input-1"]')).toBeNull();
    expect(container.querySelector('[data-testid="line-color-value-1"]').textContent).toBe("KUNING");
    unmount();
  });

  test("Ubah Item and Ubah Qty stay on their own control, and the color cell stays compact", () => {
    expect(detail).toMatch(/<LineProductReplaceControl/);
    expect(detail).toMatch(/canEdit=\{canReplaceLineProduct\}/);
    const actions = detail.slice(detail.indexOf('className="li-actions"'), detail.indexOf("</td>", detail.indexOf('className="li-actions"')));
    expect(actions).not.toMatch(/LineColorCell/);
    expect(actions).not.toMatch(/line-color-edit/);
    expect(apiSource).toMatch(/replaceSalesOrderLineProduct:/);
    expect(apiSource).toMatch(/\/lines\/\$\{lineIdx\}\/product/);
    expect(apiSource).toMatch(/updateSalesOrderLineColor:/);
    expect(apiSource).toMatch(/\/lines\/\$\{lineIdx\}\/color/);
    expect(cell).not.toMatch(/replaceSalesOrderLineProduct/);
    expect(cell).not.toMatch(/quantity:/);
    expect(cell).toMatch(/className="li-color-editor"/);
    expect(cell).not.toMatch(/role="dialog"/);
    expect(css).toMatch(/\.li-color-editor input\{[^}]*width:8\.5rem/);
  });
});
