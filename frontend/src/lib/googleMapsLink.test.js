import {
  googleMapsLinkNeedsResolve,
  parseGoogleMapsCoordinates,
  parseGoogleMapsUrlRecord,
} from "./googleMapsLink";

describe("parseGoogleMapsCoordinates", () => {
  test("1. google.com/maps URL containing @lat,lng", () => {
    const coords = parseGoogleMapsCoordinates(
      "https://www.google.com/maps/place/Monas/@-6.175392,106.827153,17z"
    );
    expect(coords).toEqual({ lat: -6.175392, lng: 106.827153 });
  });

  test("2. maps.google.com URL containing q=lat,lng", () => {
    const coords = parseGoogleMapsCoordinates("https://maps.google.com/maps?q=-6.2,106.8");
    expect(coords).toEqual({ lat: -6.2, lng: 106.8 });
  });

  test("3. Google Maps query URL containing query=lat,lng", () => {
    const coords = parseGoogleMapsCoordinates(
      "https://www.google.com/maps/search/?api=1&query=-6.175,106.827"
    );
    expect(coords).toEqual({ lat: -6.175, lng: 106.827 });
  });

  test("4. maps.app.goo.gl short link needs safe resolve", () => {
    const record = parseGoogleMapsUrlRecord("https://maps.app.goo.gl/AbCdEf123");
    expect(record.status).toBe("NEEDS_RESOLVE");
    expect(parseGoogleMapsCoordinates("https://maps.app.goo.gl/AbCdEf123")).toBeNull();
    expect(googleMapsLinkNeedsResolve("https://maps.app.goo.gl/AbCdEf123")).toBe(true);
  });

  test("5. surrounding whitespace is trimmed and accepted", () => {
    const coords = parseGoogleMapsCoordinates(
      "  https://www.google.com/maps/@-6.2,106.8,17z  \n"
    );
    expect(coords).toEqual({ lat: -6.2, lng: 106.8 });
  });

  test("6. malformed URL is rejected", () => {
    expect(parseGoogleMapsUrlRecord("not a url").status).toBe("INVALID_LINK");
    expect(parseGoogleMapsCoordinates("not a url")).toBeNull();
  });

  test("7. non-Google URL is rejected", () => {
    expect(parseGoogleMapsUrlRecord("https://example.com/maps/@-6.2,106.8").status).toBe(
      "UNSUPPORTED_HOST"
    );
    expect(parseGoogleMapsCoordinates("https://example.com/?q=-6.2,106.8")).toBeNull();
  });

  test("8. unsupported protocol is rejected", () => {
    expect(parseGoogleMapsUrlRecord("http://www.google.com/maps/@-6.2,106.8,17z").status).toBe(
      "INVALID_LINK"
    );
    expect(parseGoogleMapsUrlRecord("file:///tmp/maps").status).toBe("INVALID_LINK");
  });

  test("9. latitude > 90 is rejected", () => {
    expect(parseGoogleMapsUrlRecord("https://www.google.com/maps/@91,106.8,17z").status).toBe(
      "INVALID_LINK"
    );
    expect(parseGoogleMapsCoordinates("https://www.google.com/maps/@91,106.8,17z")).toBeNull();
  });

  test("9b. latitude < -90 is rejected", () => {
    expect(parseGoogleMapsUrlRecord("https://www.google.com/maps/@-91,106.8,17z").status).toBe(
      "INVALID_LINK"
    );
  });

  test("10. longitude > 180 is rejected", () => {
    expect(parseGoogleMapsUrlRecord("https://maps.google.com/?q=-6.2,181").status).toBe(
      "INVALID_LINK"
    );
    expect(parseGoogleMapsCoordinates("https://maps.google.com/?q=-6.2,181")).toBeNull();
  });

  test("10b. longitude < -180 is rejected", () => {
    expect(parseGoogleMapsUrlRecord("https://maps.google.com/?q=-6.2,-181").status).toBe(
      "INVALID_LINK"
    );
  });

  test("11. URL without resolvable coordinates does not invent 0,0", () => {
    const record = parseGoogleMapsUrlRecord(
      "https://www.google.com/maps/place/Monas"
    );
    expect(record.status).toBe("NO_COORDINATES");
    expect(record.lat).toBeUndefined();
    expect(parseGoogleMapsCoordinates("https://www.google.com/maps/place/Monas")).toBeNull();
  });
});
