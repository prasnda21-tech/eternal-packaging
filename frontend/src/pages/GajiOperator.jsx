import { useCallback, useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { Download, RefreshCw, Settings2 } from "lucide-react";
import api from "../lib/api";
import { useAuth } from "../lib/auth";
import { formatDisplayDate } from "../lib/constants";
import { NAV_KEYS } from "../lib/navAccess";

function todayIso() {
  const d = new Date();
  const y = d.getFullYear();
  const m = String(d.getMonth() + 1).padStart(2, "0");
  const day = String(d.getDate()).padStart(2, "0");
  return `${y}-${m}-${day}`;
}

function monthStartIso() {
  const d = new Date();
  const y = d.getFullYear();
  const m = String(d.getMonth() + 1).padStart(2, "0");
  return `${y}-${m}-01`;
}

function fmtQty(n) {
  return Number(n || 0).toLocaleString("id-ID");
}

function fmtMoney(n) {
  if (n === "" || n == null) return "Belum disetel";
  return Number(n || 0).toLocaleString("id-ID");
}

function operatorKey(o) {
  return o.operator_user_id || o.id || o.email || "";
}

export default function GajiOperator() {
  const { hasPerm } = useAuth();
  const [from, setFrom] = useState(monthStartIso());
  const [to, setTo] = useState(todayIso());
  const [selectedOps, setSelectedOps] = useState([]);
  const [item, setItem] = useState("");
  const [data, setData] = useState(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  const canWage = hasPerm("settings.operator_wage.view") || hasPerm("settings.operator_wage.manage") || hasPerm(NAV_KEYS.settings);

  const params = useMemo(() => ({
    from,
    to,
    operator_ids: selectedOps.length ? selectedOps.join(",") : undefined,
    item: item || undefined,
  }), [from, to, selectedOps, item]);

  const load = useCallback(async () => {
    setBusy(true);
    setErr("");
    try {
      const d = await api.gajiOperator(params);
      setData(d);
    } catch (e) {
      const det = e.response?.data?.detail;
      setErr(typeof det === "object" ? det.message : (det || "Gagal memuat Gaji Operator"));
    } finally {
      setBusy(false);
    }
  }, [params]);

  useEffect(() => { load(); }, [load]);

  const exportFile = async () => {
    try {
      const blob = await api.gajiOperatorExport({ ...params, fmt: "xlsx" });
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = `Gaji_Operator_${from}_${to}.xlsx`;
      a.click();
      URL.revokeObjectURL(url);
    } catch (e) {
      const det = e.response?.data?.detail;
      setErr(typeof det === "object" ? det.message : (det || "Gagal export"));
    }
  };

  const rows = data?.rows || [];
  const operators = data?.filters?.operators || [];
  const items = data?.filters?.items || [];
  const allIds = operators.map(operatorKey).filter(Boolean);
  const allSelected = allIds.length > 0 && allIds.every((id) => selectedOps.includes(id));
  const noneSelected = selectedOps.length === 0;

  const toggleAll = () => {
    if (allSelected && !noneSelected) setSelectedOps([]);
    else setSelectedOps(allIds);
  };

  const toggleOp = (id) => {
    setSelectedOps((prev) => (prev.includes(id) ? prev.filter((x) => x !== id) : [...prev, id]));
  };

  return (
    <div className="ship-status-page" data-testid="gaji-operator-page">
      <div className="ship-status-chrome">
        <header className="ship-status-heading">
          <h1>Gaji Operator</h1>
        </header>
        <div className="ship-status-toolbar" data-testid="gaji-filter-bar">
          <label className="ship-filter-field">
            <span>Tanggal Mulai</span>
            <input type="date" value={from} onChange={(e) => setFrom(e.target.value)} data-testid="gaji-filter-from" />
          </label>
          <label className="ship-filter-field">
            <span>Tanggal Selesai</span>
            <input type="date" value={to} onChange={(e) => setTo(e.target.value)} data-testid="gaji-filter-to" />
          </label>
          <fieldset className="ship-filter-field gaji-op-filter" data-testid="gaji-filter-operator">
            <legend>Operator</legend>
            <label className="gaji-op-check">
              <input
                type="checkbox"
                checked={noneSelected || allSelected}
                onChange={toggleAll}
                data-testid="gaji-filter-operator-all"
              />
              Pilih Semua
            </label>
            {operators.map((o) => {
              const id = operatorKey(o);
              return (
                <label key={id} className="gaji-op-check">
                  <input
                    type="checkbox"
                    checked={noneSelected || selectedOps.includes(id)}
                    onChange={() => {
                      if (noneSelected) {
                        setSelectedOps(allIds.filter((x) => x !== id));
                      } else {
                        toggleOp(id);
                      }
                    }}
                    data-testid={`gaji-filter-operator-${id}`}
                  />
                  {o.name || o.email || id}
                </label>
              );
            })}
            {!!selectedOps.length && (
              <button type="button" className="ghost sm" onClick={() => setSelectedOps([])} data-testid="gaji-filter-operator-clear">
                Reset
              </button>
            )}
          </fieldset>
          <label className="ship-filter-field">
            <span>Produk / Item</span>
            <select value={item} onChange={(e) => setItem(e.target.value)} data-testid="gaji-filter-item">
              <option value="">Semua Produk</option>
              {items.map((it) => (
                <option key={it.accurate_item_id || it.master_item_id} value={it.item_code || it.accurate_item_id || it.item_name}>
                  {it.item_code ? `${it.item_code} · ` : ""}{it.item_name}
                </option>
              ))}
            </select>
          </label>
          <button type="button" className="ship-icon-btn" onClick={load} title="Muat ulang" data-testid="gaji-refresh" disabled={busy}>
            <RefreshCw size={14} className={busy ? "spin" : ""} />
          </button>
          <button type="button" className="ghost sm" onClick={exportFile} data-testid="gaji-export">
            <Download size={14} /> Export
          </button>
          {canWage && (
            <Link to="/settings?tab=operator-wage" className="ghost sm" data-testid="gaji-open-wage-settings">
              <Settings2 size={14} /> Pengaturan Upah
            </Link>
          )}
        </div>
        {err && <div className="error" data-testid="gaji-error">{err}</div>}
      </div>
      <div className="ship-status-grid" data-testid="gaji-table">
        <table className="ship-table">
          <thead>
            <tr>
              <th>Nama Operator</th>
              <th>Kode Item</th>
              <th>Nama Produk</th>
              <th className="num">Output</th>
              <th>Periode / Waktu</th>
              <th className="num">Rate / pcs</th>
              <th className="num">Total Upah</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r, i) => (
              <tr key={`${r.order_id}-${r.operator_id}-${r.tanggal}-${r.line_id}-${i}`} data-testid={`gaji-row-${i}`}>
                <td>{r.operator || r.employee_name}</td>
                <td className="mono">{r.item_code || "-"}</td>
                <td>{r.item_name || r.cup_item || "-"}</td>
                <td className="num mono">{fmtQty(r.qty_completed)}</td>
                <td className="mono">{formatDisplayDate(r.tanggal || r.date)}</td>
                <td className="num mono">{r.rate_unset ? "Belum disetel" : fmtMoney(r.rate_per_pcs)}</td>
                <td className="num mono">{fmtMoney(r.total_upah)}</td>
              </tr>
            ))}
            {!rows.length && !busy && (
              <tr><td colSpan={7} className="empty-state">Tidak ada output Operator pada periode ini.</td></tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}
