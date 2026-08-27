import fs from "fs";
import path from "path";

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
