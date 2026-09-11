import { useEffect, useState } from "react";
import { useLocation, useNavigate, useParams, useSearchParams } from "react-router-dom";
import api from "../lib/api";
import { useAuth } from "../lib/auth";
import { getCanonicalCustomerName } from "../lib/canonicalCustomer";
import { formatDeadline } from "../lib/constants";
import { poCardPresentation, selectMobileWorkspace } from "../lib/mobileWorkspaces";
import { resolvePoBackTarget, storageUserKey } from "../lib/poListReturn";
import MobileTopBar from "./MobileTopBar";
import MobileStageFlow from "./components/MobileStageFlow";
import MobileWorkspaceSelect from "./components/MobileWorkspaceSelect";
import MobileScreenerWorkspace from "./workspaces/MobileScreenerWorkspace";
import MobileHelperPrepWorkspace from "./workspaces/MobileHelperPrepWorkspace";
import MobilePackingWorkspace from "./workspaces/MobilePackingWorkspace";
import MobilePrintWorkspace from "./workspaces/MobilePrintWorkspace";
import MobileSampleWorkspace from "./workspaces/MobileSampleWorkspace";
import MobileLoadingWorkspace from "./workspaces/MobileLoadingWorkspace";
import MobileDeliveryWorkspace from "./workspaces/MobileDeliveryWorkspace";

const WORKSPACE_VIEWS = {
  screener: MobileScreenerWorkspace,
  prep: MobileHelperPrepWorkspace,
  packing: MobilePackingWorkspace,
  print: MobilePrintWorkspace,
  sample: MobileSampleWorkspace,
  loading: MobileLoadingWorkspace,
  delivery: MobileDeliveryWorkspace,
};

export default function MobileOrderDetailPage() {
  const { id } = useParams();
  const [params, setParams] = useSearchParams();
  const navigate = useNavigate();
  const location = useLocation();
  const { user, hasPerm } = useAuth();
  const requested = params.get("ws");
  const choice = selectMobileWorkspace(hasPerm, requested);
  const selected = choice.selected;
  const fromTask = Boolean(location.state?.fromTask || requested);
  const canViewPoSummary = hasPerm("po.view");
  const [order, setOrder] = useState(null);
  const [err, setErr] = useState("");
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);

  const load = async ({ background } = {}) => {
    if (background && order) setRefreshing(true);
    else setLoading(true);
    setErr("");
    try {
      const d = await api.getOrder(id);
      setOrder(d.order || d);
    } catch (e) {
      setErr(e.response?.data?.detail || "Gagal memuat PO.");
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  };

  useEffect(() => {
    load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [id]);

  const onUpdated = (next) => {
    if (next) setOrder(next);
    else load({ background: true });
  };

  const onBack = () => {
    const target = resolvePoBackTarget({
      location,
      userId: storageUserKey(user),
      orderId: id,
      mobileFallback: "/m/tasks",
      webFallback: "/m/tasks",
    });
    navigate(target);
  };

  const card = poCardPresentation(order || {});
  const Workspace = selected ? WORKSPACE_VIEWS[selected.id] : null;

  return (
    <>
      <MobileTopBar
        kicker={fromTask && selected ? `Tugas Saya › ${selected.label}` : "PO"}
        title={order ? getCanonicalCustomerName(order) : "Detail PO"}
        meta={order?.po_number || ""}
        onBack={onBack}
      />
      <main className="mop-content" data-testid="mobile-order-detail">
        {loading && !order && <p className="mop-empty">Memuat PO...</p>}
        {refreshing && <p className="mop-refreshing">Memperbarui...</p>}
        {err && (
          <div className="mop-error">
            {err}
            <button type="button" className="mop-btn-ghost" style={{ marginTop: 8 }} onClick={() => load()}>Coba lagi</button>
          </div>
        )}
        {order && (
          <>
            {canViewPoSummary ? (
              <div className="mop-meta-grid" data-testid="mobile-po-ringkasan">
                <div className="mop-meta-grid-head" data-testid="mobile-po-ringkasan-title">Ringkasan PO</div>
                <div data-testid="mobile-order-customer"><span>Customer</span><strong>{getCanonicalCustomerName(order)}</strong></div>
                <div><span>Product</span><strong>{card.product}</strong></div>
                <div><span>Qty</span><strong>{card.qty != null ? Number(card.qty).toLocaleString("id-ID") : (card.lines.length ? card.lines.map((l) => l.qty).join(" / ") : "—")}</strong></div>
                <div><span>Deadline</span><strong>{formatDeadline(order.deadline) !== "-" ? formatDeadline(order.deadline) : (order.work_date || "—")}</strong></div>
              </div>
            ) : null}
            <MobileStageFlow order={order} />
            {choice.permitted.length > 0 && (
              <MobileWorkspaceSelect
                workspaces={choice.permitted}
                value={selected?.id}
                onChange={(wsId) => setParams({ ws: wsId }, { replace: true })}
              />
            )}
            {choice.unauthorized && <p className="mop-card-meta">Workspace diminta tidak diizinkan. Menampilkan workspace yang tersedia.</p>}
            {Workspace ? (
              <Workspace order={order} canEdit={hasPerm(selected.permission)} onUpdated={onUpdated} />
            ) : (
              <p className="mop-empty">Tidak ada workspace operasional untuk akun ini.</p>
            )}
          </>
        )}
      </main>
    </>
  );
}
