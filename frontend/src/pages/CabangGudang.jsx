import { useCallback, useEffect, useMemo, useState } from "react";
import { AlertCircle, Building2, Pencil, Plus, Warehouse } from "lucide-react";
import api from "../lib/api";
import { useAuth } from "../lib/auth";

const errText = (ex) => {
  const det = ex?.response?.data?.detail;
  if (typeof det === "string") return det;
  if (Array.isArray(det) && det[0]?.msg) return det.map((d) => d.msg).join("; ");
  if (det && typeof det === "object" && det.message) return det.message;
  return ex?.message || "Permintaan gagal";
};

export default function CabangGudang() {
  const { hasPerm } = useAuth();
  const canManageBranch = hasPerm("org.branches.manage");
  const canManageWarehouse = hasPerm("org.warehouses.manage");
  const canViewWarehouse = hasPerm("org.warehouses.view") || canManageWarehouse;

  const [branches, setBranches] = useState([]);
  const [warehouses, setWarehouses] = useState([]);
  const [selectedBranchId, setSelectedBranchId] = useState("");
  const [loadingBranches, setLoadingBranches] = useState(true);
  const [loadingWarehouses, setLoadingWarehouses] = useState(false);
  const [loadError, setLoadError] = useState("");
  const [actionError, setActionError] = useState("");

  const [branchFormOpen, setBranchFormOpen] = useState(false);
  const [editingBranch, setEditingBranch] = useState(null);
  const [branchCode, setBranchCode] = useState("");
  const [branchName, setBranchName] = useState("");
  const [branchActive, setBranchActive] = useState(true);
  const [savingBranch, setSavingBranch] = useState(false);

  const [whFormOpen, setWhFormOpen] = useState(false);
  const [editingWarehouse, setEditingWarehouse] = useState(null);
  const [whCode, setWhCode] = useState("");
  const [whName, setWhName] = useState("");
  const [whActive, setWhActive] = useState(true);
  const [savingWarehouse, setSavingWarehouse] = useState(false);

  const selectedBranch = useMemo(
    () => branches.find((b) => b.id === selectedBranchId) || null,
    [branches, selectedBranchId],
  );

  const loadBranches = useCallback(async () => {
    setLoadingBranches(true);
    setLoadError("");
    try {
      const data = await api.branches();
      const items = Array.isArray(data?.items) ? data.items : [];
      setBranches(items);
      setSelectedBranchId((prev) => {
        if (prev && items.some((b) => b.id === prev)) return prev;
        return items[0]?.id || "";
      });
    } catch (ex) {
      setLoadError(errText(ex));
      setBranches([]);
      setSelectedBranchId("");
    } finally {
      setLoadingBranches(false);
    }
  }, []);

  const loadWarehouses = useCallback(async (branchId) => {
    if (!branchId || !canViewWarehouse) {
      setWarehouses([]);
      return;
    }
    setLoadingWarehouses(true);
    setActionError("");
    try {
      const data = await api.warehouses({ branch_id: branchId });
      setWarehouses(Array.isArray(data?.items) ? data.items : []);
    } catch (ex) {
      setActionError(errText(ex));
      setWarehouses([]);
    } finally {
      setLoadingWarehouses(false);
    }
  }, [canViewWarehouse]);

  useEffect(() => { loadBranches(); }, [loadBranches]);

  useEffect(() => {
    if (selectedBranchId) loadWarehouses(selectedBranchId);
    else setWarehouses([]);
  }, [selectedBranchId, loadWarehouses]);

  const openCreateBranch = () => {
    setEditingBranch(null);
    setBranchCode("");
    setBranchName("");
    setBranchActive(true);
    setActionError("");
    setBranchFormOpen(true);
  };

  const openEditBranch = (row) => {
    setEditingBranch(row);
    setBranchCode(String(row?.code || ""));
    setBranchName(String(row?.name || ""));
    setBranchActive(row?.active !== false);
    setActionError("");
    setBranchFormOpen(true);
  };

  const submitBranch = async (e) => {
    e?.preventDefault?.();
    if (!canManageBranch || savingBranch) return;
    setSavingBranch(true);
    setActionError("");
    try {
      if (editingBranch) {
        await api.updateBranch(editingBranch.id, {
          code: branchCode,
          name: branchName,
          active: branchActive,
        });
      } else {
        await api.createBranch({
          code: branchCode,
          name: branchName,
          active: branchActive,
        });
      }
      setBranchFormOpen(false);
      setEditingBranch(null);
      await loadBranches();
    } catch (ex) {
      setActionError(errText(ex));
    } finally {
      setSavingBranch(false);
    }
  };

  const openCreateWarehouse = () => {
    if (!selectedBranchId) return;
    setEditingWarehouse(null);
    setWhCode("");
    setWhName("");
    setWhActive(true);
    setActionError("");
    setWhFormOpen(true);
  };

  const openEditWarehouse = (row) => {
    setEditingWarehouse(row);
    setWhCode(String(row?.code || ""));
    setWhName(String(row?.name || ""));
    setWhActive(row?.active !== false);
    setActionError("");
    setWhFormOpen(true);
  };

  const submitWarehouse = async (e) => {
    e?.preventDefault?.();
    if (!canManageWarehouse || savingWarehouse || !selectedBranchId) return;
    setSavingWarehouse(true);
    setActionError("");
    try {
      if (editingWarehouse) {
        await api.updateWarehouse(editingWarehouse.id, {
          code: whCode,
          name: whName,
          active: whActive,
        });
      } else {
        await api.createWarehouse({
          branch_id: selectedBranchId,
          code: whCode,
          name: whName,
          active: whActive,
        });
      }
      setWhFormOpen(false);
      setEditingWarehouse(null);
      await loadWarehouses(selectedBranchId);
    } catch (ex) {
      setActionError(errText(ex));
    } finally {
      setSavingWarehouse(false);
    }
  };

  const toggleWarehouseActive = async (row) => {
    if (!canManageWarehouse || !row?.id) return;
    setActionError("");
    try {
      await api.updateWarehouse(row.id, { active: !row.active });
      await loadWarehouses(selectedBranchId);
    } catch (ex) {
      setActionError(errText(ex));
    }
  };

  return (
    <div className="prod-page" data-testid="cabang-gudang-page">
      <div className="page-heading prod-heading">
        <div className="prod-heading-copy">
          <p className="eyebrow">Persediaan</p>
          <h1 data-testid="cabang-gudang-title">Cabang & Gudang</h1>
          <p className="subhead">Master organisasi cabang dan gudang fisik</p>
        </div>
        {canManageBranch && (
          <button type="button" className="primary" onClick={openCreateBranch} data-testid="cabang-add">
            <Plus size={14} /> Tambah Cabang
          </button>
        )}
      </div>

      {loadError && (
        <div className="signal-strip" role="alert" data-testid="cabang-gudang-error">
          <div><AlertCircle size={16} /><span>{loadError}</span></div>
        </div>
      )}
      {actionError && (
        <div className="signal-strip" role="alert" data-testid="cabang-gudang-action-error">
          <div><AlertCircle size={16} /><span>{actionError}</span></div>
        </div>
      )}

      <div className="prod-split" style={{ display: "grid", gridTemplateColumns: "minmax(240px, 320px) 1fr", gap: 16 }}>
        <section className="prod-panel" data-testid="cabang-list-panel">
          <div className="prod-panel-head" style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 12 }}>
            <Building2 size={16} />
            <strong>Cabang</strong>
          </div>
          {loadingBranches ? (
            <p className="muted small">Memuat cabang…</p>
          ) : branches.length === 0 ? (
            <p className="muted small" data-testid="cabang-empty">Belum ada cabang.</p>
          ) : (
            <ul style={{ listStyle: "none", margin: 0, padding: 0 }} data-testid="cabang-list">
              {branches.map((b) => (
                <li key={b.id} style={{ marginBottom: 6 }}>
                  <button
                    type="button"
                    className={b.id === selectedBranchId ? "primary" : "ghost"}
                    style={{ width: "100%", justifyContent: "space-between", display: "flex" }}
                    onClick={() => setSelectedBranchId(b.id)}
                    data-testid={`cabang-row-${b.code}`}
                  >
                    <span>
                      <strong>{b.code}</strong> · {b.name}
                    </span>
                    <span className="muted small">{b.active ? "Aktif" : "Nonaktif"}</span>
                  </button>
                </li>
              ))}
            </ul>
          )}
          {canManageBranch && selectedBranch && (
            <button
              type="button"
              className="ghost"
              style={{ marginTop: 8 }}
              onClick={() => openEditBranch(selectedBranch)}
              data-testid="cabang-edit"
            >
              <Pencil size={14} /> Ubah Cabang
            </button>
          )}
        </section>

        <section className="prod-panel" data-testid="gudang-list-panel">
          <div className="prod-panel-head" style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 8, marginBottom: 12 }}>
            <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
              <Warehouse size={16} />
              <strong>
                Gudang{selectedBranch ? ` · ${selectedBranch.code}` : ""}
              </strong>
            </div>
            {canManageWarehouse && selectedBranch && (
              <button type="button" className="primary" onClick={openCreateWarehouse} data-testid="gudang-add">
                <Plus size={14} /> Tambah Gudang
              </button>
            )}
          </div>

          {!selectedBranch ? (
            <p className="muted small">Pilih cabang untuk melihat gudang.</p>
          ) : loadingWarehouses ? (
            <p className="muted small">Memuat gudang…</p>
          ) : warehouses.length === 0 ? (
            <p className="muted small" data-testid="gudang-empty">Belum ada gudang untuk cabang ini.</p>
          ) : (
            <table className="prod-table" data-testid="gudang-table">
              <thead>
                <tr>
                  <th>Kode</th>
                  <th>Nama</th>
                  <th>Status</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {warehouses.map((w) => (
                  <tr key={w.id} data-testid={`gudang-row-${w.code}`}>
                    <td><strong>{w.code}</strong></td>
                    <td>{w.name}</td>
                    <td>{w.active ? "Aktif" : "Nonaktif"}</td>
                    <td style={{ textAlign: "right", whiteSpace: "nowrap" }}>
                      {canManageWarehouse && (
                        <>
                          <button type="button" className="ghost" onClick={() => openEditWarehouse(w)} data-testid={`gudang-edit-${w.code}`}>
                            <Pencil size={14} /> Ubah
                          </button>
                          <button
                            type="button"
                            className="ghost"
                            onClick={() => toggleWarehouseActive(w)}
                            data-testid={`gudang-toggle-${w.code}`}
                          >
                            {w.active ? "Nonaktifkan" : "Aktifkan"}
                          </button>
                        </>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </section>
      </div>

      {branchFormOpen && (
        <div className="modal-backdrop" data-testid="cabang-form-modal">
          <form className="modal-card" onSubmit={submitBranch}>
            <h2>{editingBranch ? "Ubah Cabang" : "Tambah Cabang"}</h2>
            <label>
              Kode
              <input value={branchCode} onChange={(e) => setBranchCode(e.target.value)} required data-testid="cabang-form-code" />
            </label>
            <label>
              Nama
              <input value={branchName} onChange={(e) => setBranchName(e.target.value)} required data-testid="cabang-form-name" />
            </label>
            <label style={{ display: "flex", alignItems: "center", gap: 8 }}>
              <input type="checkbox" checked={branchActive} onChange={(e) => setBranchActive(e.target.checked)} data-testid="cabang-form-active" />
              Aktif
            </label>
            <div className="modal-actions">
              <button type="button" className="ghost" disabled={savingBranch} onClick={() => setBranchFormOpen(false)}>Batal</button>
              <button type="submit" className="primary" disabled={savingBranch} data-testid="cabang-form-save">
                {savingBranch ? "Menyimpan…" : "Simpan"}
              </button>
            </div>
          </form>
        </div>
      )}

      {whFormOpen && (
        <div className="modal-backdrop" data-testid="gudang-form-modal">
          <form className="modal-card" onSubmit={submitWarehouse}>
            <h2>{editingWarehouse ? "Ubah Gudang" : "Tambah Gudang"}</h2>
            <p className="muted small">Cabang: {selectedBranch?.code} · {selectedBranch?.name}</p>
            <label>
              Kode
              <input value={whCode} onChange={(e) => { setWhCode(e.target.value); setActionError(""); }} required data-testid="gudang-form-code" />
            </label>
            <label>
              Nama
              <input value={whName} onChange={(e) => setWhName(e.target.value)} required data-testid="gudang-form-name" />
            </label>
            <label style={{ display: "flex", alignItems: "center", gap: 8 }}>
              <input type="checkbox" checked={whActive} onChange={(e) => setWhActive(e.target.checked)} data-testid="gudang-form-active" />
              Aktif
            </label>
            <div className="modal-actions">
              <button type="button" className="ghost" disabled={savingWarehouse} onClick={() => setWhFormOpen(false)}>Batal</button>
              <button type="submit" className="primary" disabled={savingWarehouse} data-testid="gudang-form-save">
                {savingWarehouse ? "Menyimpan…" : "Simpan"}
              </button>
            </div>
          </form>
        </div>
      )}
    </div>
  );
}
