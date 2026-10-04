import fs from "fs";
import path from "path";
import { FILTER_DEFAULTS } from "./productionScheduleFilters";
import { applyProductionFilters } from "./productionScheduleFilters";
import { buildProductionScheduleDisplayRows } from "./productionScheduleDisplayRows";
import { productionQueueRank, productionQuickMatch, sortByOpsQueue } from "./mobileOpsQueue";
import {
  PRODUCTION_SUMMARY_TTL_MS,
  clearProductionSummaryCache,
  invalidateProductionSummaryCache,
  loadProductionSummary,
  productionSummaryRequestKey,
  setProductionSummaryClock,
} from "./productionSummaryCache";

const params = {
  include_cancelled: false,
  include_completed: true,
  view: "summary",
};

let now = 0;

beforeEach(() => {
  now = 1_000_000;
  setProductionSummaryClock(() => now);
  clearProductionSummaryCache();
});

afterEach(() => {
  setProductionSummaryClock(null);
  clearProductionSummaryCache();
});

function load(overrides = {}) {
  return loadProductionSummary({
    userKey: "u-driver",
    requestParams: params,
    fetcher: async () => ({ rows: [] }),
    ...overrides,
  });
}

describe("productionSummaryCache", () => {
  test("menu switches inside the TTL share one request and keep the same rows", async () => {
    const fetcher = jest.fn(async () => ({
      rows: [{ id: "po-1", po_number: "PO-1", screen_qty: 4, packing_status: "Berjalan" }],
    }));
    const visits = [];
    for (let i = 0; i < 4; i += 1) {
      visits.push(await load({ fetcher }));
    }
    expect(fetcher).toHaveBeenCalledTimes(1);
    const withinTtl = {
      sequence: ["produksi", "pengiriman", "produksi", "pengiriman"],
      visits: 4,
      requests_without_reuse: 4,
      requests_with_cache: fetcher.mock.calls.length,
    };
    const dir = "/opt/cursor/artifacts";
    fs.mkdirSync(dir, { recursive: true });
    fs.writeFileSync(path.join(dir, "pwa_stage4_summary_requests.json"), JSON.stringify({ within_ttl: withinTtl }, null, 2));
    expect(visits.map((visit) => visit.rows[0].screen_qty)).toEqual([4, 4, 4, 4]);
    expect(visits.map((visit) => visit.rows[0].packing_status)).toEqual([
      "Berjalan", "Berjalan", "Berjalan", "Berjalan",
    ]);
  });

  test("after the TTL the next visit reads the latest rows, including another user's edit", async () => {
    const fetcher = jest.fn()
      .mockResolvedValueOnce({ rows: [{ id: "po-1", screen_qty: 4, packing_status: "Berjalan" }] })
      .mockResolvedValueOnce({ rows: [{ id: "po-1", screen_qty: 80, packing_status: "Sudah" }] });
    const first = await load({ fetcher });
    now += PRODUCTION_SUMMARY_TTL_MS;
    const second = await load({ fetcher });
    expect(fetcher).toHaveBeenCalledTimes(2);
    const dir = "/opt/cursor/artifacts";
    const previous = JSON.parse(fs.readFileSync(path.join(dir, "pwa_stage4_summary_requests.json"), "utf8"));
    previous.after_ttl = {
      visits: 2,
      requests: fetcher.mock.calls.length,
      note: "Second visit starts after PRODUCTION_SUMMARY_TTL_MS. The payload is the newer screen_qty.",
    };
    fs.writeFileSync(path.join(dir, "pwa_stage4_summary_requests.json"), JSON.stringify(previous, null, 2));
    expect(first.rows[0].screen_qty).toBe(4);
    expect(second.rows[0]).toEqual({ id: "po-1", screen_qty: 80, packing_status: "Sudah" });
  });

  test("identical in-flight requests share one fetcher", async () => {
    let resolve;
    const fetcher = jest.fn(() => new Promise((res) => { resolve = res; }));
    const first = load({ fetcher });
    const second = load({ fetcher });
    await Promise.resolve();
    expect(fetcher).toHaveBeenCalledTimes(1);
    resolve({ rows: [{ id: "po-1", screen_qty: 2 }] });
    await expect(first).resolves.toEqual({ rows: [{ id: "po-1", screen_qty: 2 }] });
    await expect(second).resolves.toEqual({ rows: [{ id: "po-1", screen_qty: 2 }] });
  });

  test("a failed request is not reused and the next call fetches again", async () => {
    const fetcher = jest.fn()
      .mockRejectedValueOnce(new Error("summary down"))
      .mockResolvedValueOnce({ rows: [{ id: "po-1", screen_qty: 3 }] });
    await expect(load({ fetcher })).rejects.toThrow("summary down");
    await expect(load({ fetcher })).resolves.toEqual({ rows: [{ id: "po-1", screen_qty: 3 }] });
    expect(fetcher).toHaveBeenCalledTimes(2);
  });

  test("a response that started before invalidation cannot refill the cache", async () => {
    let resolveOld;
    let resolveNew;
    const first = load({
      fetcher: () => new Promise((res) => { resolveOld = res; }),
    });
    invalidateProductionSummaryCache();
    const second = load({
      fetcher: () => new Promise((res) => { resolveNew = res; }),
    });
    await Promise.resolve();
    resolveOld({ rows: [{ id: "old", screen_qty: 1 }] });
    await first;
    resolveNew({ rows: [{ id: "new", screen_qty: 9 }] });
    await second;
    const cached = await load({
      fetcher: async () => { throw new Error("stale cache"); },
    });
    expect(cached.rows[0]).toEqual({ id: "new", screen_qty: 9 });
  });

  test("users and request parameters do not share a result", async () => {
    const fetcher = jest.fn(async () => ({ rows: [{ id: "po-1" }] }));
    await load({ userKey: "operator", fetcher });
    await load({ userKey: "helper", fetcher });
    await load({ userKey: "screener", fetcher });
    await load({ userKey: "driver", fetcher });
    await load({
      userKey: "driver",
      requestParams: { ...params, include_completed: false },
      fetcher,
    });
    expect(fetcher).toHaveBeenCalledTimes(5);
    await load({ userKey: "driver", fetcher });
    expect(fetcher).toHaveBeenCalledTimes(5);
    expect(productionSummaryRequestKey("driver", params)).not.toBe(
      productionSummaryRequestKey("operator", params),
    );
  });

  test("logout clears the summary so the same user fetches again", async () => {
    const fetcher = jest.fn(async () => ({ rows: [{ id: "po-1", screen_qty: 1 }] }));
    await load({ fetcher });
    clearProductionSummaryCache();
    await load({ fetcher });
    expect(fetcher).toHaveBeenCalledTimes(2);
    const auth = fs.readFileSync(path.join(__dirname, "auth.jsx"), "utf8");
    expect(auth).toMatch(/clearProductionSummaryCache\(\)/);
    const cache = fs.readFileSync(path.join(__dirname, "productionSummaryCache.js"), "utf8");
    expect(cache).not.toMatch(/localStorage\.|sessionStorage\.|serviceWorker/);
  });

  test("local filter sort and display keep every dummy row", () => {
    const rows = Array.from({ length: 1000 }, (_, i) => ({
      id: `po-${i}`,
      po_number: `PO-${i}`,
      customer: i % 2 === 0 ? "Alpha" : "Beta",
      cancelled: false,
      current_stage: 8,
      work_date: "2026-10-04",
      deadline: "2026-10-10",
      delivery_deadline_effective: `2026-10-${String((i % 27) + 1).padStart(2, "0")}`,
      urgent: i % 5 === 0,
      operators: [],
      screen_qty: i,
      helper_prepared: i % 10,
      helper_required: 100,
      print_qty_total: 100,
      print_completed_qty: i % 10,
      packing_status: "Berjalan",
    }));
    const samples = [];
    for (let i = 0; i < 5; i += 1) {
      const started = performance.now();
      const filtered = applyProductionFilters(rows, {
        ...FILTER_DEFAULTS,
        q: "",
        includeCancelled: false,
        includeCompleted: true,
      }).filter((row) => productionQuickMatch(row, "semua_aktif"));
      const sorted = sortByOpsQueue(filtered, productionQueueRank);
      const display = buildProductionScheduleDisplayRows(sorted);
      samples.push({
        ms: performance.now() - started,
        filtered: filtered.length,
        sorted: sorted.length,
        display: display.length,
      });
    }
    samples.forEach((sample) => {
      expect(sample.filtered).toBe(1000);
      expect(sample.sorted).toBe(1000);
      expect(sample.display).toBe(1000);
    });
    const median = samples.map((sample) => sample.ms).sort((a, b) => a - b)[2];
    const out = {
      rows: 1000,
      runs_ms: samples.map((sample) => Number(sample.ms.toFixed(3))),
      median_ms: Number(median.toFixed(3)),
      note: "jsdom CPU time for filter, quick match, queue sort, and display-row build. Not a browser paint or phone measurement.",
    };
    const dir = "/opt/cursor/artifacts";
    fs.mkdirSync(dir, { recursive: true });
    fs.writeFileSync(path.join(dir, "pwa_stage4_filter_sort.json"), JSON.stringify(out, null, 2));
  });
});
