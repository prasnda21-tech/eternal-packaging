import { useCallback, useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import api from "../lib/api";
import { useAuth } from "../lib/auth";
import "./printTemplateDesigner.css";

export default function PrintTemplateList() {
  const navigate = useNavigate();
  const { hasPerm, user } = useAuth();
  const canManage = user?.role === "admin" || user?.is_super_admin || hasPerm("print_template.manage");
  const [rows, setRows] = useState([]);
  const [documentType, setDocumentType] = useState("");
  const [status, setStatus] = useState("");
  const [error, setError] = useState("");
  const [versions, setVersions] = useState(null);

  const load = useCallback(async () => {
    const res = await api.listPrintTemplates({ document_type: documentType, status });
    setRows(res.templates || []);
  }, [documentType, status]);

  useEffect(() => {
    load().catch((err) => setError(err.response?.data?.detail || "Daftar desain gagal dimuat."));
  }, [load]);

  async function createTemplate() {
    const name = window.prompt("Nama template", "Faktur Pelanggan");
    if (!name) return;
    const document_type = window.prompt("Jenis: sales_invoice atau sales_delivery", "sales_invoice");
    const audience = window.prompt("Penggunaan: customer atau internal", "customer");
    const res = await api.createPrintTemplate({ name, document_type, audience });
    navigate(`/settings/desain-cetakan/${res.template.id}`);
  }

  async function act(row, action) {
    setError("");
    try {
      if (action === "copy") {
        const res = await api.copyPrintTemplate(row.id);
        navigate(`/settings/desain-cetakan/${res.template.id}`);
        return;
      }
      if (action === "default") await api.setDefaultPrintTemplate(row.id, row.revision);
      if (action === "archive") await api.archivePrintTemplate(row.id, row.revision);
      if (action === "history") {
        const res = await api.printTemplateVersions(row.id);
        setVersions({ name: row.name, rows: res.versions || [], template: row });
        return;
      }
      await load();
    } catch (err) {
      const detail = err.response?.data?.detail;
      setError(typeof detail === "string" ? detail : detail?.message || "Aksi gagal.");
    }
  }

  return (
    <div className="pt-list-page" data-testid="print-template-list">
      <div className="pt-filters">
        <button type="button" className="pt-btn primary" data-testid="print-template-create" disabled={!canManage} onClick={createTemplate}>Template Baru</button>
        <select data-testid="print-filter-type" value={documentType} onChange={(e) => setDocumentType(e.target.value)}>
          <option value="">Semua jenis</option>
          <option value="sales_invoice">Faktur Penjualan</option>
          <option value="sales_delivery">Surat Jalan</option>
        </select>
        <select data-testid="print-filter-status" value={status} onChange={(e) => setStatus(e.target.value)}>
          <option value="">Semua status</option>
          <option value="draft">Draf</option>
          <option value="active">Aktif</option>
          <option value="archived">Diarsipkan</option>
        </select>
      </div>
      {error && <p className="pt-error">{error}</p>}
      <table className="pt-table">
        <thead>
          <tr>
            <th>Nama template</th>
            <th>Jenis dokumen</th>
            <th>Penggunaan</th>
            <th>Status</th>
            <th>Default</th>
            <th>Terakhir diubah</th>
            <th>Aksi</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr key={row.id} data-testid={`print-template-row-${row.id}`}>
              <td>{row.name}</td>
              <td>{row.document_label}</td>
              <td>{row.audience_label}</td>
              <td>{row.status_label}</td>
              <td>{row.is_default ? "Default" : ""}</td>
              <td>{row.updated_at ? String(row.updated_at).slice(0, 16).replace("T", " ") : ""}</td>
              <td className="pt-actions">
                <button type="button" onClick={() => navigate(`/settings/desain-cetakan/${row.id}`)}>Buka Desainer</button>
                <button type="button" onClick={() => navigate(`/settings/desain-cetakan/${row.id}`)}>Pratinjau</button>
                <button type="button" disabled={!canManage} onClick={() => act(row, "copy")}>Salin</button>
                <button type="button" disabled={!canManage || row.status !== "active"} onClick={() => act(row, "default")}>Jadikan Default</button>
                <button type="button" disabled={!canManage || row.is_builtin} onClick={() => act(row, "archive")}>Arsipkan</button>
                <button type="button" onClick={() => act(row, "history")}>Riwayat Versi</button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      {versions && (
        <div className="pt-note" data-testid="print-version-history">
          <strong>Riwayat {versions.name}</strong>
          <ul>
            {versions.rows.map((row) => (
              <li key={row.id}>
                {row.action} · v{row.version} · {row.actor?.name} · {String(row.at || "").slice(0, 16).replace("T", " ")}
                {canManage && ["activate", "restore", "save_draft", "create", "copy"].includes(row.action) && (
                  <button type="button" onClick={async () => {
                    const current = rows.find((item) => item.id === versions.template.id);
                    await api.restorePrintTemplateVersion(versions.template.id, row.id, current?.revision);
                    setVersions(null);
                    await load();
                  }}>Gunakan Versi Ini</button>
                )}
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}
