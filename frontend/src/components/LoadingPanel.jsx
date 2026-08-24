import { useEffect, useState } from "react";
import { Truck, Check } from "lucide-react";
import api from "../lib/api";
import { formatDate } from "../lib/constants";

/**
 * Loading card — checklist kendaraan/barang/seal.
 * Action Loading berada di sini, bukan di card Surat Jalan atau Terkirim.
 */
export default function LoadingPanel({ order, canManage, busy, onUpdated, footerActions = null }) {
  const lc = order?.loading_checklist || {};
  const [vehiclePlate, setVehiclePlate] = useState(lc.vehicle_plate || "");
  const [driverName, setDriverName] = useState(lc.driver_name || "");
  const [itemsLoaded, setItemsLoaded] = useState(!!lc.items_loaded);
  const [sealOk, setSealOk] = useState(!!lc.seal_ok);
  const [notes, setNotes] = useState(lc.notes || "");
  const [saving, setSaving] = useState(false);
  const [err, setErr] = useState("");

  useEffect(() => {
    const c = order?.loading_checklist || {};
    setVehiclePlate(c.vehicle_plate || "");
    setDriverName(c.driver_name || "");
    setItemsLoaded(!!c.items_loaded);
    setSealOk(!!c.seal_ok);
    setNotes(c.notes || "");
    setErr("");
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [order?.id, order?.loading_checklist?.updated_at]);

  const completed = !!order?.loading_completed;
  const sjReady = !!order?.surat_jalan_created;

  const save = async () => {
    setSaving(true);
    setErr("");
    try {
      const r = await api.updateLoading(order.id, {
        vehicle_plate: vehiclePlate,
        driver_name: driverName,
        items_loaded: itemsLoaded,
        seal_ok: sealOk,
        notes,
      });
      onUpdated?.(r.order);
    } catch (e) {
      const d = e.response?.data?.detail;
      setErr(typeof d === "string" ? d : (d?.message || "Gagal menyimpan checklist Loading"));
    } finally {
      setSaving(false);
    }
  };

  return (
    <section className="panel" data-testid="loading-panel">
      <header className="panel-head">
        <h3><Truck size={14} /> Loading</h3>
        <span className={`pill sm ${completed ? "pill-ok" : ""}`} data-testid="loading-status-badge">
          {completed ? "Loading Selesai" : sjReady ? "Menunggu checklist" : "Menunggu Surat Jalan"}
        </span>
      </header>

      {!sjReady ? (
        <p className="muted small" data-testid="loading-wait-sj-note">
          Checklist Loading dapat diisi setelah Surat Jalan dibuat.
        </p>
      ) : (
        <>
          <div className="prep-grid" data-testid="loading-form">
            <label>Plat kendaraan
              <input
                type="text"
                value={vehiclePlate}
                onChange={(e) => setVehiclePlate(e.target.value)}
                disabled={!canManage || saving || busy}
                data-testid="loading-vehicle-input"
              />
            </label>
            <label>Nama pengantar
              <input
                type="text"
                value={driverName}
                onChange={(e) => setDriverName(e.target.value)}
                disabled={!canManage || saving || busy}
                data-testid="loading-driver-input"
              />
            </label>
            <label className="span-2" style={{ display: "flex", gap: 16, alignItems: "center" }}>
              <span>
                <input
                  type="checkbox"
                  checked={itemsLoaded}
                  onChange={(e) => setItemsLoaded(e.target.checked)}
                  disabled={!canManage || saving || busy}
                  data-testid="loading-items-loaded"
                />{" "}
                Barang sudah dimuat
              </span>
              <span>
                <input
                  type="checkbox"
                  checked={sealOk}
                  onChange={(e) => setSealOk(e.target.checked)}
                  disabled={!canManage || saving || busy}
                  data-testid="loading-seal-ok"
                />{" "}
                Seal OK
              </span>
            </label>
            <label className="span-2">Catatan
              <input
                type="text"
                value={notes}
                onChange={(e) => setNotes(e.target.value)}
                disabled={!canManage || saving || busy}
                data-testid="loading-notes-input"
              />
            </label>
          </div>
          {lc.updated_at && (
            <p className="muted small" data-testid="loading-updated">
              Update terakhir: {lc.updated_by_name || lc.updated_by} · {formatDate(lc.updated_at)}
            </p>
          )}
          {err && <div className="error" data-testid="loading-error">{err}</div>}
          {canManage ? (
            <div className="prep-actions">
              <button className="primary sm" onClick={save} disabled={saving || busy} data-testid="loading-save-button">
                <Check size={13} /> Simpan checklist Loading
              </button>
            </div>
          ) : (
            <p className="muted small" data-testid="loading-readonly-note">
              Butuh permission <code>loading.update</code> untuk mengisi checklist Loading.
            </p>
          )}
        </>
      )}
      {footerActions}
    </section>
  );
}
