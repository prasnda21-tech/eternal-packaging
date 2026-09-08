import { useRef, useState } from "react";
import api from "../../lib/api";
import { formatApiError } from "../../lib/errors";
import { newActionId } from "../../lib/workflowEntries";
import {
  MIN_DELIVERY_PROOFS,
  canSubmitConfirmDelivered,
  confirmDeliveredBlockReason,
  proofUploadUserError,
} from "../../lib/deliveryProof";
import { compressImage } from "../../lib/imageCompress";
import { getCanonicalCustomerName } from "../../lib/canonicalCustomer";
import { deliveryAddressFromOrder, mapsSearchUrl } from "../../lib/mobileWorkspaces";
import { applyLocalSignature } from "../../lib/mobileSignature";
import { fileUrl } from "../../lib/api";
import MobileSignatureModal from "../components/MobileSignatureModal";

export default function MobileDeliveryWorkspace({ order, canEdit, onUpdated }) {
  const delivered = order?.delivery_status === "Terkirim" || !!order?.terkirim_confirmed;
  const proofs = order?.delivery_proofs || [];
  const address = deliveryAddressFromOrder(order);
  const maps = mapsSearchUrl(address);
  const inputRef = useRef(null);
  const [recipientName, setRecipientName] = useState(order?.delivery_recipient_signature?.recipient_name || "");
  const [localSig, setLocalSig] = useState({ recipient: null, driver: null });
  const [modal, setModal] = useState({ open: false, role: null });
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");

  const orderSig = order?.delivery_recipient_signature;
  const driverSig = order?.delivery_driver_signature;
  const canConfirm = canSubmitConfirmDelivered(
    proofs,
    recipientName,
    localSig.recipient,
    orderSig,
    localSig.driver,
    driverSig,
  ) && Number(order?.eligible_delivery_qty || 0) > 0 && canEdit && !delivered;

  const addPhoto = async (file) => {
    if (!file || !canEdit || delivered) return;
    setBusy(true);
    setErr("");
    try {
      const compressed = await compressImage(file);
      const res = await api.uploadDeliveryProof(order.id, compressed.file);
      onUpdated(res.order);
    } catch (e) {
      setErr(proofUploadUserError(e));
    } finally {
      setBusy(false);
      if (inputRef.current) inputRef.current.value = "";
    }
  };

  const confirm = async () => {
    const blocked = confirmDeliveredBlockReason(
      proofs, recipientName, localSig.recipient, orderSig, localSig.driver, driverSig,
    );
    if (blocked || !canConfirm) {
      setErr(blocked || "Konfirmasi terkirim belum memenuhi syarat.");
      return;
    }
    setBusy(true);
    setErr("");
    try {
      let latest = order;
      if (localSig.recipient) {
        const res = await api.uploadDeliveryRecipientSignature(latest.id, {
          recipientName,
          signatureDataUrl: localSig.recipient,
        });
        latest = res.order;
        onUpdated(latest);
      }
      if (localSig.driver) {
        const res = await api.uploadDeliveryDriverSignature(latest.id, {
          signatureDataUrl: localSig.driver,
        });
        latest = res.order;
        onUpdated(latest);
      }
      const res = await api.confirmDelivered(order.id, { action_id: newActionId() });
      onUpdated(res.order);
      setLocalSig({ recipient: null, driver: null });
    } catch (e) {
      setErr(formatApiError(e, proofUploadUserError(e)));
    } finally {
      setBusy(false);
    }
  };

  return (
    <section data-testid="mobile-ws-delivery">
      <div className="mop-ws-header"><h2>PENGIRIMAN</h2></div>
      <div className="mop-file">
        <span>Customer</span>
        <strong className="mop-card-title" style={{ display: "block" }} data-testid="mobile-delivery-customer">{getCanonicalCustomerName(order)}</strong>
        <p>{address || "Alamat tidak tersedia"}</p>
        {maps ? (
          <a className="mop-btn-primary" style={{ display: "flex", alignItems: "center", justifyContent: "center", textDecoration: "none" }} href={maps} target="_blank" rel="noreferrer">
            Buka Maps
          </a>
        ) : null}
      </div>
      <h3>Bukti Pengiriman</h3>
      <div className="mop-photos">
        {proofs.map((p, i) => (
          <div className="mop-photo" key={p.id || i}>
            {p.storage_path ? <img src={fileUrl(p.storage_path)} alt={`Foto ${i + 1}`} /> : `Foto ${i + 1}`}
          </div>
        ))}
        {canEdit && !delivered && proofs.length < 5 && (
          <button type="button" className="mop-photo-add" onClick={() => inputRef.current?.click()} data-testid="mobile-add-photo">
            {proofs.length < MIN_DELIVERY_PROOFS ? `Foto ${proofs.length + 1}` : "Tambah Foto"}
          </button>
        )}
      </div>
      <input ref={inputRef} type="file" accept="image/*" capture="environment" hidden onChange={(e) => addPhoto(e.target.files?.[0])} />
      <label className="mop-field">
        <span>Nama Penerima</span>
        <input value={recipientName} onChange={(e) => setRecipientName(e.target.value)} disabled={!canEdit || delivered} data-testid="mobile-recipient-name" />
      </label>
      <div className="mop-file">
        <strong>TTD PENERIMA</strong>
        {(localSig.recipient || orderSig?.signature_storage_path) ? <p>TTD Tersimpan ✓</p> : null}
        <button type="button" className="mop-btn-ghost" style={{ width: "100%", marginTop: 8 }} disabled={!canEdit || delivered} onClick={() => setModal({ open: true, role: "recipient" })}>
          Buat TTD Penerima
        </button>
      </div>
      <div className="mop-file">
        <strong>TTD DRIVER</strong>
        {(localSig.driver || driverSig?.signature_storage_path || driverSig?.driver_signature_storage_path) ? <p>TTD Tersimpan ✓</p> : null}
        <button type="button" className="mop-btn-ghost" style={{ width: "100%", marginTop: 8 }} disabled={!canEdit || delivered} onClick={() => setModal({ open: true, role: "driver" })}>
          Buat TTD Driver
        </button>
      </div>
      {err && <div className="mop-error">{err}</div>}
      <button type="button" className="mop-btn-primary" disabled={!canConfirm || busy} onClick={confirm} data-testid="mobile-confirm-delivered">
        Konfirmasi Terkirim
      </button>
      <MobileSignatureModal
        open={modal.open}
        role={modal.role}
        readOnly={delivered}
        onCancel={() => setModal({ open: false, role: null })}
        onSave={(dataUrl) => {
          setLocalSig((cur) => applyLocalSignature(cur, modal.role, dataUrl));
          setModal({ open: false, role: null });
        }}
      />
    </section>
  );
}
