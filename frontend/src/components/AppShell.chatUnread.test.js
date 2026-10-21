import fs from "fs";
import path from "path";

const shell = fs.readFileSync(path.join(__dirname, "AppShell.jsx"), "utf8");

function chatUnreadEffectBody() {
  const start = shell.indexOf("api.chatUnreadSummary");
  expect(start).toBeGreaterThan(-1);
  const fromFn = shell.lastIndexOf("useEffect(() => {", start);
  const end = shell.indexOf("useEffect(() => {", start);
  return shell.slice(fromFn, end);
}

describe("AppShell chat unread polling", () => {
  test("loads unread on mount and polls on a fixed interval", () => {
    const body = chatUnreadEffectBody();
    expect(body).toMatch(/api\.chatUnreadSummary\(\)/);
    expect(body).toMatch(/setInterval/);
    expect(body).toMatch(/30000/);
    expect(body).toMatch(/loadUnread\(\)/);
  });

  test("does not refetch merely because the route pathname changed", () => {
    const body = chatUnreadEffectBody();
    expect(body).not.toMatch(/location\.pathname/);
    expect(body).toMatch(/\}, \[\]\);/);
    expect(shell).toMatch(/setActiveSidebarMenu\(null\)[\s\S]*\}, \[location\.pathname, location\.search\]\)/);
  });

  test("pauses periodic polling while the document is hidden and refreshes when visible", () => {
    const body = chatUnreadEffectBody();
    expect(body).toMatch(/visibilitychange/);
    expect(body).toMatch(/visibilityState !== "visible"/);
    expect(body).toMatch(/visibilityState === "visible"/);
    expect(body).toMatch(/startPoll/);
    expect(body).toMatch(/stopPoll/);
  });
});

describe("AppShell health polling", () => {
  test("pauses periodic health probes while the document is hidden", () => {
    const start = shell.indexOf("api.systemHealth");
    expect(start).toBeGreaterThan(-1);
    const fromFn = shell.lastIndexOf("useEffect(() => {", start);
    const end = shell.indexOf("useEffect(() => {", start);
    const body = shell.slice(fromFn, end);
    expect(body).toMatch(/setInterval/);
    expect(body).toMatch(/30000/);
    expect(body).toMatch(/visibilitychange/);
    expect(body).toMatch(/visibilityState !== "visible"/);
  });
});
