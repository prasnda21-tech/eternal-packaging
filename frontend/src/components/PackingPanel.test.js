import fs from "fs";
import path from "path";

describe("Packing card confirm-only", () => {
  const src = fs.readFileSync(path.join(__dirname, "PackingPanel.jsx"), "utf8");
  const role = fs.readFileSync(path.join(__dirname, "RoleWorkPanel.jsx"), "utf8");

  test("Packing Sebagian button is absent", () => {
    expect(src).not.toMatch(/Packing Sebagian/);
    expect(src).not.toMatch(/Lanjut Packing/);
    expect(src).not.toMatch(/complete-packing-button/);
    expect(src).not.toMatch(/onComplete/);
    expect(role).not.toMatch(/Packing Sebagian/);
  });

  test("Konfirmasi + append-only + action_id remain", () => {
    const append = fs.readFileSync(path.join(__dirname, "AppendQtyEntries.jsx"), "utf8");
    expect(append).toMatch(/Konfirmasi/);
    expect(src).toMatch(/ConfirmActionModal/);
    expect(src).toMatch(/action_id: confirm\.actionId/);
    expect(src).toMatch(/AppendQtyEntries/);
  });
});
