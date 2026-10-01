import { useEffect, useMemo, useRef, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { ChevronLeft } from "lucide-react";
import api from "../lib/api";
import { formatSalesDate, formatSalesRupiah, formatSalesWibInstant } from "../lib/salesPwa";

const STATUS_FILTERS = [
  "Semua",
  "RISIKO",
  "TERLAMBAT",
  "JATUH TEMPO",
  "WAKTU FOLLOW-UP",
  "TIDAK AKTIF",
  "BELUM WAKTUNYA",
  "DATA BELUM CUKUP",
];

function gapNumber(value) {
  const number = Number(value);
  return Number.isInteger(number) ? String(number) : number.toLocaleString("id-ID");
}

function gapLabel(row) {
  if (row?.median_gap === null || row?.median_gap === undefined) return "Pola order belum cukup";
  const median = Number(row.median_gap);
  const average = row.average_gap === null || row.average_gap === undefined ? null : Number(row.average_gap);
  const status = row?.status || row?.followup_status;
  const count = row?.history_count ?? row?.total_orders;
  const provisional = status === "DATA BELUM CUKUP" || (count !== null && count !== undefined && Number(count) < 3);
  const averageText = average === null ? "" : ` · rata-rata ${gapNumber(average)} hari`;
  if (provisional) return `Jarak order sementara: ${gapNumber(median)} hari${averageText}`;
  const pattern = row.pattern && row.pattern !== "DATA BELUM CUKUP" ? row.pattern : "";
  return `Biasanya setiap ${gapNumber(median)} hari${averageText}${pattern ? ` · ${pattern}` : ""}`;
}

function nextLabel(row) {
  if (!row?.prediction_center && !row?.prediction_start) return "Perkiraan belum tersedia";
  const center = formatSalesDate(row.prediction_center);
  const start = formatSalesDate(row.prediction_start);
  const end = formatSalesDate(row.prediction_end);
  if (row.prediction_start && row.prediction_end) return `Perkiraan ${center} (${start}–${end})`;
  return `Perkiraan ${center}`;
}

function valueText(status, amount) {
  if (status === "complete") return formatSalesRupiah(amount) || "—";
  return "—";
}

function PredictionFacts({ row }) {
  return (
    <>
      <p>{row.reason || row.evidence}</p>
      <p>Terakhir order {formatSalesDate(row.last_order_date)}</p>
      <p>{gapLabel(row)}</p>
      <p>{nextLabel(row)}</p>
      <p className={row.status === "RISIKO" || row.status === "TERLAMBAT" ? "spwa-pred-status warn" : "spwa-pred-status"}>
        {row.status || row.followup_status}
        {row.days_late ? ` · lewat ${row.days_late} hari` : ""}
      </p>
    </>
  );
}

export default function SalesPwaPredictionsPage() {
  const { customerKey } = useParams();
  const [data, setData] = useState(null);
  const [phase, setPhase] = useState("loading");
  const [err, setErr] = useState("");
  const [statusFilter, setStatusFilter] = useState("Semua");
  const [outletId, setOutletId] = useState("");
  const [scheduleAt, setScheduleAt] = useState("");
  const [confirmReplace, setConfirmReplace] = useState(false);
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState("");
  const savingRef = useRef(false);

  useEffect(() => {
    let cancelled = false;
    setPhase("loading");
    setData(null);
    setErr("");
    setNotice("");
    const req = customerKey
      ? api.salesPwaPredictionDetail(customerKey)
      : api.salesPwaPredictions();
    req
      .then((res) => {
        if (cancelled) return;
        setData(res);
        const empty = customerKey ? !res?.analysis : !(res?.rows || []).length;
        setPhase(empty ? "empty" : "loaded");
      })
      .catch((e) => {
        if (cancelled) return;
        setErr(e.response?.status === 404 ? "Prediksi tidak ditemukan" : (e.response?.data?.detail || e.message));
        setPhase("error");
      });
    return () => { cancelled = true; };
  }, [customerKey]);

  const rows = useMemo(() => data?.rows || [], [data]);
  const visible = useMemo(() => {
    if (statusFilter === "Semua") return rows;
    return rows.filter((row) => row.status === statusFilter);
  }, [rows, statusFilter]);
  const analysis = data?.analysis;
  const agenda = data?.agenda;
  const outlets = useMemo(() => agenda?.outlets || [], [agenda]);
  const selected = outlets.find((outlet) => outlet.outlet_id === outletId) || (outlets.length === 1 ? outlets[0] : null);
  const needsConfirm = Boolean(selected?.has_active_agenda);

  useEffect(() => {
    if (outlets.length === 1) setOutletId(outlets[0].outlet_id);
  }, [outlets]);

  const saveAgenda = async (event) => {
    event.preventDefault();
    if (!selected?.outlet_id || !scheduleAt || busy || savingRef.current) return;
    if (needsConfirm && !confirmReplace) return;
    const outletIdSaved = selected.outlet_id;
    savingRef.current = true;
    setBusy(true);
    setErr("");
    try {
      const saved = await api.scheduleSalesPwaVisit({
        outlet_id: outletIdSaved,
        next_follow_up_at: scheduleAt,
      });
      const storedAt = saved?.next_follow_up_at || saved?.at || scheduleAt;
      setData((current) => {
        if (!current?.agenda) return current;
        const outlets = (current.agenda.outlets || []).map((outlet) => (
          outlet.outlet_id === outletIdSaved
            ? { ...outlet, has_active_agenda: true, next_follow_up_at: storedAt }
            : outlet
        ));
        return { ...current, agenda: { ...current.agenda, linked: true, identity: "stable", outlets } };
      });
      setNotice("Ditambahkan ke Agenda");
      setScheduleAt("");
      setConfirmReplace(false);
    } catch (e) {
      setErr(e.response?.data?.detail || e.message || "Agenda gagal disimpan");
    } finally {
      savingRef.current = false;
      setBusy(false);
    }
  };

  return (
    <>
      <header className="spwa-top">
        {customerKey ? <Link to="/sales/prediksi" className="spwa-back"><ChevronLeft size={14} /> Prediksi</Link> : null}
        <p className="spwa-kicker">PWA SALES</p>
        <h1>Prediksi Order</h1>
      </header>
      <main className="spwa-main" data-testid="sales-pwa-predictions">
        {phase === "loading" && <p className="spwa-muted" data-testid="sales-pred-loading">Memuat prediksi...</p>}
        {phase === "error" && <div className="spwa-err" data-testid="sales-pred-error">{err}</div>}
        {phase === "empty" && <p className="spwa-empty" data-testid="sales-pred-empty">Tidak ada prediksi pada PO Anda.</p>}
        {notice && <p className="spwa-pred-notice" data-testid="sales-pred-notice">{notice}</p>}

        {phase === "loaded" && !customerKey && (
          <>
            <div className="spwa-pred-filters" data-testid="sales-pred-filters">
              {STATUS_FILTERS.filter((status) => status === "Semua" || rows.some((row) => row.status === status)).map((status) => (
                <button
                  key={status}
                  type="button"
                  className={statusFilter === status ? "is-on" : ""}
                  onClick={() => setStatusFilter(status)}
                >
                  {status === "Semua" ? "Semua" : status}
                </button>
              ))}
            </div>
            {visible.map((row) => (
              <Link
                key={row.customer_key}
                className="spwa-pred-row"
                to={`/sales/prediksi/${encodeURIComponent(row.customer_key)}`}
                data-testid={`sales-pred-row-${row.customer_key}`}
              >
                <strong>{row.customer_name || row.customer}</strong>
                <PredictionFacts row={row} />
              </Link>
            ))}
            {!visible.length && <p className="spwa-empty">Tidak ada prediksi pada status ini.</p>}
          </>
        )}

        {phase === "loaded" && analysis && (
          <article className="spwa-pred-detail" data-testid="sales-prediction-detail">
            <h2>{analysis.customer_name || analysis.customer}</h2>
            <PredictionFacts row={analysis} />
            <p className="spwa-pred-value" data-testid="sales-pred-value">
              Nilai historis {valueText(data?.value?.value_status, data?.value?.historical_order_value)}
              {data?.value?.value_status === "complete" && data?.value?.average_order_value !== null && data?.value?.average_order_value !== undefined
                ? ` · rata-rata ${formatSalesRupiah(data.value.average_order_value)}`
                : ""}
            </p>
            {data?.value?.value_status !== "complete" && (
              <p data-testid="sales-pred-value-gap">Data harga tidak lengkap</p>
            )}

            <h3>Produk yang biasa dibeli</h3>
            <div data-testid="sales-pred-products">
              {(data?.products || []).length === 0 && <p className="spwa-empty compact">Belum ada produk pada PO ini.</p>}
              {(data?.products || []).map((product) => (
                <p key={product.product_key}>
                  <strong>{product.name}</strong>
                  {product.unit ? ` · ${product.unit}` : ""}
                  {` · ${product.purchase_count} order`}
                  {product.quantity_complete && product.total_quantity !== null
                    ? ` · ${Number(product.total_quantity).toLocaleString("id-ID")}`
                    : " · qty tidak lengkap"}
                  {product.last_purchase_date ? ` · terakhir ${formatSalesDate(product.last_purchase_date)}` : ""}
                </p>
              ))}
            </div>

            <h3>Riwayat order</h3>
            <div data-testid="sales-pred-history">
              {(data?.history || []).length === 0 && <p className="spwa-empty compact">Belum ada riwayat order.</p>}
              {(data?.history || []).map((item) => (
                <p key={item.id || item.po_number}>
                  <strong>{formatSalesDate(item.order_date)}</strong>
                  {` · ${item.po_number || "Tanpa nomor"}`}
                  {item.outlet ? ` · ${item.outlet}` : ""}
                  {item.gap_days === null || item.gap_days === undefined ? "" : ` · jeda ${item.gap_days} hari`}
                  {item.value_status === "complete" ? ` · ${formatSalesRupiah(item.order_value)}` : " · —"}
                  {item.notes ? ` · ${item.notes}` : ""}
                </p>
              ))}
            </div>

            <h3>Agenda</h3>
            {!agenda?.linked && (agenda?.identity === "name" || agenda?.identity === "ambiguous") && (
              <p data-testid="sales-pred-identity">Customer belum memiliki identitas yang cukup untuk dihubungkan ke agenda.</p>
            )}
            {!agenda?.linked && agenda?.identity !== "name" && agenda?.identity !== "ambiguous" && (
              <p data-testid="sales-pred-unlinked">Customer belum terhubung ke outlet.</p>
            )}
            {agenda?.linked && (
              <form className="spwa-pred-agenda" data-testid="sales-pred-agenda" onSubmit={saveAgenda}>
                {outlets.length > 1 && (
                  <fieldset>
                    <legend>Pilih outlet</legend>
                    {outlets.map((outlet) => (
                      <label key={outlet.outlet_id} data-testid={`sales-pred-outlet-${outlet.outlet_id}`}>
                        <input
                          type="radio"
                          name="pred-outlet"
                          value={outlet.outlet_id}
                          checked={outletId === outlet.outlet_id}
                          onChange={() => { setOutletId(outlet.outlet_id); setConfirmReplace(false); }}
                        />
                        {outlet.name}
                      </label>
                    ))}
                  </fieldset>
                )}
                {outlets.length === 1 && <p>{outlets[0].name}</p>}
                {needsConfirm && (
                  <p data-testid="sales-pred-agenda-existing">
                    Outlet ini sudah memiliki agenda aktif. {formatSalesWibInstant(selected.next_follow_up_at)}
                  </p>
                )}
                <label>
                  Tanggal dan waktu
                  <input type="datetime-local" value={scheduleAt} onChange={(event) => setScheduleAt(event.target.value)} required />
                </label>
                {needsConfirm && (
                  <label>
                    <input data-testid="sales-pred-agenda-confirm" type="checkbox" checked={confirmReplace} onChange={(event) => setConfirmReplace(event.target.checked)} />
                    Ganti agenda yang ada
                  </label>
                )}
                <button className="spwa-primary" type="submit" data-testid="sales-pred-agenda-submit" disabled={busy || !selected || (needsConfirm && !confirmReplace)}>
                  Tambahkan ke Agenda
                </button>
              </form>
            )}
          </article>
        )}
      </main>
    </>
  );
}
