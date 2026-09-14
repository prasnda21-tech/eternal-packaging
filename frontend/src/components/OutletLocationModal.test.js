import fs from "fs";
import path from "path";
import { act } from "react";
import { createRoot } from "react-dom/client";
import OutletLocationModal from "./OutletLocationModal";

global.IS_REACT_ACT_ENVIRONMENT = true;

const mockSearch = jest.fn();
const mockDetails = jest.fn();
const mockResolve = jest.fn();
const mockSave = jest.fn();

jest.mock("../lib/api", () => ({
  __esModule: true,
  default: {
    searchDeliveryPlaces: (...args) => mockSearch(...args),
    deliveryPlaceDetails: (...args) => mockDetails(...args),
    resolveDeliveryMapsLink: (...args) => mockResolve(...args),
    saveDeliveryOutlet: (...args) => mockSave(...args),
  },
}));

jest.mock("./OutletLocationMap", () => ({
  __esModule: true,
  default: ({ lat, lng, placeKey }) => (
    <div data-testid="outlet-loc-map-stub">{lat},{lng},{placeKey}</div>
  ),
}));

function setInputValue(el, value) {
  const proto = HTMLInputElement.prototype;
  const setter = Object.getOwnPropertyDescriptor(proto, "value").set;
  setter.call(el, value);
  el.dispatchEvent(new Event("input", { bubbles: true }));
  el.dispatchEvent(new Event("change", { bubbles: true }));
}

const row = {
  order_id: "ord-1",
  stop_id: "st-1",
  customer: "PT Fixture",
  outlet_name: "Outlet Jakarta",
};

describe("outlet location modal", () => {
  const modal = fs.readFileSync(path.join(__dirname, "OutletLocationModal.jsx"), "utf8");
  const map = fs.readFileSync(path.join(__dirname, "OutletLocationMap.jsx"), "utf8");

  test("google place picker fields without manual lat lng as primary ux", () => {
    expect(modal).toMatch(/ATUR LOKASI PENGIRIMAN/);
    expect(modal).toMatch(/outlet-loc-customer/);
    expect(modal).toMatch(/Nama Outlet \/ Lokasi/);
    expect(modal).toMatch(/Cari Outlet \/ Alamat/);
    expect(modal).toMatch(/JUS\.IN Kelapa Gading/);
    expect(modal).toMatch(/Alamat Terpilih/);
    expect(modal).toMatch(/OutletLocationMap/);
    expect(modal).toMatch(/Simpan/);
    expect(modal).toMatch(/searchDeliveryPlaces/);
    expect(modal).toMatch(/deliveryPlaceDetails/);
    expect(modal).toMatch(/saveDeliveryOutlet/);
    expect(modal).toMatch(/Link Google Maps/);
    expect(modal).toMatch(/Gunakan Lokasi/);
    expect(modal).toMatch(/Tempel link Google Maps di sini/);
    expect(modal).not.toMatch(/placeholder="Lat"/);
    expect(modal).not.toMatch(/placeholder="Lng"/);
    expect(modal).not.toMatch(/placeholder="Place ID"/);
    expect(modal).not.toMatch(/GOOGLE_PLACES_API_KEY/);
    expect(modal).not.toMatch(/type="number"/);
  });

  test("map preview shows a marker that can be corrected", () => {
    const helpers = fs.readFileSync(path.join(__dirname, "../lib/outletMap.js"), "utf8");
    expect(map).toMatch(/outlet-loc-map/);
    expect(map).toMatch(/outlet-loc-marker/);
    expect(map).toMatch(/gmpDraggable/);
    expect(helpers).toMatch(/REACT_APP_GOOGLE_MAPS_BROWSER_KEY/);
    expect(helpers).toMatch(/https:\/\/maps\.googleapis\.com\/maps\/api\/js/);
    expect(map).not.toMatch(/GOOGLE_PLACES_API_KEY/);
  });
});

describe("outlet location modal behavior", () => {
  let container;
  let root;

  beforeEach(() => {
    mockSearch.mockReset();
    mockDetails.mockReset();
    mockResolve.mockReset();
    mockSave.mockReset();
    mockSave.mockResolvedValue({ outlet: { id: "out-1", lat: -6.2, lng: 106.8 } });
    container = document.createElement("div");
    document.body.appendChild(container);
    root = createRoot(container);
  });

  afterEach(async () => {
    await act(async () => {
      root.unmount();
    });
    container.remove();
  });

  test("paste Google Maps link then Simpan uses canonical saveDeliveryOutlet", async () => {
    await act(async () => {
      root.render(
        <OutletLocationModal open row={row} batchId="batch-1" onClose={() => {}} onSaved={() => {}} />
      );
    });
    expect(container.querySelector('[data-testid="outlet-loc-search"]')).toBeTruthy();
    expect(container.querySelector('[data-testid="outlet-loc-maps-link"]')).toBeTruthy();
    await act(async () => {
      setInputValue(
        container.querySelector('[data-testid="outlet-loc-maps-link"]'),
        "https://www.google.com/maps/@-6.2,106.8,17z"
      );
    });
    await act(async () => {
      container.querySelector('[data-testid="outlet-loc-use-maps-link"]').click();
    });
    expect(container.querySelector('[data-testid="outlet-loc-maps-status"]').textContent).toBe(
      "Lokasi ditemukan."
    );
    expect(container.querySelector('[data-testid="outlet-loc-selected"]').textContent).toMatch(
      /Koordinat -6\.2, 106\.8/
    );
    expect(container.querySelector('[data-testid="outlet-loc-name"]').value).toBe("Outlet Jakarta");
    expect(mockSave).not.toHaveBeenCalled();
    await act(async () => {
      container.querySelector('[data-testid="outlet-loc-save"]').click();
    });
    expect(mockSave).toHaveBeenCalledWith(expect.objectContaining({
      order_id: "ord-1",
      batch_id: "batch-1",
      name: "Outlet Jakarta",
      lat: -6.2,
      lng: 106.8,
    }));
    expect(mockResolve).not.toHaveBeenCalled();
  });

  test("Places selection still works and overwrites a pasted point", async () => {
    mockDetails.mockResolvedValue({
      place: {
        place_id: "ChIJ-monas",
        formatted_address: "Gambir, Jakarta",
        lat: -6.175,
        lng: 106.827,
        name: "Monas",
      },
    });
    await act(async () => {
      root.render(<OutletLocationModal open row={row} onClose={() => {}} onSaved={() => {}} />);
    });
    await act(async () => {
      setInputValue(
        container.querySelector('[data-testid="outlet-loc-maps-link"]'),
        "https://maps.google.com/?q=-6.2,106.8"
      );
    });
    await act(async () => {
      container.querySelector('[data-testid="outlet-loc-use-maps-link"]').click();
    });
    const pick = { place_id: "ChIJ-monas", label: "Monas", formatted_address: "Gambir, Jakarta" };
    mockSearch.mockResolvedValue({ provider: "google", suggestions: [pick] });
    await act(async () => {
      setInputValue(container.querySelector('[data-testid="outlet-loc-search"]'), "Monas");
    });
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 320));
    });
    await act(async () => {
      container.querySelector('[data-testid="outlet-loc-pick-ChIJ-monas"]').click();
    });
    expect(mockDetails).toHaveBeenCalledWith("ChIJ-monas");
    expect(container.querySelector('[data-testid="outlet-loc-selected"]').textContent).toBe(
      "Gambir, Jakarta"
    );
    expect(container.querySelector('[data-testid="outlet-loc-name"]').value).toBe("Outlet Jakarta");
    await act(async () => {
      container.querySelector('[data-testid="outlet-loc-save"]').click();
    });
    expect(mockSave).toHaveBeenCalledWith(expect.objectContaining({
      lat: -6.175,
      lng: 106.827,
      place_id: "ChIJ-monas",
    }));
  });

  test("failed short-link resolve does not corrupt the previous Places selection", async () => {
    mockDetails.mockResolvedValue({
      place: {
        place_id: "ChIJ-keep",
        formatted_address: "Kept Address",
        lat: -6.1,
        lng: 106.7,
        name: "Kept",
      },
    });
    mockSearch.mockResolvedValue({
      provider: "google",
      suggestions: [{ place_id: "ChIJ-keep", label: "Kept", formatted_address: "Kept Address" }],
    });
    mockResolve.mockRejectedValue({
      response: { data: { detail: { code: "RESOLVE_FAILURE", message: "Link Google Maps tidak dapat diproses. Coba salin ulang link dari Google Maps." } } },
    });
    await act(async () => {
      root.render(<OutletLocationModal open row={row} onClose={() => {}} onSaved={() => {}} />);
    });
    await act(async () => {
      setInputValue(container.querySelector('[data-testid="outlet-loc-search"]'), "Kept");
    });
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 320));
    });
    await act(async () => {
      container.querySelector('[data-testid="outlet-loc-pick-ChIJ-keep"]').click();
    });
    expect(container.querySelector('[data-testid="outlet-loc-selected"]').textContent).toBe("Kept Address");
    await act(async () => {
      setInputValue(
        container.querySelector('[data-testid="outlet-loc-maps-link"]'),
        "https://maps.app.goo.gl/broken"
      );
    });
    await act(async () => {
      container.querySelector('[data-testid="outlet-loc-use-maps-link"]').click();
    });
    expect(container.querySelector('[data-testid="outlet-loc-maps-status"]').textContent).toMatch(
      /tidak dapat diproses/
    );
    expect(container.querySelector('[data-testid="outlet-loc-selected"]').textContent).toBe("Kept Address");
    expect(mockSave).not.toHaveBeenCalled();
  });

  test("Enter on the maps link field resolves and does not save", async () => {
    await act(async () => {
      root.render(<OutletLocationModal open row={row} onClose={() => {}} onSaved={() => {}} />);
    });
    const input = container.querySelector('[data-testid="outlet-loc-maps-link"]');
    await act(async () => {
      setInputValue(input, "https://www.google.com/maps/search/?api=1&query=-6.175,106.827");
    });
    await act(async () => {
      input.dispatchEvent(new KeyboardEvent("keydown", { key: "Enter", bubbles: true, cancelable: true }));
    });
    expect(container.querySelector('[data-testid="outlet-loc-selected"]').textContent).toMatch(
      /Koordinat -6\.175, 106\.827/
    );
    expect(mockSave).not.toHaveBeenCalled();
  });
});
