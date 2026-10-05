import { act } from "react";
import { createRoot } from "react-dom/client";
import ProductionNotesPanel from "./ProductionNotesPanel";

global.IS_REACT_ACT_ENVIRONMENT = true;

const EXISTING = "SABLON WARNA HITAM\nPROMO SPOOKTOBER DEALS";

function setTextArea(el, value) {
  const setter = Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, "value").set;
  setter.call(el, value);
  el.dispatchEvent(new Event("input", { bubbles: true }));
  el.dispatchEvent(new Event("change", { bubbles: true }));
}

function mount(props) {
  const container = document.createElement("div");
  document.body.appendChild(container);
  const root = createRoot(container);
  const api = {
    onAppend: jest.fn().mockResolvedValue(undefined),
    onReplace: jest.fn().mockResolvedValue(undefined),
  };
  act(() => {
    root.render(
      <ProductionNotesPanel
        notes={EXISTING}
        visible
        canAppend={false}
        canEdit={false}
        busy={false}
        onAppend={api.onAppend}
        onReplace={api.onReplace}
        {...props}
      />
    );
  });
  return {
    container,
    api,
    unmount() {
      act(() => root.unmount());
      container.remove();
    },
  };
}

describe("ProductionNotesPanel", () => {
  test("hidden when the workspace does not show Catatan Produksi", () => {
    const { container, unmount } = mount({ visible: false, notes: EXISTING });
    expect(container.querySelector('[data-testid="notes-panel"]')).toBeNull();
    unmount();
  });

  test("CRM without create has no Tambah Catatan and no Edit", () => {
    const { container, unmount } = mount({ canAppend: false, canEdit: false });
    expect(container.querySelector('[data-testid="notes-panel"]')).toBeTruthy();
    expect(container.querySelector('[data-testid="notes-saved"]').textContent).toBe(EXISTING);
    expect(container.querySelector('[data-testid="toggle-note-composer"]')).toBeNull();
    expect(container.querySelector('[data-testid="edit-note-button"]')).toBeNull();
    unmount();
  });

  test("production_note.create shows Tambah Catatan", () => {
    const { container, unmount } = mount({ canAppend: true, canEdit: false });
    expect(container.querySelector('[data-testid="toggle-note-composer"]').textContent).toMatch(/Tambah Catatan/);
    expect(container.querySelector('[data-testid="edit-note-button"]')).toBeNull();
    unmount();
  });

  test("production_note.edit shows Edit and prefills the full note", () => {
    const { container, unmount } = mount({ canAppend: false, canEdit: true });
    const edit = container.querySelector('[data-testid="edit-note-button"]');
    expect(edit.textContent).toMatch(/Edit/);
    act(() => {
      edit.dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });
    const input = container.querySelector('[data-testid="edit-note-input"]');
    expect(input.value).toBe(EXISTING);
    expect(container.querySelector('[data-testid="notes-saved"]')).toBeNull();
    unmount();
  });

  test("Batal restores the read-only note", () => {
    const { container, api, unmount } = mount({ canEdit: true });
    act(() => {
      container.querySelector('[data-testid="edit-note-button"]')
        .dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });
    act(() => {
      setTextArea(container.querySelector('[data-testid="edit-note-input"]'), "SABLON WARNA MERAH");
    });
    act(() => {
      container.querySelector('[data-testid="cancel-edit-note-button"]')
        .dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });
    expect(container.querySelector('[data-testid="notes-saved"]').textContent).toBe(EXISTING);
    expect(container.querySelector('[data-testid="edit-note-input"]')).toBeNull();
    expect(api.onReplace).not.toHaveBeenCalled();
    unmount();
  });

  test("Simpan replaces the displayed note via the update callback", async () => {
    const { container, api, unmount } = mount({ canEdit: true });
    act(() => {
      container.querySelector('[data-testid="edit-note-button"]')
        .dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });
    act(() => {
      setTextArea(container.querySelector('[data-testid="edit-note-input"]'), "SABLON WARNA MERAH");
    });
    await act(async () => {
      container.querySelector('[data-testid="save-edit-note-button"]')
        .dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });
    expect(api.onReplace).toHaveBeenCalledTimes(1);
    expect(api.onReplace).toHaveBeenCalledWith("SABLON WARNA MERAH");
    expect(api.onAppend).not.toHaveBeenCalled();
    unmount();
  });

  test("Tambah Catatan appends only the new line", async () => {
    const { container, api, unmount } = mount({ canAppend: true, canEdit: true });
    act(() => {
      container.querySelector('[data-testid="toggle-note-composer"]')
        .dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });
    act(() => {
      setTextArea(container.querySelector('[data-testid="note-input"]'), "CUSTOMER MINTA DIPERCEPAT");
    });
    await act(async () => {
      container.querySelector('[data-testid="save-note-button"]')
        .dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });
    expect(api.onAppend).toHaveBeenCalledWith("CUSTOMER MINTA DIPERCEPAT");
    expect(api.onReplace).not.toHaveBeenCalled();
    unmount();
  });

  test("blank edit is blocked in the panel", async () => {
    const { container, api, unmount } = mount({ canEdit: true });
    act(() => {
      container.querySelector('[data-testid="edit-note-button"]')
        .dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });
    act(() => {
      setTextArea(container.querySelector('[data-testid="edit-note-input"]'), "   ");
    });
    const save = container.querySelector('[data-testid="save-edit-note-button"]');
    expect(save.disabled).toBe(true);
    expect(api.onReplace).not.toHaveBeenCalled();
    expect(container.querySelector('[data-testid="notes-saved"]')).toBeNull();
    expect(container.querySelector('[data-testid="edit-note-input"]').value).toBe("   ");
    unmount();
  });
});
