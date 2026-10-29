import { useEffect, useMemo, useRef, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import api from "../lib/api";
import { useAuth } from "../lib/auth";
import { ROLES } from "../lib/constants";
import { sortOperationalTasks } from "../lib/mobileTaskCard";
import {
  DEFAULT_TASK_SUMMARY_FILTER,
  TASK_SUMMARY_FILTERS,
  filterTasksBySummary,
  taskSummaryFromList,
} from "../lib/mobileTaskSummary";
import { mobileLoadingScheduleHref, permittedMobileWorkspaces } from "../lib/mobileWorkspaces";
import {
  clearAllMobileTaskListRetention,
  deleteMobileTaskListRetention,
  isAuthSessionExpiredError,
  readMobileTaskListRetention,
  writeMobileTaskListRetention,
} from "../lib/mobileTaskListRetention";
import MobileTopBar from "./MobileTopBar";
import MobileTaskCard from "./components/MobileTaskCard";
import MobileWorkspaceSelect from "./components/MobileWorkspaceSelect";

export default function MobileTasksPage() {
  const { user, hasPerm } = useAuth();
  const navigate = useNavigate();
  const workspaces = permittedMobileWorkspaces(hasPerm);
  const [params, setParams] = useSearchParams();
  const requestedWs = params.get("ws") || "";
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);
  const [summaryFilter, setSummaryFilter] = useState(DEFAULT_TASK_SUMMARY_FILTER);

  const selected = useMemo(() => {
    if (!workspaces.length) return null;
    return workspaces.find((w) => w.id === requestedWs) || workspaces[0];
  }, [workspaces, requestedWs]);

  const listAuthorized = Boolean(
    user
    && selected
    && selected.id !== "loading"
    && selected.division
    && selected.permission
    && hasPerm(selected.permission),
  );
  const retentionArgs = listAuthorized
    ? {
      user,
      division: selected.division,
      scope: "mine",
      permission: selected.permission,
      hasPerm,
    }
    : null;
  const scopeIdentity = listAuthorized
    ? `${user?.id || ""}|${selected.division}|mine`
    : "";
  const [data, setData] = useState(() => (
    retentionArgs ? readMobileTaskListRetention(retentionArgs) : null
  ));
  const [retainedScope, setRetainedScope] = useState(scopeIdentity);
  const requestRef = useRef(0);

  if (retainedScope !== scopeIdentity) {
    setRetainedScope(scopeIdentity);
    setData(retentionArgs ? readMobileTaskListRetention(retentionArgs) : null);
  }

  // Keep Pengiriman / workspace selection in the URL so browser Back restores it.
  useEffect(() => {
    if (!workspaces.length) return;
    const nextId = selected?.id || workspaces[0].id;
    if (params.get("ws") === nextId) return;
    const next = new URLSearchParams(params);
    next.set("ws", nextId);
    setParams(next, { replace: true });
  }, [workspaces, selected?.id, params, setParams]);

  const setWsId = (id) => {
    if (!id) return;
    if (id === "loading") {
      navigate(mobileLoadingScheduleHref());
      return;
    }
    const next = new URLSearchParams(params);
    next.set("ws", id);
    setParams(next, { replace: true });
  };

  useEffect(() => {
    if (selected?.id !== "loading") return;
    navigate(mobileLoadingScheduleHref(), { replace: true });
  }, [selected?.id, navigate]);

  const load = async (division) => {
    if (!division) return;
    const requestId = ++requestRef.current;
    const current = () => requestId === requestRef.current;
    setBusy(true);
    setErr("");
    try {
      const d = await api.divisionTasks(division, "mine");
      if (!current()) return;
      setData(d);
      writeMobileTaskListRetention({
        user,
        division,
        scope: "mine",
        permission: selected?.division === division ? selected.permission : "",
        hasPerm,
        payload: d,
      });
    } catch (e) {
      if (!current()) return;
      if (isAuthSessionExpiredError(e)) {
        clearAllMobileTaskListRetention();
      } else {
        deleteMobileTaskListRetention({
          user,
          division,
          scope: "mine",
          permission: selected?.division === division ? selected.permission : "",
        });
      }
      setErr(e.response?.data?.detail || "Gagal memuat tugas.");
      setData({ tasks: [] });
    } finally {
      if (current()) setBusy(false);
    }
  };

  useEffect(() => {
    if (selected?.id === "loading") return undefined;
    if (selected?.division) load(selected.division);
    return () => {
      requestRef.current += 1;
    };
    // Cetak and Upload Sampel share division+user+scope, so this does not refetch.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selected?.division, user?.id]);

  const tasks = useMemo(
    () => (Array.isArray(data?.tasks) ? data.tasks : []),
    [data?.tasks],
  );
  const summary = useMemo(() => taskSummaryFromList(tasks), [tasks]);
  const visibleTasks = useMemo(
    () => filterTasksBySummary(tasks, summaryFilter),
    [tasks, summaryFilter],
  );
  const sortedTasks = useMemo(() => sortOperationalTasks(visibleTasks), [visibleTasks]);
  const roleLabel = ROLES[user?.role] || user?.role || "";

  return (
    <>
      <MobileTopBar
        kicker="ETERNAL PACKAGING"
        title="Tugas"
        subtitle={`${user?.name || user?.email || ""} · ${roleLabel}`.trim()}
      />
      <main className="mop-content" data-testid="mobile-tasks-page">
        <div className="mop-summary" data-testid="mobile-task-summary" role="group" aria-label="Filter tugas">
          {TASK_SUMMARY_FILTERS.map((item) => {
            const selected = summaryFilter === item.id;
            return (
              <button
                key={item.id}
                type="button"
                className={`mop-summary-cell${selected ? " is-selected" : ""}`}
                aria-pressed={selected}
                data-testid={`mobile-task-filter-${item.id}`}
                onClick={() => setSummaryFilter(item.id)}
              >
                <span>{item.label}</span>
                <strong>{summary[item.id]}</strong>
              </button>
            );
          })}
        </div>
        {workspaces.length > 0 && (
          <MobileWorkspaceSelect
            workspaces={workspaces}
            value={selected?.id}
            onChange={setWsId}
          />
        )}
        {busy && !tasks.length && <p className="mop-empty">Memuat tugas...</p>}
        {err && <div className="mop-error" role="alert">{err}</div>}
        {!err && data?.completeness === "incomplete" && (
          <p className="mop-empty" data-testid="mobile-tasks-incomplete" role="status">
            Antrean belum lengkap. Batas baca {data.read_limit}.
          </p>
        )}
        {!workspaces.length && (
          <p className="mop-empty">
            Tidak ada workspace operasional untuk akun ini. Pastikan role Anda memiliki akses tugas produksi.
          </p>
        )}
        {selected && sortedTasks.map((task) => (
          <MobileTaskCard key={task.task_id || task.order_id} task={task} workspaceId={selected.id} />
        ))}
        {selected && !busy && !sortedTasks.length && !err && (
          <p className="mop-empty">Tidak ada tugas di filter ini. Coba ubah ringkasan atau workspace.</p>
        )}
      </main>
    </>
  );
}

