import fs from "fs";
import path from "path";

const css = fs.readFileSync(path.join(__dirname, "spk.css"), "utf8");
const page = fs.readFileSync(path.join(__dirname, "SpkCreate.jsx"), "utf8");

describe("Buat SPK layout", () => {
  test("workspace tabs stay in flow under the app header", () => {
    expect(css).toMatch(/\.content:has\(\.spk-page\) > \.kb-workspace-tabs/);
    expect(css).toMatch(/\.content:has\(\.spk-page\) > \.kb-workspace-tabs,[\s\S]*?top:\s*0/);
    expect(css).not.toMatch(/\.spk-head\{[^}]*grid-row/);
    expect(css).not.toMatch(/\.spk-page\{[^}]*display:\s*contents/);
    expect(page).toMatch(/className="spk-head"/);
    expect(page).toMatch(/spk-save-draft/);
    expect(page).toMatch(/spk-publish/);
  });

  test("create controls reclaim compact height from ui-system 42px inputs", () => {
    expect(css).toMatch(/\.content \.spk-create-page \.spk-field input:not\(\[type="checkbox"\]\)/);
    expect(css).toMatch(/\.content \.spk-create-page \.spk-field textarea\s*\{[^}]*min-height:\s*52px/);
    expect(css).toMatch(/\.content \.spk-create-page \.spk-ms-table input:not\(\[type="checkbox"\]\)[\s\S]*?height:\s*28px/);
    expect(page).toMatch(/Target Nilai/);
    expect(page).toMatch(/Bukti Wajib/);
    expect(page).toMatch(/className="spk-create-scroll"/);
  });
});
