export const CUSTOMER_STATUS_LABEL = {
  completed: "Selesai",
  in_progress: "Proses",
  waiting: "Menunggu",
};

export function trackingStatusLabel(status) {
  return CUSTOMER_STATUS_LABEL[status] || "Menunggu";
}

export function trackingStatusClass(status) {
  if (status === "completed") return "tracking-badge is-done";
  if (status === "in_progress") return "tracking-badge is-run";
  return "tracking-badge is-wait";
}
