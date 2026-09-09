import { dedupeInflight } from "./dedupeInflight";

describe("dedupeInflight", () => {
  test("concurrent callers share one in-flight promise (single underlying call)", async () => {
    let calls = 0;
    let resolveOuter;
    const gate = new Promise((resolve) => {
      resolveOuter = resolve;
    });
    const fetchOnce = dedupeInflight(async () => {
      calls += 1;
      await gate;
      return { operators: [{ email: "a@x.com" }] };
    });

    const p1 = fetchOnce();
    const p2 = fetchOnce();
    const p3 = fetchOnce();
    expect(calls).toBe(1);
    expect(p1).toBe(p2);
    expect(p2).toBe(p3);

    resolveOuter();
    const [a, b, c] = await Promise.all([p1, p2, p3]);
    expect(a).toEqual({ operators: [{ email: "a@x.com" }] });
    expect(b).toBe(a);
    expect(c).toBe(a);
    expect(calls).toBe(1);
  });

  test("after settle, a later call starts a new underlying request", async () => {
    let calls = 0;
    const fetchOnce = dedupeInflight(async () => {
      calls += 1;
      return calls;
    });
    await expect(fetchOnce()).resolves.toBe(1);
    await expect(fetchOnce()).resolves.toBe(2);
    expect(calls).toBe(2);
  });
});
