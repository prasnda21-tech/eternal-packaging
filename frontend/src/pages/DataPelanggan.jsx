import React, { useState, useEffect, useCallback, useMemo } from "react";
import {
  Users,
  Plus,
  Search,
  Edit2,
  Phone,
  Mail,
  MapPin,
  Building,
  AlertCircle,
  CheckCircle2,
  X,
  Loader2,
  FileText,
} from "lucide-react";
import api from "../lib/api";
import { useAuth } from "../lib/auth";
import { FormSection } from "../components/form-standard";

const INITIAL_FORM = {
  name: "",
  phone: "",
  address: "",
  pic: "",
  email: "",
  city: "",
  notes: "",
};

export default function DataPelanggan() {
  const { hasPerm } = useAuth();
  const canManage = hasPerm("sales.customer.manage") || hasPerm("admin");

  const [customers, setCustomers] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [searchQuery, setSearchQuery] = useState("");

  // Modal State
  const [modalOpen, setModalOpen] = useState(false);
  const [editingCustomer, setEditingCustomer] = useState(null);
  const [formData, setFormData] = useState({ ...INITIAL_FORM });
  const [formBusy, setFormBusy] = useState(false);
  const [formError, setFormError] = useState("");
  const [successMsg, setSuccessMsg] = useState("");

  const loadCustomers = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      const params = {};
      if (searchQuery.trim()) {
        params.q = searchQuery.trim();
      }
      const data = await api.listSalesCustomers(params);
      setCustomers(data.customers || []);
    } catch (err) {
      const det = err.response?.data?.detail;
      setError(typeof det === "string" ? det : "Gagal memuat data pelanggan");
    } finally {
      setLoading(false);
    }
  }, [searchQuery]);

  useEffect(() => {
    loadCustomers();
  }, [loadCustomers]);

  const handleOpenCreate = () => {
    setEditingCustomer(null);
    setFormData({ ...INITIAL_FORM });
    setFormError("");
    setModalOpen(true);
  };

  const handleOpenEdit = (cust) => {
    setEditingCustomer(cust);
    setFormData({
      name: cust.name || "",
      phone: cust.phone || "",
      address: cust.address || "",
      pic: cust.pic || "",
      email: cust.email || "",
      city: cust.city || "",
      notes: cust.notes || "",
    });
    setFormError("");
    setModalOpen(true);
  };

  const handleCloseModal = () => {
    if (formBusy) return;
    setModalOpen(false);
    setEditingCustomer(null);
    setFormError("");
  };

  const handleFormChange = (field, val) => {
    setFormData((prev) => ({ ...prev, [field]: val }));
    if (formError) setFormError("");
  };

  const handleFormSubmit = async (e) => {
    e.preventDefault();
    if (!formData.name.trim()) {
      setFormError("Nama Pelanggan wajib diisi.");
      return;
    }
    if (!formData.phone.trim()) {
      setFormError("No. HP / WhatsApp wajib diisi.");
      return;
    }
    if (!formData.address.trim()) {
      setFormError("Alamat wajib diisi.");
      return;
    }

    setFormBusy(true);
    setFormError("");
    try {
      const payload = {
        name: formData.name.trim(),
        phone: formData.phone.trim(),
        address: formData.address.trim(),
        pic: formData.pic.trim(),
        email: formData.email.trim(),
        city: formData.city.trim(),
        notes: formData.notes.trim(),
      };

      if (editingCustomer) {
        await api.updateSalesCustomer(editingCustomer.id, payload);
        setSuccessMsg("Data pelanggan berhasil diperbarui!");
      } else {
        await api.createSalesCustomer(payload);
        setSuccessMsg("Pelanggan baru berhasil ditambahkan!");
      }

      setModalOpen(false);
      loadCustomers();
      setTimeout(() => setSuccessMsg(""), 4000);
    } catch (err) {
      const det = err.response?.data?.detail;
      if (typeof det === "string") {
        setFormError(det);
      } else if (typeof det === "object" && det?.message) {
        setFormError(det.message);
      } else {
        setFormError("Gagal menyimpan data pelanggan. Pastikan tidak ada duplikasi nama.");
      }
    } finally {
      setFormBusy(false);
    }
  };

  const filteredCustomers = useMemo(() => {
    if (!searchQuery.trim()) return customers;
    const q = searchQuery.toLowerCase();
    return customers.filter(
      (c) =>
        (c.name || "").toLowerCase().includes(q) ||
        (c.phone || "").toLowerCase().includes(q) ||
        (c.pic || "").toLowerCase().includes(q) ||
        (c.email || "").toLowerCase().includes(q) ||
        (c.city || "").toLowerCase().includes(q) ||
        (c.address || "").toLowerCase().includes(q)
    );
  }, [customers, searchQuery]);

  return (
    <div style={{ padding: "24px 32px", maxWidth: 1400, margin: "0 auto" }}>
      {/* Header */}
      <div
        style={{
          display: "flex",
          justifyContent: "space-between",
          alignItems: "flex-start",
          marginBottom: 24,
          flexWrap: "wrap",
          gap: 16,
        }}
      >
        <div>
          <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
            <div
              style={{
                width: 40,
                height: 40,
                borderRadius: 10,
                backgroundColor: "#e0f2fe",
                display: "flex",
                alignItems: "center",
                justifyContent: "center",
                color: "#0284c7",
              }}
            >
              <Users size={22} />
            </div>
            <div>
              <h1 style={{ fontSize: "1.5rem", fontWeight: 700, margin: 0, color: "#0f172a" }}>
                Data Pelanggan
              </h1>
              <p style={{ margin: "2px 0 0", fontSize: "0.875rem", color: "#64748b" }}>
                Master data pelanggan untuk Pesanan Penjualan dan alur distribusi.
              </p>
            </div>
          </div>
        </div>

        <div style={{ display: "flex", gap: 12, alignItems: "center" }}>
          {canManage && (
            <button
              type="button"
              onClick={handleOpenCreate}
              data-testid="btn-add-customer"
              style={{
                display: "inline-flex",
                alignItems: "center",
                gap: 8,
                backgroundColor: "#0284c7",
                color: "#ffffff",
                border: "none",
                borderRadius: 8,
                padding: "10px 18px",
                fontSize: "0.9rem",
                fontWeight: 600,
                cursor: "pointer",
                boxShadow: "0 1px 2px rgba(0,0,0,0.05)",
                transition: "background 0.15s ease",
              }}
              onMouseOver={(e) => (e.currentTarget.style.backgroundColor = "#0369a1")}
              onMouseOut={(e) => (e.currentTarget.style.backgroundColor = "#0284c7")}
            >
              <Plus size={18} />
              Tambah Pelanggan
            </button>
          )}
        </div>
      </div>

      {/* Success Notification */}
      {successMsg && (
        <div
          data-testid="customer-success-msg"
          style={{
            padding: "12px 16px",
            backgroundColor: "#f0fdf4",
            border: "1px solid #bbf7d0",
            borderRadius: 8,
            marginBottom: 20,
            display: "flex",
            alignItems: "center",
            gap: 10,
            color: "#166534",
            fontSize: "0.9rem",
          }}
        >
          <CheckCircle2 size={18} color="#16a34a" />
          <span>{successMsg}</span>
        </div>
      )}

      {/* Error Notification */}
      {error && (
        <div
          data-testid="customer-list-error"
          style={{
            padding: "12px 16px",
            backgroundColor: "#fef2f2",
            border: "1px solid #fecaca",
            borderRadius: 8,
            marginBottom: 20,
            display: "flex",
            alignItems: "center",
            gap: 10,
            color: "#991b1b",
            fontSize: "0.9rem",
          }}
        >
          <AlertCircle size={18} color="#dc2626" />
          <span>{error}</span>
        </div>
      )}

      {/* Filter / Search Bar */}
      <div
        style={{
          backgroundColor: "#ffffff",
          border: "1px solid #e2e8f0",
          borderRadius: 12,
          padding: "14px 18px",
          marginBottom: 20,
          display: "flex",
          justifyContent: "space-between",
          alignItems: "center",
          flexWrap: "wrap",
          gap: 12,
          boxShadow: "0 1px 3px rgba(0,0,0,0.02)",
        }}
      >
        <div style={{ position: "relative", minWidth: 320, flex: 1, maxWidth: 480 }}>
          <Search
            size={16}
            style={{
              position: "absolute",
              left: 12,
              top: "50%",
              transform: "translateY(-50%)",
              color: "#94a3b8",
            }}
          />
          <input
            type="text"
            data-testid="input-search-customer"
            placeholder="Cari pelanggan (nama, telp, PIC, kota)..."
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
            style={{
              width: "100%",
              padding: "8px 12px 8px 36px",
              borderRadius: 8,
              border: "1px solid #cbd5e1",
              fontSize: "0.9rem",
              boxSizing: "border-box",
              outline: "none",
            }}
          />
        </div>
        <div style={{ fontSize: "0.85rem", color: "#64748b", fontWeight: 500 }}>
          Total: <strong>{filteredCustomers.length}</strong> pelanggan
        </div>
      </div>

      {/* Table Container */}
      <div
        style={{
          backgroundColor: "#ffffff",
          border: "1px solid #e2e8f0",
          borderRadius: 12,
          overflow: "hidden",
          boxShadow: "0 1px 3px rgba(0,0,0,0.03)",
        }}
      >
        {loading ? (
          <div style={{ padding: "48px 0", textAlign: "center", color: "#64748b" }}>
            <Loader2 size={32} className="animate-spin" style={{ margin: "0 auto 12px" }} />
            <p style={{ margin: 0, fontSize: "0.95rem" }}>Memuat daftar pelanggan...</p>
          </div>
        ) : filteredCustomers.length === 0 ? (
          <div style={{ padding: "56px 0", textAlign: "center", color: "#64748b" }}>
            <Users size={40} style={{ margin: "0 auto 12px", color: "#cbd5e1" }} />
            <p style={{ fontSize: "1rem", fontWeight: 600, color: "#334155", margin: 0 }}>
              Tidak ada data pelanggan
            </p>
            <p style={{ fontSize: "0.875rem", color: "#94a3b8", margin: "4px 0 16px" }}>
              {searchQuery
                ? "Tidak ditemukan pelanggan yang sesuai kata kunci pencarian."
                : "Belum ada master pelanggan yang terdaftar."}
            </p>
            {canManage && !searchQuery && (
              <button
                type="button"
                onClick={handleOpenCreate}
                style={{
                  display: "inline-flex",
                  alignItems: "center",
                  gap: 6,
                  backgroundColor: "#0284c7",
                  color: "#fff",
                  border: "none",
                  borderRadius: 6,
                  padding: "8px 14px",
                  fontSize: "0.85rem",
                  fontWeight: 600,
                  cursor: "pointer",
                }}
              >
                <Plus size={16} /> Tambah Pelanggan Pertama
              </button>
            )}
          </div>
        ) : (
          <div style={{ overflowX: "auto" }}>
            <table
              data-testid="customers-table"
              style={{
                width: "100%",
                borderCollapse: "collapse",
                textAlign: "left",
                fontSize: "0.875rem",
              }}
            >
              <thead>
                <tr
                  style={{
                    backgroundColor: "#f8fafc",
                    borderBottom: "1px solid #e2e8f0",
                    color: "#475569",
                    fontWeight: 600,
                  }}
                >
                  <th style={{ padding: "12px 16px" }}>Nama Pelanggan</th>
                  <th style={{ padding: "12px 16px" }}>No. HP / WhatsApp</th>
                  <th style={{ padding: "12px 16px" }}>PIC / Kontak</th>
                  <th style={{ padding: "12px 16px" }}>Kota</th>
                  <th style={{ padding: "12px 16px" }}>Alamat Lengkap</th>
                  <th style={{ padding: "12px 16px" }}>Catatan</th>
                  <th style={{ padding: "12px 16px", textAlign: "center", width: 90 }}>Aksi</th>
                </tr>
              </thead>
              <tbody>
                {filteredCustomers.map((cust) => (
                  <tr
                    key={cust.id}
                    data-testid={`customer-row-${cust.id}`}
                    style={{
                      borderBottom: "1px solid #f1f5f9",
                      transition: "background 0.1s ease",
                    }}
                    onMouseOver={(e) => (e.currentTarget.style.backgroundColor = "#f8fafc")}
                    onMouseOut={(e) => (e.currentTarget.style.backgroundColor = "transparent")}
                  >
                    <td style={{ padding: "14px 16px", fontWeight: 600, color: "#0f172a" }}>
                      <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                        <Building size={16} color="#64748b" />
                        <span>{cust.name}</span>
                      </div>
                    </td>
                    <td style={{ padding: "14px 16px", color: "#334155" }}>
                      {cust.phone ? (
                        <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
                          <Phone size={14} color="#059669" />
                          <span>{cust.phone}</span>
                        </div>
                      ) : (
                        "—"
                      )}
                    </td>
                    <td style={{ padding: "14px 16px", color: "#475569" }}>
                      {cust.pic || "—"}
                      {cust.email && (
                        <div
                          style={{
                            display: "flex",
                            alignItems: "center",
                            gap: 4,
                            fontSize: "0.775rem",
                            color: "#64748b",
                            marginTop: 2,
                          }}
                        >
                          <Mail size={12} />
                          <span>{cust.email}</span>
                        </div>
                      )}
                    </td>
                    <td style={{ padding: "14px 16px", color: "#475569" }}>
                      {cust.city ? (
                        <span
                          style={{
                            display: "inline-block",
                            padding: "2px 8px",
                            backgroundColor: "#f1f5f9",
                            borderRadius: 4,
                            fontSize: "0.8rem",
                            fontWeight: 500,
                          }}
                        >
                          {cust.city}
                        </span>
                      ) : (
                        "—"
                      )}
                    </td>
                    <td
                      style={{
                        padding: "14px 16px",
                        color: "#475569",
                        maxWidth: 280,
                        whiteSpace: "normal",
                        lineHeight: 1.4,
                      }}
                    >
                      {cust.address ? (
                        <div style={{ display: "flex", alignItems: "flex-start", gap: 6 }}>
                          <MapPin size={14} color="#64748b" style={{ flexShrink: 0, marginTop: 2 }} />
                          <span>{cust.address}</span>
                        </div>
                      ) : (
                        "—"
                      )}
                    </td>
                    <td
                      style={{
                        padding: "14px 16px",
                        color: "#64748b",
                        maxWidth: 200,
                        whiteSpace: "normal",
                        fontSize: "0.8rem",
                      }}
                    >
                      {cust.notes || "—"}
                    </td>
                    <td style={{ padding: "14px 16px", textAlign: "center" }}>
                      {canManage && (
                        <button
                          type="button"
                          onClick={() => handleOpenEdit(cust)}
                          data-testid={`btn-edit-customer-${cust.id}`}
                          title="Edit Data Pelanggan"
                          style={{
                            display: "inline-flex",
                            alignItems: "center",
                            justifyContent: "center",
                            width: 32,
                            height: 32,
                            borderRadius: 6,
                            border: "1px solid #cbd5e1",
                            backgroundColor: "#ffffff",
                            color: "#334155",
                            cursor: "pointer",
                            transition: "all 0.1s ease",
                          }}
                          onMouseOver={(e) => {
                            e.currentTarget.style.backgroundColor = "#f1f5f9";
                            e.currentTarget.style.borderColor = "#94a3b8";
                          }}
                          onMouseOut={(e) => {
                            e.currentTarget.style.backgroundColor = "#ffffff";
                            e.currentTarget.style.borderColor = "#cbd5e1";
                          }}
                        >
                          <Edit2 size={14} />
                        </button>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {/* Modal Tambah / Edit Pelanggan */}
      {modalOpen && (
        <div
          data-testid="customer-modal-backdrop"
          style={{
            position: "fixed",
            top: 0,
            left: 0,
            right: 0,
            bottom: 0,
            backgroundColor: "rgba(15, 23, 42, 0.6)",
            backdropFilter: "blur(2px)",
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            zIndex: 1000,
            padding: 16,
          }}
        >
          <div
            data-testid="customer-modal"
            style={{
              backgroundColor: "#ffffff",
              borderRadius: 14,
              width: "100%",
              maxWidth: 680,
              maxHeight: "92vh",
              overflowY: "auto",
              boxShadow: "0 20px 25px -5px rgba(0, 0, 0, 0.1), 0 10px 10px -5px rgba(0, 0, 0, 0.04)",
            }}
          >
            {/* Modal Header */}
            <div
              style={{
                display: "flex",
                justifyContent: "space-between",
                alignItems: "center",
                padding: "18px 24px",
                borderBottom: "1px solid #e2e8f0",
              }}
            >
              <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
                <div
                  style={{
                    width: 36,
                    height: 36,
                    borderRadius: 8,
                    backgroundColor: "#e0f2fe",
                    display: "flex",
                    alignItems: "center",
                    justifyContent: "center",
                    color: "#0284c7",
                  }}
                >
                  <Users size={20} />
                </div>
                <div>
                  <h2
                    data-testid="modal-title"
                    style={{ fontSize: "1.15rem", fontWeight: 700, margin: 0, color: "#0f172a" }}
                  >
                    {editingCustomer ? "Edit Data Pelanggan" : "Tambah Pelanggan Baru"}
                  </h2>
                  <p style={{ margin: "2px 0 0", fontSize: "0.8rem", color: "#64748b" }}>
                    {editingCustomer
                      ? "Perbarui informasi kontak dan alamat pelanggan."
                      : "Daftarkan pelanggan baru ke master Data Pelanggan."}
                  </p>
                </div>
              </div>
              <button
                type="button"
                onClick={handleCloseModal}
                disabled={formBusy}
                style={{
                  background: "transparent",
                  border: "none",
                  cursor: "pointer",
                  color: "#94a3b8",
                  padding: 4,
                  borderRadius: 6,
                }}
              >
                <X size={20} />
              </button>
            </div>

            {/* Modal Form */}
            <form onSubmit={handleFormSubmit} style={{ padding: 24 }}>
              {formError && (
                <div
                  data-testid="customer-form-error"
                  style={{
                    padding: "12px 16px",
                    backgroundColor: "#fef2f2",
                    border: "1px solid #fecaca",
                    borderRadius: 8,
                    marginBottom: 16,
                    display: "flex",
                    alignItems: "center",
                    gap: 10,
                    color: "#991b1b",
                    fontSize: "0.875rem",
                  }}
                >
                  <AlertCircle size={18} color="#dc2626" style={{ flexShrink: 0 }} />
                  <span>{formError}</span>
                </div>
              )}

              {/* FormSection: consistent with PesananPenjualan */}
              <FormSection
                id="customer-master-form"
                title="Informasi Pelanggan"
                description="Lengkapi data identitas pelanggan. Tanpa rincian barang/line item."
              >
                <div
                  style={{
                    display: "grid",
                    gridTemplateColumns: "repeat(auto-fit, minmax(240px, 1fr))",
                    gap: 16,
                  }}
                >
                  {/* Nama Pelanggan */}
                  <div style={{ gridColumn: "1 / -1" }}>
                    <label
                      style={{
                        display: "block",
                        fontSize: "0.85rem",
                        fontWeight: 600,
                        color: "#334155",
                        marginBottom: 4,
                      }}
                    >
                      Nama Pelanggan <span style={{ color: "#ef4444" }}>*</span>
                    </label>
                    <input
                      type="text"
                      data-testid="input-customer-name"
                      placeholder="mis. PT Sumber Rasa Jaya"
                      value={formData.name}
                      onChange={(e) => handleFormChange("name", e.target.value)}
                      required
                      style={{
                        width: "100%",
                        padding: "8px 12px",
                        border: "1px solid #cbd5e1",
                        borderRadius: 6,
                        fontSize: "0.95rem",
                        boxSizing: "border-box",
                      }}
                    />
                  </div>

                  {/* No HP / WA */}
                  <div>
                    <label
                      style={{
                        display: "block",
                        fontSize: "0.85rem",
                        fontWeight: 600,
                        color: "#334155",
                        marginBottom: 4,
                      }}
                    >
                      No. HP / WhatsApp <span style={{ color: "#ef4444" }}>*</span>
                    </label>
                    <input
                      type="text"
                      data-testid="input-customer-phone"
                      placeholder="mis. 0812-3456-7890"
                      value={formData.phone}
                      onChange={(e) => handleFormChange("phone", e.target.value)}
                      required
                      style={{
                        width: "100%",
                        padding: "8px 12px",
                        border: "1px solid #cbd5e1",
                        borderRadius: 6,
                        fontSize: "0.95rem",
                        boxSizing: "border-box",
                      }}
                    />
                  </div>

                  {/* PIC */}
                  <div>
                    <label
                      style={{
                        display: "block",
                        fontSize: "0.85rem",
                        fontWeight: 600,
                        color: "#334155",
                        marginBottom: 4,
                      }}
                    >
                      Kontak Person (PIC)
                    </label>
                    <input
                      type="text"
                      data-testid="input-customer-pic"
                      placeholder="mis. Ibu Dewi"
                      value={formData.pic}
                      onChange={(e) => handleFormChange("pic", e.target.value)}
                      style={{
                        width: "100%",
                        padding: "8px 12px",
                        border: "1px solid #cbd5e1",
                        borderRadius: 6,
                        fontSize: "0.95rem",
                        boxSizing: "border-box",
                      }}
                    />
                  </div>

                  {/* Email */}
                  <div>
                    <label
                      style={{
                        display: "block",
                        fontSize: "0.85rem",
                        fontWeight: 600,
                        color: "#334155",
                        marginBottom: 4,
                      }}
                    >
                      Email Pelanggan
                    </label>
                    <input
                      type="email"
                      data-testid="input-customer-email"
                      placeholder="mis. purchasing@perusahaan.com"
                      value={formData.email}
                      onChange={(e) => handleFormChange("email", e.target.value)}
                      style={{
                        width: "100%",
                        padding: "8px 12px",
                        border: "1px solid #cbd5e1",
                        borderRadius: 6,
                        fontSize: "0.95rem",
                        boxSizing: "border-box",
                      }}
                    />
                  </div>

                  {/* Kota */}
                  <div>
                    <label
                      style={{
                        display: "block",
                        fontSize: "0.85rem",
                        fontWeight: 600,
                        color: "#334155",
                        marginBottom: 4,
                      }}
                    >
                      Kota / Kabupaten
                    </label>
                    <input
                      type="text"
                      data-testid="input-customer-city"
                      placeholder="mis. Jakarta Barat"
                      value={formData.city}
                      onChange={(e) => handleFormChange("city", e.target.value)}
                      style={{
                        width: "100%",
                        padding: "8px 12px",
                        border: "1px solid #cbd5e1",
                        borderRadius: 6,
                        fontSize: "0.95rem",
                        boxSizing: "border-box",
                      }}
                    />
                  </div>
                </div>

                {/* Alamat Lengkap */}
                <div style={{ marginTop: 16 }}>
                  <label
                    style={{
                      display: "block",
                      fontSize: "0.85rem",
                      fontWeight: 600,
                      color: "#334155",
                      marginBottom: 4,
                    }}
                  >
                    Alamat Lengkap <span style={{ color: "#ef4444" }}>*</span>
                  </label>
                  <textarea
                    data-testid="input-customer-address"
                    rows={3}
                    placeholder="Alamat jalan, gedung, nomor, kawasan industri..."
                    value={formData.address}
                    onChange={(e) => handleFormChange("address", e.target.value)}
                    required
                    style={{
                      width: "100%",
                      padding: "8px 12px",
                      border: "1px solid #cbd5e1",
                      borderRadius: 6,
                      fontSize: "0.95rem",
                      boxSizing: "border-box",
                    }}
                  />
                </div>

                {/* Catatan Pelanggan */}
                <div style={{ marginTop: 14 }}>
                  <label
                    style={{
                      display: "block",
                      fontSize: "0.85rem",
                      fontWeight: 600,
                      color: "#334155",
                      marginBottom: 4,
                    }}
                  >
                    Catatan Khusus
                  </label>
                  <textarea
                    data-testid="input-customer-notes"
                    rows={2}
                    placeholder="Preferensi penagihan, instruksi penerimaan, dsb."
                    value={formData.notes}
                    onChange={(e) => handleFormChange("notes", e.target.value)}
                    style={{
                      width: "100%",
                      padding: "8px 12px",
                      border: "1px solid #cbd5e1",
                      borderRadius: 6,
                      fontSize: "0.95rem",
                      boxSizing: "border-box",
                    }}
                  />
                </div>
              </FormSection>

              {/* Action Buttons */}
              <div
                style={{
                  display: "flex",
                  justifyContent: "flex-end",
                  gap: 12,
                  marginTop: 24,
                  borderTop: "1px solid #f1f5f9",
                  paddingTop: 16,
                }}
              >
                <button
                  type="button"
                  data-testid="btn-cancel-customer"
                  onClick={handleCloseModal}
                  disabled={formBusy}
                  style={{
                    padding: "9px 18px",
                    borderRadius: 6,
                    border: "1px solid #cbd5e1",
                    backgroundColor: "#ffffff",
                    color: "#475569",
                    fontWeight: 600,
                    fontSize: "0.9rem",
                    cursor: "pointer",
                  }}
                >
                  Batal
                </button>
                <button
                  type="submit"
                  data-testid="btn-save-customer"
                  disabled={formBusy}
                  style={{
                    display: "inline-flex",
                    alignItems: "center",
                    gap: 6,
                    padding: "9px 20px",
                    borderRadius: 6,
                    border: "none",
                    backgroundColor: "#0284c7",
                    color: "#ffffff",
                    fontWeight: 600,
                    fontSize: "0.9rem",
                    cursor: formBusy ? "not-allowed" : "pointer",
                    opacity: formBusy ? 0.7 : 1,
                  }}
                >
                  {formBusy && <Loader2 size={16} className="animate-spin" />}
                  {formBusy ? "Menyimpan..." : editingCustomer ? "Simpan Perubahan" : "Simpan Pelanggan"}
                </button>
              </div>
            </form>
          </div>
        </div>
      )}
    </div>
  );
}
