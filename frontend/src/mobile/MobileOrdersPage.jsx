import { useEffect, useMemo, useState } from "react";
import { Search } from "lucide-react";
import api from "../lib/api";
import { taskMatchesQuery } from "../lib/divisionTasks";
import MobileTopBar from "./MobileTopBar";
import MobilePOCard from "./components/MobilePOCard";

export default function MobileOrdersPage() {
  const [orders, setOrders] = useState([]);
  const [q, setQ] = useState("");
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    let alive = true;
    (async () => {
      setBusy(true);
      try {
        const d = await api.listOrders("aktif");
        const list = Array.isArray(d?.orders) ? d.orders : (Array.isArray(d) ? d : []);
        if (alive) setOrders(list);
      } catch (e) {
        if (alive) setErr(e.response?.data?.detail || "Gagal memuat PO.");
      } finally {
        if (alive) setBusy(false);
      }
    })();
    return () => { alive = false; };
  }, []);

  const rows = useMemo(() => {
    const list = Array.isArray(orders) ? orders : [];
    const s = q.trim().toLowerCase();
    if (!s) return list;
    return list.filter((o) => taskMatchesQuery({
      order_number: o.po_number,
      customer_name: o.customer_name || o.outlet,
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
            onChange={(e) => setQ(e.target.value)}
            placeholder="Cari nomor PO / customer"
            data-testid="mobile-orders-search"
          />
        </label>
        {busy && !rows.length && <p className="mop-empty">Memuat pesanan...</p>}
        {err && <div className="mop-error">{err} Coba lagi.</div>}
        {rows.map((order) => <MobilePOCard key={order.id} order={order} />)}
        {!busy && !rows.length && !err && <p className="mop-empty">Tidak ada pesanan.</p>}
      </main>
    </>
  );
}
