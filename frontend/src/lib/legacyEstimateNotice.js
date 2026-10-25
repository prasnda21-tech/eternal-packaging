/** Read-only Legacy calendar→working estimate notice. Display only. */
import { formatShortAxisDate } from "./widgetDashboardModel";

/**
 * @param {object|null|undefined} sla delivery_sla_effective from API
 * @returns {{ text: string, title: string }|null}
 */
export function legacyEstimateNotice(sla) {
  if (!sla || sla.contract !== "legacy_working_days" || !sla.estimate_adjusted) {
    return null;
  }
  const from = formatShortAxisDate(sla.estimate_from || sla.legacy_calendar_date);
  const to = formatShortAxisDate(sla.estimate_to || sla.date);
  if (!from || !to || from === to) return null;
  return {
    text: `Estimasi menyesuaikan hari kerja: ${from} → ${to}`,
    title:
      "Bukan tanggal manual dan bukan SLA terkunci. Estimasi otomatis Legacy memakai hari kerja (Senin–Sabtu; Minggu dan libur operasional dilewati).",
  };
}
