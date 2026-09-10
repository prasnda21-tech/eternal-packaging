import { act } from "react";
import { createRoot } from "react-dom/client";
import MobileTopBar from "./MobileTopBar";
import { MobileNavProvider } from "./MobileNavContext";

global.IS_REACT_ACT_ENVIRONMENT = true;

describe("MobileTopBar title + meta hierarchy", () => {
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

  test("renders customer primary title and PO secondary meta", () => {
    act(() => {
      root.render(
        <MobileNavProvider>
          <MobileTopBar
            kicker="Tugas Saya › Packing"
            title="TEST_QA reimport"
            meta="PO-QA15-DFAC2C"
            onBack={() => {}}
          />
        </MobileNavProvider>,
      );
    });
    expect(container.querySelector('[data-testid="mobile-topbar-kicker"]').textContent).toBe("Tugas Saya › Packing");
    expect(container.querySelector('[data-testid="mobile-topbar-title"]').tagName).toBe("H1");
    expect(container.querySelector('[data-testid="mobile-topbar-title"]').textContent).toBe("TEST_QA reimport");
    expect(container.querySelector('[data-testid="mobile-topbar-meta"]').textContent).toBe("PO-QA15-DFAC2C");
    expect(container.querySelector('[data-testid="mobile-back"]')).toBeTruthy();
    expect(container.querySelector('[data-testid="mobile-nav-menu"]')).toBeTruthy();
  });

  test("title-only pages stay unchanged without meta row overflow", () => {
    act(() => {
      root.render(
        <MobileNavProvider>
          <MobileTopBar title="Jadwal Pengiriman" />
        </MobileNavProvider>,
      );
    });
    expect(container.querySelector('[data-testid="mobile-topbar-title"]').textContent).toBe("Jadwal Pengiriman");
    expect(container.querySelector('[data-testid="mobile-topbar-meta"]')).toBeFalsy();
  });
});
