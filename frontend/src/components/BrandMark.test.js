import fs from "fs";
import path from "path";

describe("Eternal web branding", () => {
  const mark = fs.readFileSync(path.join(__dirname, "BrandMark.jsx"), "utf8");
  const login = fs.readFileSync(path.join(__dirname, "../pages/Login.jsx"), "utf8");
  const shell = fs.readFileSync(path.join(__dirname, "AppShell.jsx"), "utf8");
  const css = fs.readFileSync(path.join(__dirname, "../App.css"), "utf8");
  const html = fs.readFileSync(path.join(__dirname, "../../public/index.html"), "utf8");
  const brandingDir = path.join(__dirname, "../assets/branding");

  test("login uses the full Eternal logo, not a letter placeholder", () => {
    expect(login).toMatch(/BrandMark stacked/);
    expect(login).toMatch(/login-brand/);
    expect(mark).toMatch(/eternal-logo-full-white/);
    expect(mark).toMatch(/login-brand-logo/);
    expect(mark).not.toMatch(/eternal-mark\.svg/);
    expect(login).not.toMatch(/>P</);
  });

  test("sidebar uses the Eternal symbol, not a letter placeholder", () => {
    expect(shell).toMatch(/BrandMark compact=\{!sidebarExpanded\}/);
    expect(shell).toMatch(/sidebar-brand/);
    expect(mark).toMatch(/eternal-logo-symbol/);
    expect(mark).toMatch(/brand-symbol/);
    expect(mark).not.toMatch(/eternal-mark-on-dark/);
    expect(shell).not.toMatch(/>P</);
  });

  test("brand images keep their aspect ratio", () => {
    expect(css).toMatch(/\.brand-mark img\{[^}]*object-fit:contain/);
    expect(css).toMatch(/\.brand-logo-symbol\{[^}]*aspect-ratio:1 \/ 1/);
    expect(css).toMatch(/\.brand-logo-full\{[^}]*height:auto/);
    expect(css).toMatch(/\.brand-logo-full\{[^}]*background:#FFFFFF/);
    expect(css).toMatch(/\.app-sidebar \.brand-logo-symbol\{[^}]*width:40px/);
  });

  test("favicon uses the Eternal symbol", () => {
    expect(html).toMatch(/eternal-favicon\.png/);
    expect(html).toMatch(/<title>Eternal Packaging<\/title>/);
    expect(fs.existsSync(path.join(__dirname, "../../public/eternal-favicon.png"))).toBe(true);
  });

  test("web branding assets exist and are not imported from mobile at runtime", () => {
    expect(fs.existsSync(path.join(brandingDir, "eternal-logo-full.png"))).toBe(true);
    expect(fs.existsSync(path.join(brandingDir, "eternal-logo-full-white.png"))).toBe(true);
    expect(fs.existsSync(path.join(brandingDir, "eternal-logo-symbol.png"))).toBe(true);
    expect(mark).not.toMatch(/mobile\/assets/);
    expect(login).not.toMatch(/mobile\/assets/);
  });
});
