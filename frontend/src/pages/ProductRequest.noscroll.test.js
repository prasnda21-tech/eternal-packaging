import fs from "fs";
import path from "path";

const css = fs.readFileSync(path.join(__dirname, "productRequest.css"), "utf8");
const form = fs.readFileSync(path.join(__dirname, "ProductRequestForm.jsx"), "utf8");

function cssBlock(selector) {
  const escaped = selector.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  const match = css.match(new RegExp(`${escaped}\\{[^}]*\\}`));
  return match ? match[0] : "";
}

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
    expect(css).toMatch(/grid-template-columns:minmax\(0,1fr\) 280px/);
    expect(css).toMatch(/align-self:start/);
    expect(css).toMatch(/height:31px/);
    expect(css).toMatch(/min-height:30px/);
    expect(css).not.toMatch(/margin\s*:\s*-/);
    expect(css).not.toMatch(/transform\s*:/);
    expect(css).not.toMatch(/zoom\s*:/);
    expect(css).not.toMatch(/\.prq-card\{[^}]*position:\s*absolute/);
    expect(css).not.toMatch(/\.prq-progress\{[^}]*position:\s*absolute/);
    expect(css).not.toMatch(/\.prq-card\{[^}]*overflow:\s*hidden/);
    expect(css).not.toMatch(/\.prq-main\{[^}]*overflow:\s*hidden/);
    expect(css).not.toMatch(/\.prq-form-page\{[^}]*overflow:\s*hidden/);
    expect(css).not.toMatch(/\.prq-body\{[^}]*overflow:\s*hidden/);
    expect(form).toMatch(/prq-grid is-product/);
    expect(form).toMatch(/prq-grid is-potential/);
    expect(form).toMatch(/prq-grid is-customer/);
    expect(form).toMatch(/prq-material-label/);
    expect(form).toMatch(/prq-model-label/);
    expect(css).toMatch(/\.prq-progress\{[^}]*min-height:32px/);
    expect(css).toMatch(/\.prq-progress\{[^}]*overflow:visible/);
    expect(css).toMatch(/\.prq-progress\{[^}]*margin-top:0/);
    expect(form).toMatch(/prq-form-page/);
    expect(form).toMatch(/prq-channel/);
    expect(form).toMatch(/prq-note/);
    expect(form).toMatch(/prq-new-customer-name/);
    expect(form).toMatch(/prq-new-customer-pic/);
    expect(form).toMatch(/prq-new-customer-wa/);
    expect(form).toMatch(/prq-new-customer-city/);
  });

  test("list page header sits above workspace tabs without overlap hacks", () => {
    expect(css).toMatch(/\.content:has\(\.prq-list-page\)/);
    expect(css).toMatch(/\.content:has\(\.prq-list-page\) > \.prq-list-page\{[^}]*display:contents/);
    expect(css).toMatch(/\.content:has\(\.prq-list-page\) > \.kb-workspace-tabs/);
    expect(css).toMatch(/\.prq-list-page > \.prq-list-head\{[^}]*grid-row:1/);
    expect(css).toMatch(/\.content:has\(\.prq-list-page\) > \.kb-workspace-tabs,[\s\S]*?grid-row:2/);
    expect(css).toMatch(/grid-template-rows:36px auto minmax\(0,1fr\)/);
    expect(css).toMatch(/\.prq-list-page \.prq-table\{[^}]*table-layout:fixed/);
    expect(css).toMatch(/\.prq-list-page \.prq-table th,[\s\S]*?white-space:normal/);
    expect(css).toMatch(/\.prq-list-page > \.prq-list-body\{[^}]*min-width:0/);
    expect(css).toMatch(/\.prq-list-page \.prq-list-wrap\{[^}]*min-width:0/);
    expect(css).not.toMatch(/\.prq-list-head\{[^}]*transform/);
    expect(css).not.toMatch(/\.prq-list-head\{[^}]*margin-top:\s*-/);
    expect(css).toMatch(/\.prq-list-page \.prq-table th,[\s\S]*?white-space:normal/);
    expect(cssBlock(".prq-list-page .prq-table")).not.toMatch(/white-space:nowrap/);
  });

  test("form page reserves title, workspace tabs, stepper, body, and footer rows", () => {
    expect(css).toMatch(/\.content:has\(\.prq-form-page\)/);
    expect(css).toMatch(/grid-template-rows:36px auto auto minmax\(0,1fr\) auto/);
    expect(css).toMatch(/\.content:has\(\.prq-form-page\) > \.prq-form-page\{[^}]*display:contents/);
    expect(css).toMatch(/\.prq-form-page > \.prq-head\{[^}]*grid-row:1/);
    expect(css).toMatch(/\.content:has\(\.prq-form-page\) > \.kb-workspace-tabs,[\s\S]*?grid-row:2/);
    expect(css).toMatch(/\.content:has\(\.prq-form-page\) > \.kb-workspace-tabs,[\s\S]*?position:static/);
    expect(css).toMatch(/\.prq-form-page > \.prq-progress\{[^}]*grid-row:3/);
    expect(css).toMatch(/\.prq-form-page > \.prq-body\{[^}]*grid-row:4/);
    expect(css).toMatch(/\.prq-form-page > \.prq-actions\{[^}]*grid-row:5/);
    expect(css).toMatch(/\.prq-form-page > \.prq-actions\{[^}]*padding:4px 56px 4px 8px/);
    expect(cssBlock(".prq-form-page > .prq-head")).toMatch(/overflow:visible/);
    expect(cssBlock(".prq-form-page > .prq-progress")).toMatch(/overflow:visible/);
    expect(cssBlock(".prq-form-page > .prq-body")).toMatch(/min-height:\s*0/);
    expect(cssBlock(".prq-form-page > .prq-body")).toMatch(/min-width:\s*0/);
    expect(cssBlock(".prq-form-page > .prq-body")).toMatch(/overflow-y:\s*auto/);
    expect(cssBlock(".prq-form-page > .prq-body")).not.toMatch(/overflow:\s*hidden/);
    expect(cssBlock(".content:has(.prq-page)")).toMatch(/overflow:\s*hidden/);
    expect(css).not.toMatch(/margin\s*:\s*-/);
    expect(css).not.toMatch(/transform\s*:/);
    expect(cssBlock(".prq-actions")).not.toMatch(/position:\s*(fixed|sticky|absolute)/);
    expect(cssBlock(".prq-form-page > .prq-actions")).not.toMatch(/position:\s*(fixed|sticky|absolute)/);
    expect(css).not.toMatch(/\.prq-actions\{[^}]*position:\s*(fixed|sticky|absolute)/);
    expect(form).toMatch(/prq-qty/);
    expect(form).toMatch(/prq-frequency/);
    expect(form).toMatch(/prq-price/);
    expect(form).toMatch(/prq-needed-date/);
    expect(form).toMatch(/prq-channel/);
    expect(form).toMatch(/prq-note/);
    expect(form).toMatch(/prq-save-draft/);
    expect(form).toMatch(/prq-submit/);
  });
});
