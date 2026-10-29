import { useEffect, useMemo, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { Search } from "lucide-react";
import api from "../lib/api";
import { formatApiError } from "../lib/errors";
import { getCanonicalCustomerName } from "../lib/canonicalCustomer";
import { taskMatchesQuery } from "../lib/divisionTasks";
import { MOBILE_LOAD_TIMEOUT_MS, withTimeout } from "../lib/mobileRequest";
import MobileTopBar from "./MobileTopBar";
import MobilePOCard from "./components/MobilePOCard";

export function ordersFromListResponse(payload) {
  if (Array.isArray(payload?.orders)) return payload.orders;
  if (Array.isArray(payload)) return payload;
  return [];
}

export function ordersLoadErrorMessage(err) {
  const status = err?.response?.status;
  if (err?.code === "ETIMEDOUT" || err?.code === "ECONNABORTED") {
    return "Gagal memuat pesanan.";
  }
  const msg = formatApiError(err, "Gagal memuat pesanan.");
  if (status === 403) return msg || "Anda tidak punya akses untuk melihat pesanan.";
  if (status === 401) return msg || "Sesi berakhir. Silakan masuk kembali.";
  return msg || "Gagal memuat pesanan.";
}

export default function MobileOrdersPage() {
  const [params, setParams] = useSearchParams();
  const [orders, setOrders] = useState([]);
  const [listRead, setListRead] = useState(null);
  const [q, setQ] = useState(() => params.get("q") || "");
  const [err, setErr] = useState("");
  const [loading, setLoading] = useState(true);
  const [reloadKey, setReloadKey] = useState(0);

  const onSearchChange = (value) => {
    setQ(value);
    const next = new URLSearchParams(params);
    const trimmed = String(value || "").trim();
    if (trimmed) next.set("q", trimmed);
    else next.delete("q");
    setParams(next, { replace: true });
  };

  useEffect(() => {
    let cancelled = false;
    (async () => {
      setLoading(true);
      setErr("");
      try {
        const payload = await withTimeout(api.listOrders("aktif", { view: "summary" }), MOBILE_LOAD_TIMEOUT_MS);
        if (cancelled) return;
        setOrders(ordersFromListResponse(payload));
        setListRead(payload?.list_read || null);
      } catch (e) {
        if (cancelled) return;
        setOrders([]);
        setListRead(null);
        setErr(ordersLoadErrorMessage(e));
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => { cancelled = true; };
  }, [reloadKey]);

  const rows = useMemo(() => {
    const list = Array.isArray(orders) ? orders : [];
    const s = q.trim().toLowerCase();
    if (!s) return list;
    return list.filter((o) => taskMatchesQuery({
      order_number: o.po_number,
      customer_name: getCanonicalCustomerName(o, ""),
      item: o.item,
    }, q));
  }, [orders, q]);

  return (
    <>
      <MobileTopBar kicker="ETERNAL PACKAGING" title="Pesanan" />
      <main className="mop-content" data-testid="mobile-orders-page">
        <label className="mop-search">
          <Search size={16} />
          <input
            value={q}
            onChange={(e) => onSearchChange(e.target.value)}
            placeholder="Cari nomor PO / customer"
            data-testid="mobile-orders-search"
          />
        </label>
        {loading && (
          <p className="mop-empty" data-testid="mobile-orders-loading">Memuat pesanan...</p>
        )}
        {!loading && !err && listRead?.completeness === "incomplete" && (
          <p className="mop-empty" data-testid="mobile-orders-incomplete" role="status">
            Daftar PO belum lengkap. Batas baca {listRead.read_limit}.
          </p>
        )}
        {!loading && err && (
          <div className="mop-error" data-testid="mobile-orders-error">
            <p>Gagal memuat pesanan.</p>
            {err !== "Gagal memuat pesanan." ? <p className="mop-card-meta">{err}</p> : null}
            <button
              type="button"
              className="mop-btn-primary"
              onClick={() => setReloadKey((n) => n + 1)}
              data-testid="mobile-orders-retry"
            >
              Coba Lagi
            </button>
          </div>
        )}
        {!loading && !err && rows.map((order) => (
          <MobilePOCard key={order.id || order.po_number} order={order} />
        ))}
        {!loading && !err && !rows.length && (
          <p className="mop-empty" data-testid="mobile-orders-empty">Belum ada pesanan.</p>
        )}
      </main>
    </>
  );
}
