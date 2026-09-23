import fs from "fs";
import path from "path";

const css = fs.readFileSync(path.join(__dirname, "spk.css"), "utf8");
const page = fs.readFileSync(path.join(__dirname, "SpkCreate.jsx"), "utf8");

function cssBlocks(source, selector) {
  const clean = source.replace(/\/\*[\s\S]*?\*\//g, "");
  const blocks = [];
  const re = /([^{}]+)\{([^{}]*)\}/g;
  let match = re.exec(clean);
  while (match) {
    const selectors = match[1].split(",").map((item) => item.trim());
    if (selectors.includes(selector)) blocks.push(match[2]);
    match = re.exec(clean);
  }
  return blocks;
}

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

  test("milestone list grows inside the create-page scroll owner", () => {
    const milestone = cssBlocks(css, ".spk-milestone-scroll").join("\n");
    const owner = cssBlocks(css, ".spk-create-scroll").join("\n");
    const listScroll = cssBlocks(css, ".spk-table-scroll").join("\n");
    expect(milestone).toMatch(/overflow-x:\s*auto/);
    expect(milestone).toMatch(/overflow-y:\s*hidden/);
    expect(milestone).toMatch(/flex:\s*0\s*0\s*auto/);
    expect(milestone).toMatch(/max-height:\s*none/);
    expect(milestone).toMatch(/height:\s*auto/);
    expect(milestone).not.toMatch(/overflow\s*:\s*auto/);
    expect(milestone).not.toMatch(/overflow-y\s*:\s*(auto|scroll)/);
    expect(milestone).not.toMatch(/flex\s*:\s*1\b/);
    expect(owner).toMatch(/overflow-y:\s*auto/);
    expect(owner).toMatch(/overflow-x:\s*hidden/);
    expect(listScroll).toMatch(/overflow:\s*auto/);
    expect(css).toMatch(/\.spk-page\s*\{[^}]*overflow:\s*hidden/);
    expect(css).toMatch(/\.content:has\(\.spk-page\)\s*\{[^}]*overflow:\s*hidden/);
    expect(css).toMatch(/\.spk-ms-table\s*\{[^}]*min-width:\s*720px/);
    const scrollAt = page.indexOf('className="spk-milestone-scroll"');
    const addAt = page.indexOf('data-testid="spk-add-milestone"');
    expect(scrollAt).toBeGreaterThan(-1);
    expect(addAt).toBeGreaterThan(scrollAt);
    expect(page.slice(scrollAt, addAt)).toMatch(/<\/table>/);
  });

  test("PIC panel is an overlay with a bounded list scroll", () => {
    const panel = cssBlocks(css, ".spk-pic-panel").join("\n");
    const list = cssBlocks(css, ".spk-pic-list").join("\n");
    expect(panel).toMatch(/position:\s*fixed/);
    expect(panel).toMatch(/z-index:\s*45/);
    expect(panel).toMatch(/max-height:\s*300px/);
    expect(panel).toMatch(/max-width:\s*calc\(100vw - 16px\)/);
    expect(list).toMatch(/overflow-y:\s*auto/);
    expect(page).toMatch(/aria-expanded=\{open\}/);
    expect(page).toMatch(/aria-controls="spk-pic-panel"/);
    expect(page).toMatch(/htmlFor="spk-pic-trigger"/);
    expect(page).toMatch(/placeholder="Cari PIC"/);
    expect(page).toMatch(/PIC tidak ditemukan/);
    expect(page).toMatch(/Maksimal 12 PIC/);
    expect(page).toMatch(/Pilih PIC/);
  });
});
