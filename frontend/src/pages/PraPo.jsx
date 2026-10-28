import { useCallback, useEffect, useMemo, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { useAuth } from "../lib/auth";
import api from "../lib/api";
import { NAV_KEYS } from "../lib/navAccess";

const STATUS_LABEL = {
  draft: "Draft",
  submitted: "Diajukan",
  returned: "Dikembalikan",
  resubmitted: "Diajukan Ulang",
  verified: "Disetujui",
};

function emptyLine() {
  return {
    line_id: `ln-${Math.random().toString(36).slice(2, 10)}`,
    name: "",
    quantity: 1,
    unit: "pcs",
    master_item_id: "",
    accurate_item_id: "",
  };
}

export default function PraPoPage() {
  const { hasPerm, user } = useAuth();
  const navigate = useNavigate();
  const { id: routeId } = useParams();
  const canCreate = hasPerm("pra_po.create");
  const canVerify = hasPerm("pra_po.verify");
  const canReturn = hasPerm("pra_po.return");
  const canViewAll = hasPerm("pra_po.view_all") || canVerify || canReturn;

  const [items, setItems] = useState([]);
  const [queue, setQueue] = useState([]);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [selected, setSelected] = useState(null);
  const [returnReason, setReturnReason] = useState("");
  const [form, setForm] = useState({
    customer: "",
    notes: "",
    line_items: [emptyLine()],
  });

  const reload = useCallback(async () => {
    setBusy(true);
    setError("");
    try {
      const jobs = [api.listPraPo(canViewAll ? {} : { view: "own" })];
      if (canViewAll) jobs.push(api.listPraPo({ view: "queue" }));
      const [mine, q] = await Promise.all(jobs);
      setItems(mine.items || []);
      setQueue(q?.items || []);
    } catch (e) {
      setError(e?.response?.data?.detail || e?.message || "Gagal memuat Pra PO");
    } finally {
      setBusy(false);
    }
  }, [canViewAll]);

  useEffect(() => {
    reload();
  }, [reload]);

  useEffect(() => {
    if (!routeId) return undefined;
    let cancelled = false;
    api.getPraPo(routeId)
      .then((d) => {
        if (!cancelled) setSelected(d.pra_po);
      })
      .catch((e) => {
        if (!cancelled) setError(e?.response?.data?.detail || e?.message || "Gagal membuka Pra PO");
      });
    return () => { cancelled = true; };
  }, [routeId]);

  const openRow = async (row) => {
    try {
      const d = await api.getPraPo(row.id);
      setSelected(d.pra_po);
      setReturnReason("");
      navigate(`/penjualan/pra-po/${row.id}`, { replace: true });
    } catch (e) {
      setError(e?.response?.data?.detail || e?.message || "Gagal membuka");
    }
  };

  const onCreate = async (e) => {
    e.preventDefault();
    setBusy(true);
    setError("");
    try {
      const payload = {
        customer: form.customer,
        notes: form.notes,
        line_items: form.line_items.map((li) => ({
          ...li,
          quantity: Number(li.quantity) || 0,
          master_item_id: li.master_item_id || undefined,
          accurate_item_id: li.accurate_item_id || undefined,
        })),
      };
      const created = await api.createPraPo(payload);
      setForm({ customer: "", notes: "", line_items: [emptyLine()] });
      await reload();
      setSelected(created.pra_po);
      navigate(`/penjualan/pra-po/${created.pra_po.id}`, { replace: true });
    } catch (err) {
      setError(err?.response?.data?.detail || err?.message || "Gagal membuat Pra PO");
    } finally {
      setBusy(false);
    }
  };

  const onSubmit = async () => {
    if (!selected?.id) return;
    setBusy(true);
    try {
      const d = await api.submitPraPo(selected.id);
      setSelected(d.pra_po);
      await reload();
    } catch (err) {
      setError(err?.response?.data?.detail || err?.message || "Gagal mengajukan");
    } finally {
      setBusy(false);
    }
  };

  const onReturn = async () => {
    if (!selected?.id) return;
    setBusy(true);
    try {
      const d = await api.returnPraPo(selected.id, { reason: returnReason });
      setSelected(d.pra_po);
      setReturnReason("");
      await reload();
    } catch (err) {
      setError(err?.response?.data?.detail || err?.message || "Gagal mengembalikan");
    } finally {
      setBusy(false);
    }
  };

  const onVerify = async () => {
    if (!selected?.id) return;
    setBusy(true);
    try {
      const d = await api.verifyPraPo(selected.id);
      setSelected(d.pra_po);
      await reload();
      if (d.order?.id) {
        navigate(`/po/${d.order.id}`);
      }
    } catch (err) {
      setError(err?.response?.data?.detail || err?.message || "Gagal menyetujui");
    } finally {
      setBusy(false);
    }
  };

  const onSaveReturned = async () => {
    if (!selected?.id) return;
    setBusy(true);
    try {
      const d = await api.updatePraPo(selected.id, {
        customer: selected.customer,
        notes: selected.notes || "",
        line_items: selected.line_items || [],
      });
      setSelected(d.pra_po);
      await reload();
    } catch (err) {
      setError(err?.response?.data?.detail || err?.message || "Gagal menyimpan revisi");
    } finally {
      setBusy(false);
    }
  };

  const isOwner = selected && String(selected.created_by_user_id || "") === String(user?.id || user?.user_id || "");
  const canEditSelected = isOwner && (selected?.status === "draft" || selected?.status === "returned");
  const canSubmitSelected = isOwner && (selected?.status === "draft" || selected?.status === "returned");

  const rows = useMemo(() => items, [items]);

  return (
    <div className="page" data-testid="pra-po-page" style={{ padding: 16, display: "grid", gap: 16 }}>
      <header style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline", gap: 12 }}>
        <div>
          <h1 style={{ margin: 0 }}>Pra PO</h1>
          <p className="muted small" style={{ margin: "4px 0 0" }}>
            CRM mengajukan Pra PO. Admin memverifikasi sebelum menjadi Pesanan Penjualan.
          </p>
        </div>
      </header>

      {error ? <p style={{ color: "#8f1f1f" }} data-testid="pra-po-error">{typeof error === "string" ? error : JSON.stringify(error)}</p> : null}
      {busy ? <p className="muted small">Memuat…</p> : null}

      {canCreate ? (
        <form className="settings-card" onSubmit={onCreate} data-testid="pra-po-create-form" style={{ padding: 12, display: "grid", gap: 8, maxWidth: 720 }}>
          <h3 style={{ margin: 0 }}>Buat Draft Pra PO</h3>
          <label>
            Customer
            <input
              required
              value={form.customer}
              onChange={(e) => setForm((f) => ({ ...f, customer: e.target.value }))}
              data-testid="pra-po-customer"
            />
          </label>
          <label>
            Catatan
            <input
              value={form.notes}
              onChange={(e) => setForm((f) => ({ ...f, notes: e.target.value }))}
              data-testid="pra-po-notes"
            />
          </label>
          {(form.line_items || []).map((li, idx) => (
            <div key={li.line_id} style={{ display: "grid", gridTemplateColumns: "2fr 1fr 1fr", gap: 8 }}>
              <input
                required
                placeholder="Nama barang"
                value={li.name}
                onChange={(e) => {
                  const next = [...form.line_items];
                  next[idx] = { ...next[idx], name: e.target.value };
                  setForm((f) => ({ ...f, line_items: next }));
                }}
                data-testid={`pra-po-line-name-${idx}`}
              />
              <input
                type="number"
                min={1}
                required
                value={li.quantity}
                onChange={(e) => {
                  const next = [...form.line_items];
                  next[idx] = { ...next[idx], quantity: e.target.value };
                  setForm((f) => ({ ...f, line_items: next }));
                }}
                data-testid={`pra-po-line-qty-${idx}`}
              />
              <input
                placeholder="master_item_id"
                value={li.master_item_id || ""}
                onChange={(e) => {
                  const next = [...form.line_items];
                  next[idx] = { ...next[idx], master_item_id: e.target.value };
                  setForm((f) => ({ ...f, line_items: next }));
                }}
                data-testid={`pra-po-line-master-${idx}`}
              />
            </div>
          ))}
          <div style={{ display: "flex", gap: 8 }}>
            <button
              type="button"
              className="ghost"
              onClick={() => setForm((f) => ({ ...f, line_items: [...f.line_items, emptyLine()] }))}
            >
              + Baris
            </button>
            <button type="submit" className="primary" disabled={busy} data-testid="pra-po-create-submit">
              Simpan Draft
            </button>
          </div>
        </form>
      ) : null}

      {canViewAll ? (
        <section data-testid="pra-po-queue">
          <h3>Antrean Verifikasi</h3>
          <table className="data-table">
            <thead>
              <tr>
                <th>Nomor</th>
                <th>Customer</th>
                <th>Status</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {queue.length === 0 ? (
                <tr><td colSpan={4} className="muted">Tidak ada pengajuan.</td></tr>
              ) : queue.map((row) => (
                <tr key={row.id}>
                  <td>{row.number}</td>
                  <td>{row.customer}</td>
                  <td>{STATUS_LABEL[row.status] || row.status}</td>
                  <td>
                    <button type="button" className="ghost sm" onClick={() => openRow(row)} data-testid={`pra-po-open-${row.id}`}>
                      Buka
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </section>
      ) : null}

      <section data-testid="pra-po-list">
        <h3>{canViewAll ? "Semua Pra PO" : "Pra PO Saya"}</h3>
        <table className="data-table">
          <thead>
            <tr>
              <th>Nomor</th>
              <th>Customer</th>
              <th>Status</th>
              <th>PO</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {rows.length === 0 ? (
              <tr><td colSpan={5} className="muted">Belum ada Pra PO.</td></tr>
            ) : rows.map((row) => (
              <tr key={row.id}>
                <td>{row.number}</td>
                <td>{row.customer}</td>
                <td>{STATUS_LABEL[row.status] || row.status}</td>
                <td>{row.sales_order_po_number || "—"}</td>
                <td>
                  <button type="button" className="ghost sm" onClick={() => openRow(row)}>
                    Buka
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </section>

      {selected ? (
        <aside
          className="settings-card"
          data-testid="pra-po-detail-drawer"
          style={{ padding: 12, border: "1px solid var(--line)", borderRadius: 10, maxWidth: 720 }}
        >
          <h3 style={{ marginTop: 0 }}>{selected.number}</h3>
          <p><strong>Status:</strong> {STATUS_LABEL[selected.status] || selected.status}</p>
          <p><strong>Customer:</strong> {selected.customer}</p>
          {selected.return_reason ? (
            <p data-testid="pra-po-return-reason" style={{ color: "#8f1f1f" }}>
              Alasan pengembalian: {selected.return_reason}
            </p>
          ) : null}
          {selected.sales_order_id ? (
            <p>
              Pesanan Penjualan:{" "}
              <button type="button" className="linkish" onClick={() => navigate(`/po/${selected.sales_order_id}`)}>
                {selected.sales_order_po_number || selected.sales_order_id}
              </button>
            </p>
          ) : null}

          {canEditSelected ? (
            <div style={{ display: "grid", gap: 8, marginTop: 8 }}>
              <label>
                Customer
                <input
                  value={selected.customer || ""}
                  onChange={(e) => setSelected((s) => ({ ...s, customer: e.target.value }))}
                />
              </label>
              <label>
                Catatan
                <input
                  value={selected.notes || ""}
                  onChange={(e) => setSelected((s) => ({ ...s, notes: e.target.value }))}
                />
              </label>
              <button type="button" className="ghost" onClick={onSaveReturned} disabled={busy} data-testid="pra-po-save-revision">
                Simpan Revisi
              </button>
            </div>
          ) : null}

          <div style={{ display: "flex", flexWrap: "wrap", gap: 8, marginTop: 12 }}>
            {canSubmitSelected ? (
              <button type="button" className="primary" onClick={onSubmit} disabled={busy} data-testid="pra-po-submit">
                {selected.status === "returned" ? "Ajukan Ulang" : "Ajukan"}
              </button>
            ) : null}
            {canVerify && (selected.status === "submitted" || selected.status === "resubmitted") ? (
              <button type="button" className="primary" onClick={onVerify} disabled={busy} data-testid="pra-po-verify">
                Setujui → Pesanan Penjualan
              </button>
            ) : null}
            {canReturn && (selected.status === "submitted" || selected.status === "resubmitted") ? (
              <>
                <input
                  placeholder="Alasan pengembalian (wajib)"
                  value={returnReason}
                  onChange={(e) => setReturnReason(e.target.value)}
                  data-testid="pra-po-return-input"
                  style={{ minWidth: 220 }}
                />
                <button type="button" className="ghost" onClick={onReturn} disabled={busy} data-testid="pra-po-return">
                  Kembalikan
                </button>
              </>
            ) : null}
            <button
              type="button"
              className="ghost"
              onClick={() => {
                setSelected(null);
                navigate("/penjualan/pra-po", { replace: true });
              }}
            >
              Tutup
            </button>
          </div>
        </aside>
      ) : null}
      {/* silence unused NAV_KEYS lint in some builds */}
      <span hidden>{NAV_KEYS.praPo}</span>
    </div>
  );
}
