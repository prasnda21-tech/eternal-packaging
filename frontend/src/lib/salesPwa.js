export const SALES_PWA_ROLE = "sales_eternal";
export const SALES_PWA_HOME = "/sales";

export const SALES_PWA_NAV = [
  { id: "dashboard", label: "Dashboard", to: "/sales" },
  { id: "orders", label: "PO Saya", to: "/sales/po" },
  { id: "visits", label: "Visit", to: "/sales/visit" },
  { id: "more", label: "Lainnya", to: "#sales-more" },
];

export const SALES_PWA_MORE_NAV = [
  { id: "invoices", label: "Invoice", to: "/sales/invoices" },
  { id: "receivables", label: "Piutang", to: "/sales/receivables" },
  { id: "customers", label: "Pelanggan", to: "/sales/pelanggan" },
  { id: "predictions", label: "Prediksi Order", to: "/sales/prediksi" },
  { id: "profile", label: "Profil", to: "/sales/profil" },
];

export function isSalesPwaRole(user) {
  return String(user?.role || "").trim() === SALES_PWA_ROLE;
}

export function isCustomerDesignApprovalPath(pathname) {
  const path = String(pathname || "");
  const prefix = "/customer/design-approval/";
  if (!path.startsWith(prefix)) return false;
  const token = path.slice(prefix.length).split("/")[0];
  return Boolean(token);
}

export function isSafeSalesNextPath(path) {
  if (typeof path !== "string") return false;
  const value = path.trim();
  if (value.startsWith("//") || value.includes("://") || value.includes("\\")) return false;
  return value === "/sales" || value.startsWith("/sales/");
}

export function activeSalesPwaNav(pathname) {
  const path = String(pathname || "");
  if (path === "/sales") return "dashboard";
  if (path.startsWith("/sales/visit")) return "visits";
  if (path === "/sales/po" || path.startsWith("/sales/po/")) return "orders";
  if (path.startsWith("/sales/invoices")) return "invoices";
  if (path.startsWith("/sales/receivables")) return "receivables";
  if (path.startsWith("/sales/pelanggan")) return "customers";
  if (path.startsWith("/sales/prediksi")) return "predictions";
  if (path.startsWith("/sales/profil")) return "profile";
  if (path.startsWith("/sales")) return "dashboard";
  return "";
}

export function isValidSalesManifest(manifest) {
  if (!manifest || typeof manifest !== "object") return false;
  if (manifest.name !== "Eternal Packaging Sales") return false;
  if (manifest.display !== "standalone") return false;
  if (manifest.start_url !== "/sales") return false;
  if (manifest.scope !== "/sales") return false;
  if (!Array.isArray(manifest.icons) || !manifest.icons.length) return false;
  return true;
}

export function salesHomeForUser(user, nextPath) {
  if (!isSalesPwaRole(user)) return null;
  return isSafeSalesNextPath(nextPath) ? nextPath : SALES_PWA_HOME;
}

export function formatSalesRupiah(value) {
  if (value === null || value === undefined || value === "") return null;
  const amount = Number(value);
  if (!Number.isFinite(amount)) return null;
  return `Rp${amount.toLocaleString("id-ID")}`;
}

export function formatSalesDate(iso) {
  const text = String(iso || "").trim();
  if (!text) return "—";
  const day = text.slice(0, 10);
  const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(day);
  if (!m) return text;
  return `${m[3]}/${m[2]}/${m[1].slice(-2)}`;
}

export function salesJakartaDay(date = new Date()) {
  const instant = date instanceof Date ? date : new Date(date);
  if (Number.isNaN(instant.getTime())) return "";
  const parts = new Intl.DateTimeFormat("en-GB", {
    timeZone: "Asia/Jakarta",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  }).formatToParts(instant);
  const pick = (type) => parts.find((part) => part.type === type)?.value || "";
  const year = pick("year");
  const month = pick("month");
  const day = pick("day");
  if (!year || !month || !day) return "";
  return `${year}-${month}-${day}`;
}

export function salesWibParts(iso) {
  const text = String(iso || "").trim();
  if (!text) return null;
  const zoned = /(?:Z|[+-]\d{2}:\d{2})$/i.test(text);
  if (!zoned) {
    const day = text.slice(0, 10);
    if (!/^\d{4}-\d{2}-\d{2}$/.test(day)) return null;
    const clock = /T(\d{2}):(\d{2})/.exec(text);
    return { day, clock: clock ? `${clock[1]}:${clock[2]}` : "" };
  }
  const date = new Date(text);
  if (Number.isNaN(date.getTime())) return null;
  const parts = new Intl.DateTimeFormat("en-GB", {
    timeZone: "Asia/Jakarta",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    hourCycle: "h23",
  }).formatToParts(date);
  const pick = (type) => parts.find((part) => part.type === type)?.value || "";
  const hour = pick("hour") === "24" ? "00" : pick("hour");
  return { day: `${pick("year")}-${pick("month")}-${pick("day")}`, clock: `${hour}:${pick("minute")}` };
}

export function formatSalesWibInstant(iso) {
  const parts = salesWibParts(iso);
  if (!parts) {
    const fallback = formatSalesDate(iso);
    return fallback === "—" ? "" : fallback;
  }
  const label = formatSalesDate(parts.day);
  return parts.clock ? `${label} ${parts.clock}` : label;
}

export function documentTotal(order) {
  const docs = order?.documents || {};
  const n = Number(docs.total);
  if (Number.isFinite(n)) return n;
  return (Number(docs.po) || 0) + (Number(docs.sj) || 0) + (Number(docs.invoice) || 0) + (Number(docs.bukti_kirim) || 0);
}

export function formatSalesMoney(value) {
  const amount = Number(value);
  if (!Number.isFinite(amount)) return "Rp0";
  return `Rp${new Intl.NumberFormat("id-ID", { maximumFractionDigits: 0 }).format(amount)}`;
}

export function currentSalesMonth(now = new Date()) {
  const year = now.getFullYear();
  const month = String(now.getMonth() + 1).padStart(2, "0");
  return `${year}-${month}`;
}

export function salesMonthBounds(month) {
  const [year, monthNumber] = String(month || "").split("-").map(Number);
  if (!year || !monthNumber || monthNumber < 1 || monthNumber > 12) return { date_from: "", date_to: "" };
  const last = new Date(year, monthNumber, 0).getDate();
  const key = `${year}-${String(monthNumber).padStart(2, "0")}`;
  return {
    date_from: `${key}-01`,
    date_to: `${key}-${String(last).padStart(2, "0")}`,
  };
}
