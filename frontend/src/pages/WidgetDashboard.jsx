import { useEffect, useRef, useState } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import WidgetBoard, { useNarrow } from "../components/widgetDashboard/WidgetBoard";
import { TABS, widgetAllowed } from "../components/widgetDashboard/widgetDashboardCatalog";
import { useAuth } from "../lib/auth";
import { jakartaDate, periodRange, shouldApplyDashboardResponse } from "../lib/financialDashboard";
import api from "../lib/api";
import {
  failedModel,
  forbiddenModel,
  formatLongDate,
  formatRange,
  loadingModel,
  mapAging,
  mapAssets,
  mapCash,
  mapCustomerRank,
  mapExpense,
  mapOmzet,
  mapOverdue,
  mapPayables,
  mapProduction,
  mapProducts,
  mapProfit,
  mapPurchases,
  mapReconciliations,
  mapSales,
  mapSchedule,
  mapSettlement,
  mapShipToday,
  mapToday,
  mapTrend,
} from "../lib/widgetDashboardModel";

function findTab(id) {
  return TABS.find((tab) => tab.id === id) || TABS[0];
}

export default function WidgetDashboard() {
  const location = useLocation();
  const navigate = useNavigate();
  const narrow = useNarrow();
  const { user, hasPerm } = useAuth();
  const params = new URLSearchParams(location.search);
  const tab = findTab(params.get("tab"));
  const panel = tab.panels.some((item) => item.id === params.get("panel"))
    ? tab.panels.find((item) => item.id === params.get("panel"))
    : tab.panels[0];
  const requestedWidget = params.get("widget");
  const widgetId = panel.widgets.includes(requestedWidget) ? requestedWidget : panel.widgets[0];
  const preset = ["today", "month", "year", "custom"].includes(params.get("preset")) ? params.get("preset") : "month";
  const today = jakartaDate();
  const ranged = preset === "custom"
    ? { start: params.get("start") || today, end: params.get("end") || today }
    : periodRange(preset);
  const start = ranged.start;
  const end = ranged.end;
  const periodLabel = formatRange(start, end);
  const asOfLabel = formatLongDate(today);
  const ytdStart = `${today.slice(0, 4)}-01-01`;
  const [models, setModels] = useState({});
  const requestRef = useRef(0);

  const go = (next) => {
    const query = new URLSearchParams(location.search);
    Object.entries(next).forEach(([key, value]) => {
      if (value) query.set(key, value);
      else query.delete(key);
    });
    navigate({ pathname: location.pathname, search: `?${query.toString()}` }, { replace: true });
  };

  const allowed = (id) => widgetAllowed(id, user, hasPerm);
  const permissionKey = panel.widgets.map((id) => `${id}:${allowed(id) ? "1" : "0"}`).join("|");
  const loadKey = [tab.id, panel.id, start, end, today, permissionKey].join("~");

  useEffect(() => {
    const requestId = requestRef.current + 1;
    requestRef.current = requestId;
    const next = {};
    panel.widgets.forEach((id) => {
      next[id] = allowed(id) ? loadingModel() : forbiddenModel();
    });
    setModels(next);

    const salesAllowed = allowed("omzet");
    const tasks = [];

    const remember = (run) => {
      tasks.push(run().then((patch) => {
        if (!shouldApplyDashboardResponse(requestId, requestRef.current) || !patch) return;
        setModels((current) => ({ ...current, ...patch }));
      }));
    };

    if (panel.id === "ringkasan" && tab.id === "penjualan" && salesAllowed) {
      remember(async () => {
        try {
          const body = await api.salesDashboard({ start_date: start, end_date: end, preset });
          return {
            omzet: mapOmzet(body.omzet, periodLabel),
            settlement: mapSettlement(body.settlement),
            trend: mapTrend(body.trend),
            sales: mapSales(body.sales),
            products: mapProducts(body.products),
            aging: mapAging(body.receivables, asOfLabel),
          };
        } catch (error) {
          const failed = failedModel(error);
          return { omzet: failed, settlement: failed, trend: failed, sales: failed, products: failed, aging: failed };
        }
      });
    }

    if (panel.id === "pelanggan" && salesAllowed) {
      remember(async () => {
        try {
          const body = await api.salesDashboard({ start_date: start, end_date: end, preset });
          const patch = { customers: mapCustomerRank(body.customer_rank) };
          if (start === today && end === today) patch.today = mapToday(body.omzet, asOfLabel);
          return patch;
        } catch (error) {
          const failed = failedModel(error);
          return start === today && end === today
            ? { customers: failed, today: failed }
            : { customers: failed };
        }
      });
      if (!(start === today && end === today)) {
        remember(async () => {
          try {
            const body = await api.salesDashboard({ start_date: today, end_date: today, preset: "today" });
            return { today: mapToday(body.omzet, asOfLabel) };
          } catch (error) {
            return { today: failedModel(error) };
          }
        });
      }
    }

    if (panel.id === "jadwal") {
      if (allowed("shipToday")) {
        remember(async () => {
          try {
            return { shipToday: mapShipToday(await api.dashboardToday()) };
          } catch (error) {
            return { shipToday: failedModel(error) };
          }
        });
      }
      if (allowed("shipLate") || allowed("shipNext")) {
        remember(async () => {
          try {
            const mapped = mapSchedule(await api.deliverySchedule(), today);
            const patch = {};
            if (allowed("shipLate")) patch.shipLate = mapped.late;
            if (allowed("shipNext")) patch.shipNext = mapped.next;
            return patch;
          } catch (error) {
            const failed = failedModel(error);
            const patch = {};
            if (allowed("shipLate")) patch.shipLate = failed;
            if (allowed("shipNext")) patch.shipNext = failed;
            return patch;
          }
        });
      }
      if (allowed("production")) {
        remember(async () => {
          try {
            return { production: mapProduction(await api.productionDashboard({ view: "summary", include_completed: true }), today) };
          } catch (error) {
            return { production: failedModel(error) };
          }
        });
      }
    }

    if (panel.id === "ringkasan" && tab.id === "keuangan") {
      if (allowed("purchases")) {
        remember(async () => {
          try {
            return { purchases: mapPurchases(await api.purchasePeriodSummary({ start_date: start, end_date: end })) };
          } catch (error) {
            return { purchases: failedModel(error) };
          }
        });
      }
      if (allowed("ap")) {
        remember(async () => {
          try {
            return { ap: mapPayables(await api.accountsPayableSummary()) };
          } catch (error) {
            return { ap: failedModel(error) };
          }
        });
      }
      if (allowed("cash") || allowed("expense")) {
        remember(async () => {
          try {
            const body = await api.financialDashboard({ start_date: start, end_date: end });
            const patch = {};
            if (allowed("cash")) patch.cash = mapCash(body.cash_movement);
            if (allowed("expense")) patch.expense = mapExpense(body.charts?.expense_composition);
            return patch;
          } catch (error) {
            const failed = failedModel(error);
            const patch = {};
            if (allowed("cash")) patch.cash = failed;
            if (allowed("expense")) patch.expense = failed;
            return patch;
          }
        });
      }
      if (allowed("pl")) {
        remember(async () => {
          try {
            const [periodBody, ytdBody] = await Promise.all([
              api.widgetProfitLoss({ start_date: start, end_date: end }),
              api.widgetProfitLoss({ start_date: ytdStart, end_date: today }),
            ]);
            return { pl: mapProfit(periodBody, ytdBody, formatRange(ytdStart, today)) };
          } catch (error) {
            return { pl: failedModel(error) };
          }
        });
      }
      if (allowed("assets")) {
        remember(async () => {
          try {
            return { assets: mapAssets(await api.balanceSheet({ as_of_date: today }), asOfLabel) };
          } catch (error) {
            return { assets: failedModel(error) };
          }
        });
      }
    }

    if (panel.id === "bank") {
      if (allowed("overdue")) {
        remember(async () => {
          try {
            return { overdue: mapOverdue(await api.accountsReceivableOverdueCustomers()) };
          } catch (error) {
            return { overdue: failedModel(error) };
          }
        });
      }
      if (allowed("recon")) {
        remember(async () => {
          try {
            return { recon: mapReconciliations(await api.widgetReconciliations()) };
          } catch (error) {
            return { recon: failedModel(error) };
          }
        });
      }
    }

    return undefined;
    // loadKey already folds panel, period, and permission. `allowed` is a new
    // function every render and must not retrigger this effect.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [loadKey]);

  const periodControl = (
    <>
      <button type="button" className="wp-text-btn" onClick={() => go({ preset: "today", start: today, end: today })}>Hari ini</button>
      <button type="button" className="wp-text-btn" onClick={() => { const range = periodRange("month"); go({ preset: "month", start: range.start, end: range.end }); }}>Bulan ini</button>
      <input
        type="date"
        aria-label="Tanggal awal"
        value={start}
        onChange={(event) => go({ preset: "custom", start: event.target.value, end })}
      />
      <input
        type="date"
        aria-label="Tanggal akhir"
        value={end}
        onChange={(event) => go({ preset: "custom", start, end: event.target.value })}
      />
    </>
  );

  return (
    <WidgetBoard
      tabId={tab.id}
      panelId={panel.id}
      widgetId={widgetId}
      models={models}
      allowed={allowed}
      onNavigate={go}
      periodLabel={periodLabel}
      narrow={narrow}
      periodControl={periodControl}
    />
  );
}
