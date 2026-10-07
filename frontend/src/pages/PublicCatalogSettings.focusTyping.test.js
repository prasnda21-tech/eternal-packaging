import { act } from "react";
import { createRoot } from "react-dom/client";

globalThis.IS_REACT_ACT_ENVIRONMENT = true;

jest.mock("react-router-dom", () => require("../test/reactRouterMock"), { virtual: true });

jest.mock("lucide-react", () => new Proxy({}, {
  get: (_target, prop) => {
    if (prop === "__esModule") return true;
    return (props) => <span data-icon={String(prop)} {...props} />;
  },
}), { virtual: true });

jest.mock("../lib/api", () => ({
  __esModule: true,
  default: {
    publicCatalogSettings: jest.fn(),
    publicCatalogCategories: jest.fn(),
    publicCatalogGroups: jest.fn(),
    updatePublicCatalogSettings: jest.fn(),
    updatePublicCatalogGroup: jest.fn(),
    publicCatalogVariants: jest.fn(),
    updatePublicCatalogVariant: jest.fn(),
    publicCatalogVariantImages: jest.fn(),
    customerPricingCatalogImageUrl: jest.fn(() => ""),
  },
}));

import api from "../lib/api";
import PublicCatalogSettings from "./PublicCatalogSettings";

const mockApi = api;
const GROUP_A = {
  pricing_group_id: "pg-a",
  group_name: "FX Cup Inject",
  pricing_enabled: true,
  public_description: "desc",
  catalog_image: null,
  catalog: {
    configured: true,
    category_id: "cat-1",
    category_name: "Cup",
    public_name: "Cup Inject",
    slug: "cup-inject",
    display_order: 1,
    visible: true,
  },
};

const GROUP_B = {
  pricing_group_id: "pg-b",
  group_name: "FX Bowl",
  pricing_enabled: true,
  public_description: "",
  catalog_image: null,
  catalog: {
    configured: true,
    category_id: "cat-2",
    category_name: "Bowl",
    public_name: "Bowl Premium",
    slug: "bowl-premium",
    display_order: 2,
    visible: true,
  },
};

function setNativeValue(el, value) {
  const proto = el.tagName === "TEXTAREA"
    ? window.HTMLTextAreaElement.prototype
    : window.HTMLInputElement.prototype;
  const setter = Object.getOwnPropertyDescriptor(proto, "value")?.set;
  setter.call(el, value);
}

/** Character-by-character typing (not a single bulk change). */
async function typeChars(el, text) {
  for (const ch of text) {
    await act(async () => {
      el.focus();
      const next = `${el.value || ""}${ch}`;
      setNativeValue(el, next);
      el.dispatchEvent(new Event("input", { bubbles: true }));
    });
  }
}

async function flush() {
  await act(async () => {
    await Promise.resolve();
  });
}

describe("PublicCatalogSettings drawer continuous typing focus", () => {
  let container;
  let root;
  let portalHost;

  beforeEach(async () => {
    mockApi.publicCatalogSettings.mockResolvedValue({
      settings: {
        catalog_name: "Catalog",
        hero_title: "Title",
        hero_subtitle: "",
        public_whatsapp_phone: "",
        catalog_enabled: true,
      },
    });
    mockApi.publicCatalogCategories.mockResolvedValue({
      categories: [
        { id: "cat-1", name: "Cup", enabled: true, display_order: 1 },
        { id: "cat-2", name: "Bowl", enabled: true, display_order: 2 },
      ],
    });
    mockApi.publicCatalogGroups.mockResolvedValue({ groups: [GROUP_A, GROUP_B] });
    mockApi.publicCatalogVariants.mockResolvedValue({
      variants: [
        {
          accurate_item_id: "item-1",
          pricing_group_id: "pg-a",
          display_name: "12oz",
          item_code: "CUP-12",
          unit: "pcs",
          is_active: true,
          catalog: { configured: false },
        },
      ],
    });
    mockApi.publicCatalogVariantImages.mockResolvedValue({ images: [], legacy_image: null });

    container = document.createElement("div");
    document.body.appendChild(container);
    portalHost = document.body;
    root = createRoot(container);
  });

  afterEach(async () => {
    await act(async () => {
      root.unmount();
    });
    container.remove();
    document.querySelectorAll("[data-testid='pcs-group-drawer'], [data-testid='pcs-variant-drawer']")
      .forEach((node) => node.remove());
  });

  async function renderEmbedded(groupId = "pg-a") {
    await act(async () => {
      root.render(
        <PublicCatalogSettings
          notify={jest.fn()}
          canEdit
          embedded
          section="kelompok"
          selectedGroupId={groupId}
        />
      );
    });
    await flush();
    await flush();
  }

  test("Nama Publik accumulates characters and retains focus without remount", async () => {
    await renderEmbedded("pg-a");
    const input = portalHost.querySelector('[data-testid="pcs-group-editor-public-name"]');
    expect(input).toBeTruthy();

    await act(async () => {
      input.focus();
      setNativeValue(input, "");
      input.dispatchEvent(new Event("input", { bubbles: true }));
    });

    const nodeBefore = input;
    await typeChars(input, "ABCDE");
    expect(document.activeElement).toBe(nodeBefore);
    expect(nodeBefore.value).toBe("ABCDE");

    await typeChars(nodeBefore, "123");
    expect(document.activeElement).toBe(nodeBefore);
    expect(nodeBefore.value).toBe("ABCDE123");
    expect(portalHost.querySelector('[data-testid="pcs-group-editor-public-name"]')).toBe(nodeBefore);
  });

  test("Slug continuous typing retains focus", async () => {
    await renderEmbedded("pg-a");
    const input = portalHost.querySelector('[data-testid="pcs-group-editor-slug"]');
    expect(input).toBeTruthy();

    await act(async () => {
      input.focus();
      setNativeValue(input, "");
      input.dispatchEvent(new Event("input", { bubbles: true }));
    });

    const nodeBefore = input;
    await typeChars(input, "cup-inject-test");
    expect(document.activeElement).toBe(nodeBefore);
    expect(nodeBefore.value).toBe("cup-inject-test");
  });

  test("same-group groups refresh preserves draft; A→B refreshes draft", async () => {
    let controlledId = "pg-a";
    const renderWith = async (id, groups) => {
      mockApi.publicCatalogGroups.mockResolvedValue({ groups });
      await act(async () => {
        root.render(
          <PublicCatalogSettings
            notify={jest.fn()}
            canEdit
            embedded
            section="kelompok"
            selectedGroupId={id}
          />
        );
      });
      await flush();
      await flush();
    };

    await renderWith(controlledId, [GROUP_A, GROUP_B]);
    const nameInput = portalHost.querySelector('[data-testid="pcs-group-editor-public-name"]');
    await act(async () => {
      nameInput.focus();
      setNativeValue(nameInput, "");
      nameInput.dispatchEvent(new Event("input", { bubbles: true }));
    });
    await typeChars(nameInput, "TypedLocal");
    expect(nameInput.value).toBe("TypedLocal");

    // Same group, new array identity (list refresh)
    const refreshedA = { ...GROUP_A, catalog: { ...GROUP_A.catalog, public_name: "Server Name" } };
    await renderWith("pg-a", [refreshedA, GROUP_B]);
    const afterRefresh = portalHost.querySelector('[data-testid="pcs-group-editor-public-name"]');
    expect(afterRefresh.value).toBe("TypedLocal");
    expect(document.activeElement === afterRefresh || afterRefresh.value === "TypedLocal").toBe(true);

    // Switch group A → B
    controlledId = "pg-b";
    await renderWith(controlledId, [refreshedA, GROUP_B]);
    const groupBInput = portalHost.querySelector('[data-testid="pcs-group-editor-public-name"]');
    expect(groupBInput.value).toBe("Bowl Premium");
  });

  test("variant drawer typing does not steal focus to close button", async () => {
    await act(async () => {
      root.render(
        <PublicCatalogSettings
          notify={jest.fn()}
          canEdit
          embedded
          section="varian"
          selectedGroupId="pg-a"
        />
      );
    });
    await flush();
    await flush();

    const configure = portalHost.querySelector('[data-testid="pcs-variant-configure"]')
      || container.querySelector('[data-testid="pcs-variant-configure"]');
    expect(configure).toBeTruthy();
    await act(async () => {
      configure.dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });
    await flush();

    const sizeInput = document.querySelector('[data-testid="pcs-variant-editor-size"]');
    expect(sizeInput).toBeTruthy();
    await act(async () => {
      sizeInput.focus();
      setNativeValue(sizeInput, "");
      sizeInput.dispatchEvent(new Event("input", { bubbles: true }));
    });
    const node = sizeInput;
    await typeChars(node, "12oz");
    expect(document.activeElement).toBe(node);
    expect(node.value).toBe("12oz");
    expect(document.activeElement?.getAttribute("data-testid")).not.toBe("pcs-variant-drawer-close");
  });

  test("Escape still closes group drawer", async () => {
    await renderEmbedded("pg-a");
    expect(document.querySelector('[data-testid="pcs-group-drawer"]')).toBeTruthy();
    await act(async () => {
      window.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape", bubbles: true }));
    });
    await flush();
    expect(document.querySelector('[data-testid="pcs-group-drawer"]')).toBeFalsy();
  });
});
