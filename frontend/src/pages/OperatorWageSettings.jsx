import { useEffect, useState } from "react";
import api from "../lib/api";

function todayIso() {
  const d = new Date();
  const y = d.getFullYear();
  const m = String(d.getMonth() + 1).padStart(2, "0");
  const day = String(d.getDate()).padStart(2, "0");
  return `${y}-${m}-${day}`;
}

function numOrEmpty(v) {
  return v == null || v === "" ? "" : v;
}

export default function OperatorWageSettings({ notify, canEdit }) {
  const [operators, setOperators] = useState([]);
  const [overrides, setOverrides] = useState([]);
  const [additions, setAdditions] = useState([]);
  const [bonus, setBonus] = useState({
    target_qty: "",
    amount: "",
    mode: "once_per_period",
    effective_from: "",
    active: true,
  });
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  const [q, setQ] = useState("");
  const [items, setItems] = useState([]);
  const [editing, setEditing] = useState("");

  const load = () => {
    setBusy(true);
    Promise.all([
      api.operatorWageSettings(),
      api.masterItems({ page: 1, page_size: 80, q }).catch(() => ({ items: [] })),
    ]).then(([d, m]) => {
      const s = d.settings || {};
      setOperators((d.operators || []).map((o) => ({
        ...o,
        default_rate_per_pcs: numOrEmpty(o.default_rate_per_pcs),
        effective_from: o.effective_from || "",
      })));
      setOverrides((s.overrides || []).map((o) => ({
        ...o,
        rate_per_pcs: numOrEmpty(o.rate_per_pcs),
        rate_type: o.rate_type || "override",
      })));
      setAdditions((s.product_additions || []).map((o) => ({
        ...o,
        additional_rate_per_pcs: numOrEmpty(o.additional_rate_per_pcs),
        effective_from: o.effective_from || "",
      })));
      const b = s.bonus || {};
      setBonus({
        target_qty: numOrEmpty(b.target_qty),
        amount: numOrEmpty(b.amount),
        mode: b.mode || "once_per_period",
        effective_from: b.effective_from || "",
        active: b.active !== false,
      });
      setItems(m.items || []);
    }).catch((e) => setErr(e?.response?.data?.detail || "Gagal memuat upah")).finally(() => setBusy(false));
  };

  useEffect(() => {
    load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const upsertAddition = (item) => {
    const next = [...additions];
    const idx = next.findIndex((o) => (
      (item.accurate_item_id && String(o.accurate_item_id) === String(item.accurate_item_id))
      || (!item.accurate_item_id && String(o.master_item_id) === String(item.id || item.master_item_id))
    ));
    const row = {
      accurate_item_id: item.accurate_item_id || "",
      master_item_id: item.id || item.master_item_id || "",
      item_code: item.item_code || "",
      item_name: item.item_name || "",
      additional_rate_per_pcs: "",
      effective_from: todayIso(),
      active: true,
    };
    if (idx >= 0) next[idx] = { ...next[idx], ...row, additional_rate_per_pcs: next[idx].additional_rate_per_pcs };
    else next.push(row);
    setAdditions(next);
  };

  const payload = () => {
    const bonusFilled = bonus.target_qty !== "" || bonus.amount !== "";
    const body = {
      operators: operators.map((o) => ({
        operator_user_id: o.operator_user_id || null,
        operator_email: o.operator_email || null,
        operator_name: o.operator_name || "",
        default_rate_per_pcs: o.default_rate_per_pcs === "" ? null : Number(o.default_rate_per_pcs),
        effective_from: o.effective_from || null,
        active: true,
      })),
      overrides: overrides.map((o) => ({
        operator_user_id: o.operator_user_id || null,
        operator_email: o.operator_email || null,
        accurate_item_id: o.accurate_item_id || null,
        master_item_id: o.master_item_id || null,
        item_code: o.item_code || "",
        item_name: o.item_name || "",
        rate_per_pcs: o.rate_per_pcs === "" ? null : Number(o.rate_per_pcs),
        rate_type: o.rate_type || "override",
        active: o.active !== false,
      })),
      product_additions: additions.map((o) => ({
        operator_user_id: o.operator_user_id || null,
        operator_email: o.operator_email || null,
        accurate_item_id: o.accurate_item_id || null,
        master_item_id: o.master_item_id || null,
        item_code: o.item_code || "",
        item_name: o.item_name || "",
        additional_rate_per_pcs: o.additional_rate_per_pcs === "" ? null : Number(o.additional_rate_per_pcs),
        effective_from: o.effective_from || null,
        active: o.active !== false,
      })),
    };
    if (bonusFilled) {
      body.bonus = {
        target_qty: bonus.target_qty === "" ? null : Number(bonus.target_qty),
        amount: bonus.amount === "" ? null : Number(bonus.amount),
        mode: bonus.mode || "once_per_period",
        effective_from: bonus.effective_from || null,
        active: bonus.active !== false,
      };
    }
    return body;
  };

  const save = async () => {
    setBusy(true); setErr("");
    try {
      const body = payload();
      if (body.operators.some((o) => o.default_rate_per_pcs != null && o.default_rate_per_pcs < 0)) {
        setErr("Upah dasar / pcs tidak boleh negatif");
        return;
      }
      if (body.bonus) {
        if (!(body.bonus.target_qty > 0)) {
          setErr("Target produksi bonus harus lebih dari 0");
          return;
        }
        if (body.bonus.amount == null || body.bonus.amount < 0) {
          setErr("Nilai bonus tidak boleh negatif");
          return;
        }
      }
      if (body.product_additions.some((o) => o.additional_rate_per_pcs != null && o.additional_rate_per_pcs < 0)) {
        setErr("Tambahan upah produk tidak boleh negatif");
        return;
      }
      await api.updateOperatorWageSettings(body);
      notify?.("Sistem upah operator disimpan");
      setEditing("");
      load();
    } catch (e) {
      setErr(e?.response?.data?.detail || "Gagal menyimpan upah");
    } finally { setBusy(false); }
  };

  return (
    <div data-testid="operator-wage-tab" className="wage-settings">
      <p className="muted small" style={{ marginTop: 0 }}>
        Semua nominal diisi manual. Identitas Operator = user id, identitas produk = ID master/Accurate.
        Perubahan dengan tanggal berlaku baru tidak menimpa payroll periode sebelumnya.
      </p>

      <section className="wage-section" data-testid="wage-base-section">
        <h3>Upah Dasar Operator</h3>
        <div className="wage-table-wrap">
          <table className="wage-table" data-testid="wage-operator-table">
            <thead>
              <tr>
                <th>Nama Operator</th>
                <th className="num">Upah / pcs</th>
                <th>Berlaku Mulai</th>
                <th>Status</th>
                <th>Aksi</th>
              </tr>
            </thead>
            <tbody>
              {operators.map((o, i) => {
                const id = o.operator_user_id || o.operator_email;
                const isEdit = editing === `op-${id}`;
                const unset = o.default_rate_per_pcs === "" || o.default_rate_per_pcs == null;
                return (
                  <tr key={id || i} data-testid={`wage-operator-${i}`}>
                    <td>{o.operator_name || o.operator_email}</td>
                    <td className="num">
                      {isEdit ? (
                        <input
                          type="number"
                          min="0"
                          step="any"
                          value={o.default_rate_per_pcs}
                          disabled={!canEdit}
                          placeholder="Isi manual"
                          onChange={(e) => {
                            const next = [...operators];
                            next[i] = { ...next[i], default_rate_per_pcs: e.target.value };
                            setOperators(next);
                          }}
                          data-testid={`wage-operator-rate-${i}`}
                          style={{ width: 110, textAlign: "right" }}
                        />
                      ) : (
                        <span data-testid={`wage-operator-rate-view-${i}`}>{unset ? "Belum Disetel" : o.default_rate_per_pcs}</span>
                      )}
                    </td>
                    <td>
                      {isEdit ? (
                        <input
                          type="date"
                          value={o.effective_from || ""}
                          disabled={!canEdit}
                          onChange={(e) => {
                            const next = [...operators];
                            next[i] = { ...next[i], effective_from: e.target.value };
                            setOperators(next);
                          }}
                          data-testid={`wage-operator-from-${i}`}
                        />
                      ) : (o.effective_from || "—")}
                    </td>
                    <td data-testid={`wage-operator-status-${i}`}>{unset ? "Belum Disetel" : "Aktif"}</td>
                    <td>
                      <button
                        type="button"
                        className="ghost sm"
                        disabled={!canEdit}
                        onClick={() => setEditing(isEdit ? "" : `op-${id}`)}
                        data-testid={`wage-operator-edit-${i}`}
                      >
                        {isEdit ? "Selesai" : "Edit"}
                      </button>
                    </td>
                  </tr>
                );
              })}
              {!operators.length && !busy && <tr><td colSpan={5} className="empty-state">Belum ada Operator aktif.</td></tr>}
            </tbody>
          </table>
        </div>
      </section>

      <section className="wage-section" data-testid="wage-bonus-section">
        <h3>Bonus Operator</h3>
        <div className="wage-bonus-grid">
          <label>
            <span>Target Produksi / pcs</span>
            <input
              type="number"
              min="1"
              step="any"
              value={bonus.target_qty}
              disabled={!canEdit}
              placeholder="Isi manual"
              onChange={(e) => setBonus({ ...bonus, target_qty: e.target.value })}
              data-testid="wage-bonus-target"
            />
          </label>
          <label>
            <span>Nilai Bonus</span>
            <input
              type="number"
              min="0"
              step="any"
              value={bonus.amount}
              disabled={!canEdit}
              placeholder="Isi manual"
              onChange={(e) => setBonus({ ...bonus, amount: e.target.value })}
              data-testid="wage-bonus-amount"
            />
          </label>
          <label>
            <span>Berlaku Mulai</span>
            <input
              type="date"
              value={bonus.effective_from || ""}
              disabled={!canEdit}
              onChange={(e) => setBonus({ ...bonus, effective_from: e.target.value })}
              data-testid="wage-bonus-from"
            />
          </label>
        </div>
        <fieldset className="wage-bonus-mode" data-testid="wage-bonus-mode">
          <legend>Mode Bonus</legend>
          <label>
            <input
              type="radio"
              name="bonus-mode"
              checked={bonus.mode === "once_per_period"}
              disabled={!canEdit}
              onChange={() => setBonus({ ...bonus, mode: "once_per_period" })}
              data-testid="wage-bonus-mode-once"
            />
            Sekali saat target tercapai
          </label>
          <label>
            <input
              type="radio"
              name="bonus-mode"
              checked={bonus.mode === "per_target_block"}
              disabled={!canEdit}
              onChange={() => setBonus({ ...bonus, mode: "per_target_block" })}
              data-testid="wage-bonus-mode-block"
            />
            Setiap kelipatan target
          </label>
        </fieldset>
        <p className="muted small">Bonus dihitung per Operator dari output terkonfirmasi pada periode laporan. Tanpa isian, bonus = 0.</p>
      </section>

      <section className="wage-section" data-testid="wage-product-section">
        <h3>Tambahan Upah Produk Khusus</h3>
        <p className="muted small">Tambahan ditambahkan di atas upah dasar (bukan mengganti). ID produk, bukan nama.</p>
        <div className="wage-table-wrap">
          <table className="wage-table" data-testid="wage-addition-table">
            <thead>
              <tr>
                <th>Produk</th>
                <th className="num">Tambahan / pcs</th>
                <th>Berlaku Mulai</th>
                <th>Status</th>
                <th>Aksi</th>
              </tr>
            </thead>
            <tbody>
              {additions.map((o, i) => (
                <tr key={`${o.accurate_item_id || o.master_item_id}-${i}`} data-testid={`wage-addition-${i}`}>
                  <td>{o.item_code ? `${o.item_code} · ` : ""}{o.item_name || o.accurate_item_id || o.master_item_id}</td>
                  <td className="num">
                    <input
                      type="number"
                      min="0"
                      step="any"
                      value={o.additional_rate_per_pcs}
                      disabled={!canEdit}
                      placeholder="Isi manual"
                      onChange={(e) => {
                        const next = [...additions];
                        next[i] = { ...next[i], additional_rate_per_pcs: e.target.value };
                        setAdditions(next);
                      }}
                      data-testid={`wage-addition-rate-${i}`}
                      style={{ width: 110, textAlign: "right" }}
                    />
                  </td>
                  <td>
                    <input
                      type="date"
                      value={o.effective_from || ""}
                      disabled={!canEdit}
                      onChange={(e) => {
                        const next = [...additions];
                        next[i] = { ...next[i], effective_from: e.target.value };
                        setAdditions(next);
                      }}
                      data-testid={`wage-addition-from-${i}`}
                    />
                  </td>
                  <td>{o.active === false ? "Nonaktif" : (o.additional_rate_per_pcs === "" || o.additional_rate_per_pcs == null ? "Belum Disetel" : "Aktif")}</td>
                  <td>
                    <button
                      type="button"
                      className="ghost sm"
                      disabled={!canEdit}
                      onClick={() => {
                        const next = [...additions];
                        next[i] = { ...next[i], active: next[i].active === false };
                        setAdditions(next);
                      }}
                      data-testid={`wage-addition-toggle-${i}`}
                    >
                      {o.active === false ? "Aktifkan" : "Nonaktifkan"}
                    </button>
                  </td>
                </tr>
              ))}
              {!additions.length && !busy && <tr><td colSpan={5} className="empty-state">Belum ada tambahan produk.</td></tr>}
            </tbody>
          </table>
        </div>
        <div className="wage-item-search">
          <input placeholder="Cari produk master" value={q} onChange={(e) => setQ(e.target.value)} data-testid="wage-item-search" />
          <button type="button" className="ghost sm" onClick={load} data-testid="wage-item-search-btn">Cari</button>
        </div>
        {!!items.length && canEdit && (
          <ul className="muted small wage-item-results" data-testid="wage-item-results">
            {items.slice(0, 8).map((it) => (
              <li key={it.id || it.accurate_item_id}>
                <span className="mono">{it.item_code}</span> {it.item_name}
                <button type="button" className="ghost sm" onClick={() => upsertAddition(it)} data-testid={`wage-add-item-${it.accurate_item_id || it.id}`}>
                  Tambah
                </button>
              </li>
            ))}
          </ul>
        )}
      </section>

      {!!overrides.length && (
        <section className="wage-section" data-testid="wage-legacy-override-section">
          <h3>Rate Override Lama (pengganti upah)</h3>
          <p className="muted small">Data historis absolut — tidak diubah menjadi tambahan.</p>
          <div className="wage-table-wrap">
            <table className="wage-table">
              <thead><tr><th>Operator</th><th>Produk</th><th className="num">Rate / pcs</th><th>Aktif</th></tr></thead>
              <tbody>
                {overrides.map((o, i) => (
                  <tr key={`${o.operator_user_id}-${o.accurate_item_id || o.master_item_id}-${i}`} data-testid={`wage-override-${i}`}>
                    <td>{operators.find((x) => x.operator_user_id === o.operator_user_id || x.operator_email === o.operator_email)?.operator_name || o.operator_email || "Semua"}</td>
                    <td>{o.item_code ? `${o.item_code} · ` : ""}{o.item_name || o.accurate_item_id}</td>
                    <td className="num">
                      <input
                        type="number"
                        min="0"
                        value={o.rate_per_pcs}
                        disabled={!canEdit}
                        onChange={(e) => {
                          const next = [...overrides];
                          next[i] = { ...next[i], rate_per_pcs: e.target.value };
                          setOverrides(next);
                        }}
                        data-testid={`wage-override-rate-${i}`}
                        style={{ width: 110, textAlign: "right" }}
                      />
                    </td>
                    <td>
                      <input
                        type="checkbox"
                        checked={o.active !== false}
                        disabled={!canEdit}
                        onChange={(e) => {
                          const next = [...overrides];
                          next[i] = { ...next[i], active: e.target.checked };
                          setOverrides(next);
                        }}
                        data-testid={`wage-override-active-${i}`}
                      />
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      )}

      <div className="wage-save-row">
        <button className="primary sm" onClick={save} disabled={!canEdit || busy} data-testid="wage-save">Simpan Pengaturan Upah</button>
        {err && <p className="wage-err">{err}</p>}
        {!canEdit && <p className="muted small">Butuh permission <code>settings.operator_wage.manage</code>.</p>}
      </div>
    </div>
  );
}
