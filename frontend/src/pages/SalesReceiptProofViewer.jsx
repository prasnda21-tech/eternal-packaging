import { useCallback } from "react";
import InAppDocumentViewer from "../components/InAppDocumentViewer";
import api from "../lib/api";

/** Receipt-only proof viewer: compact modal/drawer; does not change default InAppDocumentViewer size. */
export default function SalesReceiptProofViewer({ receipt, onClose }) {
  const loadFile = useCallback(({ download = false, signal } = {}) => (
    api.downloadSalesReceiptPaymentProof(receipt.id, download, { signal })
  ), [receipt.id]);

  return (
    <InAppDocumentViewer
      documentKey={receipt.id}
      title="Bukti pembayaran"
      fileName={receipt?.payment_proof?.name || "bukti-pembayaran"}
      contentType={receipt?.payment_proof?.content_type || ""}
      loadFile={loadFile}
      allowDownload
      onClose={onClose}
      testId="receipt-proof-viewer"
      layout="compact"
    />
  );
}
