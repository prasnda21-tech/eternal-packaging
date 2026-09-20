import fs from "fs";
import path from "path";

const css = fs.readFileSync(path.join(__dirname, "productRequest.css"), "utf8");
const form = fs.readFileSync(path.join(__dirname, "ProductRequestForm.jsx"), "utf8");

describe("product request no-scroll contract", () => {
  test("locks the Eternal shell to one viewport and keeps actions visible", () => {
    expect(css).toMatch(/\.app-shell:has\(\.prq-page\)/);
    expect(css).toMatch(/height:calc\(100vh - var\(--ep-topbar-height/);
    expect(css).toMatch(/\.prq-actions/);
    expect(css).toMatch(/font-size:12px/);
    expect(form).toMatch(/prq-save-draft/);
    expect(form).toMatch(/prq-submit/);
    expect(form).toMatch(/prq-summary/);
    expect(form).toMatch(/prq-customer-card/);
    expect(form).toMatch(/prq-product-card/);
    expect(form).toMatch(/prq-potential-card/);
  });

  test("compacts form sections without clipping or fake offsets", () => {
    expect(css).toMatch(/grid-template-columns:minmax\(0,1fr\) 300px/);
    expect(css).toMatch(/align-self:start/);
    expect(css).toMatch(/height:31px/);
    expect(css).toMatch(/min-height:38px/);
    expect(css).not.toMatch(/margin\s*:\s*-/);
    expect(css).not.toMatch(/transform\s*:/);
    expect(css).not.toMatch(/\.prq-card\{[^}]*position:\s*absolute/);
    expect(css).not.toMatch(/\.prq-progress\{[^}]*position:\s*absolute/);
    expect(css).not.toMatch(/\.prq-card\{[^}]*overflow:\s*hidden/);
    expect(css).not.toMatch(/\.prq-main\{[^}]*overflow:\s*hidden/);
    expect(form).toMatch(/prq-grid is-product/);
    expect(form).toMatch(/prq-grid is-potential/);
    expect(form).toMatch(/prq-material-label/);
    expect(form).toMatch(/prq-model-label/);
    expect(css).toMatch(/\.prq-progress\{[^}]*min-height:32px/);
    expect(css).toMatch(/\.prq-progress\{[^}]*overflow:visible/);
    expect(css).toMatch(/\.prq-progress\{[^}]*padding-block:5px/);
    expect(css).toMatch(/\.prq-progress\{[^}]*margin-top:16px/);
    expect(css).toMatch(/\.prq-progress\{[^}]*margin-bottom:2px/);
  });

  test("list page header sits above workspace tabs without overlap hacks", () => {
    expect(css).toMatch(/\.content:has\(\.prq-list-page\)/);
    expect(css).toMatch(/display:contents/);
    expect(css).toMatch(/\.content:has\(\.prq-list-page\) > \.kb-workspace-tabs/);
    expect(css).toMatch(/position:static/);
    expect(css).not.toMatch(/\.prq-list-head\{[^}]*transform/);
    expect(css).not.toMatch(/\.prq-list-head\{[^}]*margin-top:\s*-/);
  });
});
