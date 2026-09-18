import fs from "fs";
import path from "path";

const detail = fs.readFileSync(path.join(__dirname, "PODetail.jsx"), "utf8");
const css = fs.readFileSync(path.join(__dirname, "../App.css"), "utf8");
const menu = fs.readFileSync(path.join(__dirname, "../components/poDetail/PoGlobalCommandBar.jsx"), "utf8");

function sliceHeader() {
  const start = detail.indexOf("po-hero-identity");
  const end = detail.indexOf("po-hero-toolbar");
  return detail.slice(start, end);
}

function countTestId(src, id) {
  return (src.match(new RegExp(`data-testid="${id}"`, "g")) || []).length;
}

describe("Detail PO header identity and status badges", () => {
  test("customer sits on the first row; PO number and outlet sit on the second", () => {
    const header = sliceHeader();
    const customer = header.indexOf("detail-customer-name");
    const number = header.indexOf("detail-po-number");
    const outlet = header.indexOf("detail-outlet");
    const meta = header.indexOf("po-hero-identity-meta");
    expect(header).toMatch(/po-hero-customer-row/);
    expect(customer).toBeGreaterThan(-1);
    expect(number).toBeGreaterThan(customer);
    expect(outlet).toBeGreaterThan(number);
    expect(header.indexOf("detail-po-number")).toBeGreaterThan(meta);
    expect(header).toMatch(/po-hero-dot/);
    expect(header).toMatch(/·/);
    expect(countTestId(detail, "detail-po-number")).toBe(1);
    expect(countTestId(detail, "detail-outlet")).toBe(1);
  });

  test("Urgent and Buffer Stock badges render from canonical order fields only when active", () => {
    const header = sliceHeader();
    expect(header).toMatch(/order\.priority === "urgent"/);
    expect(header).toMatch(/data-testid="detail-priority"/);
    expect(header).toMatch(/>URGENT</);
    expect(header).toMatch(/order\.buffer_stock \?/);
    expect(header).toMatch(/data-testid="buffer-stock-badge"/);
    expect(header).toMatch(/>BUFFER STOCK</);
    expect(header.indexOf("detail-priority")).toBeLessThan(header.indexOf("buffer-stock-badge"));
    expect(header.indexOf("buffer-stock-badge")).toBeLessThan(header.indexOf("hold-banner"));
    expect(countTestId(detail, "detail-priority")).toBe(1);
    expect(countTestId(detail, "buffer-stock-badge")).toBe(1);
    expect(detail).not.toMatch(/standard-badge/);
  });

  test("PO HOLD remains a single inline header label without a banner", () => {
    expect(countTestId(detail, "hold-banner")).toBe(1);
    expect(detail).toMatch(/po-hero-hold/);
    expect(detail).not.toMatch(/className="hold-banner"/);
    expect(css).toMatch(/\.po-detail-stage-workspace \.po-hero-hold\{[\s\S]*background:transparent/);
    expect(css).toMatch(/\.po-detail-stage-workspace \.po-hero-hold\{[\s\S]*border:0/);
  });

  test("Set Urgent and Buffer Stock keep original handlers and update from commitOrder", () => {
    expect(detail).toMatch(/const toggleUrgent = \(\) => run\(\(\) => api\.setUrgent\(order\.id, order\.priority !== "urgent"\)/);
    expect(detail).toMatch(/const toggleBufferStock = \(\) => run\(/);
    expect(detail).toMatch(/api\.setBufferStock\(order\.id, !order\.buffer_stock\)/);
    expect(detail).toMatch(/commitOrder\(res\.order\)/);
    expect(detail).toMatch(/canManageUrgentPerm\(hasPerm\)/);
    expect(detail).toMatch(/hasPerm\("buffer_stock\.manage"\)/);
    expect(detail).toMatch(/onToggleUrgent=\{toggleUrgent\}/);
    expect(detail).toMatch(/onToggleBufferStock=\{toggleBufferStock\}/);
    expect(menu).toMatch(/toggle-urgent-button/);
    expect(menu).toMatch(/toggle-buffer-stock-button/);
    expect(menu).toMatch(/closeThen\(onToggleUrgent\)/);
    expect(menu).toMatch(/closeThen\(onToggleBufferStock\)/);
    const runBody = detail.slice(detail.indexOf("const run = async"), detail.indexOf("const approveDesign"));
    expect(runBody).toMatch(/commitOrder\(res\.order\)/);
    expect(runBody).toMatch(/catch/);
    expect(runBody).not.toMatch(/commitOrder\(.*priority/);
    expect(runBody).not.toMatch(/setOrder\(\{[\s\S]*priority/);
  });

  test("opening the page does not mutate urgent or buffer stock", () => {
    const effects = detail.slice(0, detail.indexOf("const toggleUrgent"));
    expect(effects).not.toMatch(/api\.setUrgent/);
    expect(effects).not.toMatch(/api\.setBufferStock/);
    expect(detail).toMatch(/api\.getOrder\(id\)/);
  });

  test("one-screen header CSS keeps badges compact and avoids page overflow", () => {
    expect(css).toMatch(/\.po-detail-stage-workspace \.po-hero-customer-row\{[\s\S]*flex-wrap:nowrap/);
    expect(css).toMatch(/\.po-hero-status-badge\.is-urgent/);
    expect(css).toMatch(/\.po-hero-status-badge\.is-buffer/);
    expect(css).toMatch(/\.app-shell\.po-detail-route\{/);
    expect(css).toMatch(/overflow:hidden/);
  });
});
