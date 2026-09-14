import {
  applyMapsLinkSelection,
  applyPlacesSelection,
  buildSaveDeliveryOutletPayload,
  formatCoordinateAddress,
  nextOutletName,
} from "./outletLocationSelection";

const placesPoint = {
  place_id: "ChIJ-monas",
  formatted_address: "Gambir, Jakarta",
  lat: -6.175,
  lng: 106.827,
  name: "Monas",
};

const pastedPoint = { lat: -6.2, lng: 106.8 };

describe("outlet location selection", () => {
  test("12. existing Google Places selection still produces canonical lat/lng", () => {
    const next = applyPlacesSelection(placesPoint, "");
    expect(next.picked.lat).toBe(-6.175);
    expect(next.picked.lng).toBe(106.827);
    expect(next.picked.place_id).toBe("ChIJ-monas");
    expect(next.picked.formatted_address).toBe("Gambir, Jakarta");
  });

  test("13. paste-link selection updates the same canonical lat/lng fields", () => {
    const next = applyMapsLinkSelection(pastedPoint, "Outlet Jakarta", null);
    expect(next.picked.lat).toBe(-6.2);
    expect(next.picked.lng).toBe(106.8);
    expect(next.picked).toEqual(expect.objectContaining({
      lat: -6.2,
      lng: 106.8,
      formatted_address: formatCoordinateAddress(-6.2, 106.8),
    }));
  });

  test("14. existing outlet name is not overwritten by a pasted link", () => {
    expect(nextOutletName("Outlet Jakarta", "Monas")).toBe("Outlet Jakarta");
    const next = applyMapsLinkSelection(pastedPoint, "Outlet Jakarta", { name: "Monas" });
    expect(next.name).toBe("Outlet Jakarta");
  });

  test("15. successful pasted location uses the same save payload shape as Places", () => {
    const row = { order_id: "ord-1", stop_id: "st-1", customer: "PT Fixture" };
    const fromPlaces = applyPlacesSelection(placesPoint, "Outlet Jakarta");
    const fromLink = applyMapsLinkSelection(pastedPoint, "Outlet Jakarta", null);
    const placesSave = buildSaveDeliveryOutletPayload({
      row,
      batchId: "batch-1",
      name: fromPlaces.name,
      picked: fromPlaces.picked,
    });
    const linkSave = buildSaveDeliveryOutletPayload({
      row,
      batchId: "batch-1",
      name: fromLink.name,
      picked: fromLink.picked,
    });
    expect(Object.keys(placesSave)).toEqual(Object.keys(linkSave));
    expect(linkSave).toEqual(expect.objectContaining({
      order_id: "ord-1",
      batch_id: "batch-1",
      stop_id: "st-1",
      name: "Outlet Jakarta",
      lat: -6.2,
      lng: 106.8,
    }));
    expect(placesSave.lat).toBe(-6.175);
    expect(placesSave.lng).toBe(106.827);
  });

  test("16. resolve failure keeps the previous selected location", () => {
    const previous = applyPlacesSelection(placesPoint, "Outlet Jakarta").picked;
    const failed = null;
    const kept = failed || previous;
    expect(kept).toEqual(previous);
    expect(kept.lat).toBe(-6.175);
    expect(kept.lng).toBe(106.827);
  });

  test("17. changing from pasted link to Places makes Places canonical", () => {
    const pasted = applyMapsLinkSelection(pastedPoint, "Outlet Jakarta", null);
    const places = applyPlacesSelection(placesPoint, pasted.name);
    expect(places.picked.lat).toBe(-6.175);
    expect(places.picked.lng).toBe(106.827);
    expect(places.picked.place_id).toBe("ChIJ-monas");
    expect(places.name).toBe("Outlet Jakarta");
  });

  test("18. changing from Places to pasted link makes the pasted point canonical", () => {
    const places = applyPlacesSelection(placesPoint, "Outlet Jakarta");
    const pasted = applyMapsLinkSelection(pastedPoint, places.name, places.picked);
    expect(pasted.picked.lat).toBe(-6.2);
    expect(pasted.picked.lng).toBe(106.8);
    expect(pasted.picked.place_id).toBe("");
    expect(pasted.name).toBe("Outlet Jakarta");
  });
});
