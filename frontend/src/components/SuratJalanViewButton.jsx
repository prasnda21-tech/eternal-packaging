import { useState } from "react";
import { useAuth } from "../lib/auth";
import { SJ_VIEW_FILE_PERM, sjHistory } from "../lib/suratJalan";
import SuratJalanPreviewModal from "./SuratJalanPreviewModal";

export default function SuratJalanViewButton({
  order,
  entries,
  label,
  testid = "sj-lihat-button",
}) {
  const { hasPerm } = useAuth();
  const canView = hasPerm(SJ_VIEW_FILE_PERM);
  const list = Array.isArray(entries) && entries.length ? entries : sjHistory(order);
  const [open, setOpen] = useState(false);
  if (!canView || !order?.id || !list.some((e) => e.file_id || e.id)) return null;
  const count = list.length;
  const text = label || (count > 1 ? `Lihat Surat Jalan (${count})` : "Lihat Surat Jalan");
  return (
    <>
      <button type="button" className="ghost sm" onClick={() => setOpen(true)} data-testid={testid}>
        {text}
      </button>
      <SuratJalanPreviewModal
        open={open}
        order={order}
        entries={list}
        canDownload={canView}
        onClose={() => setOpen(false)}
      />
    </>
  );
}
