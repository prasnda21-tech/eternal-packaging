import { act } from "react";
import { createRoot } from "react-dom/client";
import api from "../lib/api";
import CabangGudang from "./CabangGudang";

jest.mock("../lib/auth", () => ({
  useAuth: () => ({
    hasPerm: (key) => [
      "org.branches.view",
      "org.branches.manage",
      "org.warehouses.view",
      "org.warehouses.manage",
    ].includes(key),
  }),
}));

jest.mock("../lib/api", () => ({
  __esModule: true,
  default: {
    branches: jest.fn(),
    warehouses: jest.fn(),
  },
}));

global.IS_REACT_ACT_ENVIRONMENT = true;

describe("Cabang & Gudang empty vs error", () => {
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
    jest.clearAllMocks();
  });

  test("successful empty list shows Belum ada cabang", async () => {
    api.branches.mockResolvedValue({ items: [] });
    await act(async () => {
      root.render(<CabangGudang />);
    });
    await act(async () => {
      await Promise.resolve();
    });
    expect(container.querySelector('[data-testid="cabang-empty"]')?.textContent).toMatch(/Belum ada cabang/i);
    expect(container.querySelector('[data-testid="cabang-gudang-error"]')).toBeNull();
    expect(container.querySelector('[data-testid="cabang-load-failed"]')).toBeNull();
  });

  test("403/network error does not show Belum ada cabang", async () => {
    const err = new Error("Forbidden");
    err.response = { status: 403, data: { detail: "Forbidden" } };
    api.branches.mockRejectedValue(err);
    await act(async () => {
      root.render(<CabangGudang />);
    });
    await act(async () => {
      await Promise.resolve();
    });
    expect(container.querySelector('[data-testid="cabang-empty"]')).toBeNull();
    expect(container.querySelector('[data-testid="cabang-gudang-error"]')?.textContent).toMatch(/Forbidden/i);
    expect(container.querySelector('[data-testid="cabang-load-failed"]')?.textContent).toMatch(/gagal dimuat/i);
  });
});
