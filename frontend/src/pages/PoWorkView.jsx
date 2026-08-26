import { useEffect, useState } from "react";
import { Link, Navigate, useParams, useSearchParams } from "react-router-dom";
import { ArrowLeft, RefreshCw } from "lucide-react";
import api from "../lib/api";
import { useAuth } from "../lib/auth";
import RoleWorkPanel from "../components/RoleWorkPanel";
import {
  defaultDivisionId,
  divisionById,
  fallbackDivision,
  permittedDivisionIds,
  workPanelPath,
} from "../lib/workPanel";
import { TASK_NAV_PATHS } from "../lib/navAccess";

export default function PoWorkView() {
  const { id } = useParams();
  const [params] = useSearchParams();
  const { hasPerm } = useAuth();
  const requested = params.get("divisi") || "";
  const lineRaw = params.get("line");
  const focusLineIdx = lineRaw != null && lineRaw !== "" ? Number(lineRaw) : null;
  const [order, setOrder] = useState(null);
  const [settings, setSettings] = useState(null);
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState("");

  const load = async () => {
    setErr("");
    try {
      const [od, st] = await Promise.all([
        api.getOrder(id),
        api.workPanelSettings().catch(() => ({ settings: { divisions: [] } })),
      ]);
      setOrder(od.order);
      setSettings(st.settings || st);
    } catch (e) {
      const d = e.response?.data?.detail;
      setErr(typeof d === "string" ? d : "Gagal memuat pekerjaan");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { load(); /* eslint-disable-next-line */ }, [id]);

  const permitted = permittedDivisionIds(hasPerm);
  const activeId = defaultDivisionId(hasPerm, requested) || requested || "operator";
  const division = divisionById(settings, activeId) || fallbackDivision(activeId);
  const canView = hasPerm("work_panel.view") || (division.submit_perm && hasPerm(division.submit_perm));

  if (loading) return <div className="loading">Memuat pekerjaan…</div>;
  if (err) return <div className="error" data-testid="po-work-error">{err}</div>;
  if (!order) return null;

  if (!canView) {
    return (
      <div className="page-heading" data-testid="po-work-forbidden">
        <p>Anda tidak punya permission untuk Form Pekerjaan Divisi.</p>
        {hasPerm("po.view") && <Link to={`/po/${order.id}`}>Buka Detail PO</Link>}
      </div>
    );
  }

  if (requested && permitted.length && !permitted.includes(requested)) {
    return <Navigate to={workPanelPath(order.id, permitted[0])} replace />;
  }

  return (
    <div data-testid="po-work-view">
      <div className="page-heading work-page-heading">
        <Link to={TASK_NAV_PATHS[activeId] || "/tasks"} className="ghost sm" data-testid="work-back-schedule">
          <ArrowLeft size={14} /> Tugas per Divisi
        </Link>
        {permitted.length > 1 && (
          <nav className="work-divisi-switch" data-testid="work-divisi-switch">
            {permitted.map((pid) => {
              const row = divisionById(settings, pid) || fallbackDivision(pid);
              const active = pid === activeId;
              return (
                <Link
                  key={pid}
                  to={workPanelPath(order.id, pid)}
                  className={active ? "active" : ""}
                  data-testid={`work-divisi-${pid}`}
                >
                  {row.label}
                </Link>
              );
            })}
          </nav>
        )}
        <button type="button" className="ghost sm" onClick={load} data-testid="work-refresh">
          <RefreshCw size={14} /> Refresh
        </button>
      </div>
      <RoleWorkPanel order={order} division={division} onUpdated={setOrder} focusLineIdx={focusLineIdx} />
    </div>
  );
}
