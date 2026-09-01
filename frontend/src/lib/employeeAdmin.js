/** Administrasi Karyawan — labels, form helpers, status display. */

export const EA_STATUS_LABELS = {
  DRAFT: "Draft",
  DIAJUKAN: "Diajukan",
  MENUNGGU_PERSETUJUAN: "Menunggu Persetujuan",
  DISETUJUI: "Disetujui",
  DITOLAK: "Ditolak",
};

export const EA_FIELD_TYPES = [
  { value: "text", label: "Teks" },
  { value: "textarea", label: "Paragraf" },
  { value: "number", label: "Angka" },
  { value: "currency", label: "Mata uang" },
  { value: "date", label: "Tanggal" },
  { value: "datetime", label: "Tanggal & waktu" },
  { value: "select", label: "Pilihan" },
  { value: "checkbox", label: "Centang" },
  { value: "file", label: "Lampiran" },
];

export const EA_SETTINGS_TABS = [
  { id: "type", label: "Tipe Pengajuan" },
  { id: "form", label: "Form" },
  { id: "eligibility", label: "Hak Pengajuan" },
  { id: "approval", label: "Alur Persetujuan" },
  { id: "document", label: "Dokumen" },
  { id: "numbering", label: "Nomor Dokumen" },
];

export function statusLabel(status) {
  return EA_STATUS_LABELS[status] || status || "-";
}

export function fieldTypeLabel(type) {
  return (EA_FIELD_TYPES.find((t) => t.value === type) || {}).label || type;
}

export function emptyField(order = 1) {
  return {
    key: `field_${order}`,
    label: "",
    type: "text",
    required: false,
    placeholder: "",
    options: [],
    order,
    active: true,
    minimum: "",
    maximum: "",
  };
}

export function emptyStep(level = 1) {
  return {
    id: `step-${level}`,
    level,
    label: `Level ${level}`,
    active: true,
    user_ids: [],
    roles: [],
    divisions: [],
  };
}

export function emptyEligibility() {
  return { mode: "any", all_employees: true, user_ids: [], roles: [], divisions: [] };
}

export function displayValue(field, value) {
  if (!field) return value == null || value === "" ? "-" : String(value);
  if (field.type === "checkbox") return value ? "Ya" : "Tidak";
  if (field.type === "file") {
    if (value && typeof value === "object") return value.name || "Lampiran";
    return value ? String(value) : "-";
  }
  if (field.type === "currency" && value != null && value !== "") {
    const n = Number(value);
    if (Number.isFinite(n)) return `Rp ${n.toLocaleString("id-ID")}`;
  }
  if (value == null || value === "") return "-";
  return String(value);
}

export function eligibleTypeOptions(types) {
  return (types || []).filter((t) => t && t.active !== false);
}

export function currentStepLabel(row) {
  if (!row) return "-";
  if (row.status === "DISETUJUI" || row.status === "DITOLAK") return "-";
  return row.current_step_label || (row.current_step_level ? `Level ${row.current_step_label}` : "-");
}
