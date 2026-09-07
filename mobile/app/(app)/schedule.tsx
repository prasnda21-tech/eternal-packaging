import React, { useCallback, useEffect, useMemo, useState } from "react";
import {
  Pressable,
  RefreshControl,
  ScrollView,
  StyleSheet,
  Text,
  TextInput,
  View,
} from "react-native";
import { router, Stack, useFocusEffect, useLocalSearchParams } from "expo-router";
import { useAuth } from "../../src/auth/AuthContext";
import { DivisionTask, eternalApi, ProductionRow } from "../../src/api/eternal";
import { EmptyState, ErrorState, GhostButton, LoadingState } from "../../src/components/Ui";
import { SectionCardHeader, workContextHeader } from "../../src/components/SectionCardHeader";
import TaskWorkspaceMenu from "../../src/components/TaskWorkspaceMenu";
import { colors, space, typography, ui } from "../../src/theme";
import { apiErrorMessage, fmtDate, fmtQty } from "../../src/utils/format";
import {
  canApproveDesign,
  canUploadSample,
  canViewSchedule,
  permittedWorkDivisions,
} from "../../src/utils/permissions";
import { isActionableDivisionTask, taskBelongsToWorkspace } from "../../src/utils/taskEligibility";
import {
  availableTaskWorkspaces,
  persistSelectedWorkspace,
  taskEmptyMessage,
  tasksForSelectedWorkspace,
} from "../../src/utils/taskWorkspaces";

type TaskCard = {
  key: string;
  orderId: string;
  po: string;
  customer: string;
  item: string;
  workDate?: string | null;
  qtyLabel: string;
  status: string;
  hold?: boolean;
  division?: string;
  actionLabel?: string;
};

type ScheduleRow = {
  key: string;
  orderId: string;
  tanggal: string;
  po: string;
  customer: string;
  outlet: string;
  qty: string;
  status: string;
  hold?: boolean;
};

const COL = {
  tanggal: 88,
  po: 118,
  customer: 140,
  outlet: 130,
  qty: 88,
  status: 100,
} as const;

const TABLE_MIN_WIDTH =
  COL.tanggal + COL.po + COL.customer + COL.outlet + COL.qty + COL.status;

function taskCard(t: DivisionTask): TaskCard {
  const qtyParts: string[] = [];
  if (t.target_qty != null) qtyParts.push(`Tgt ${fmtQty(t.target_qty)}`);
  if (t.completed_qty != null) qtyParts.push(`Akt ${fmtQty(t.completed_qty)}`);
  return {
    key: t.task_id,
    orderId: t.order_id,
    po: t.order_number || "—",
    customer: t.customer_name || "—",
    item: t.item || "—",
    workDate: t.work_date,
    qtyLabel: qtyParts.join(" · ") || "—",
    status: t.hold ? "HOLD" : t.status_label || t.status || "—",
    hold: !!t.hold,
    division: t.division,
    actionLabel: t.action_label || "Kerjakan",
  };
}

function mergeTaskCards(tasks: DivisionTask[]): TaskCard[] {
  return tasks.map(taskCard);
}

function scheduleRow(r: ProductionRow): ScheduleRow {
  const qty =
    r.operator_assigned_qty != null && Number(r.operator_assigned_qty) > 0
      ? Number(r.operator_assigned_qty)
      : r.print_qty_total != null && Number(r.print_qty_total) > 0
        ? Number(r.print_qty_total)
        : r.total_quantity;
  return {
    key: r.id,
    orderId: r.id,
    tanggal: fmtDate(r.work_date),
    po: r.po_number || "—",
    customer: r.customer || "—",
    outlet: r.outlet || "—",
    qty: qty != null ? fmtQty(qty) : "—",
    status: r.hold ? "HOLD" : r.status_final || r.status_label || "—",
    hold: !!r.hold,
  };
}

function HeaderCell({ label, width }: { label: string; width: number }) {
  return (
    <View style={[styles.cell, { width }]}>
      <Text style={styles.headerText} numberOfLines={1}>
        {label}
      </Text>
    </View>
  );
}

function BodyCell({
  value,
  width,
  mono,
  warn,
}: {
  value: string;
  width: number;
  mono?: boolean;
  warn?: boolean;
}) {
  return (
    <View style={[styles.cell, { width }]}>
      <Text
        style={[styles.cellText, mono && styles.monoText, warn && styles.warnText]}
        numberOfLines={2}
      >
        {value}
      </Text>
    </View>
  );
}

export default function ScheduleScreen() {
  const { user, hasPerm, logout } = useAuth();
  const { ws } = useLocalSearchParams<{ ws?: string }>();
  const divisions = useMemo(() => permittedWorkDivisions(hasPerm), [hasPerm]);
  const workspaces = useMemo(() => availableTaskWorkspaces(hasPerm), [hasPerm]);
  const workspaceIds = useMemo(() => workspaces.map((w) => w.id), [workspaces]);
  const [activeDiv, setActiveDiv] = useState<string>(() =>
    persistSelectedWorkspace(Array.isArray(ws) ? ws[0] : ws, workspaceIds)
  );
  const [taskCards, setTaskCards] = useState<TaskCard[]>([]);
  const [scheduleRows, setScheduleRows] = useState<ScheduleRow[]>([]);
  const [loadingTasks, setLoadingTasks] = useState(true);
  const [loadingSchedule, setLoadingSchedule] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [taskErr, setTaskErr] = useState("");
  const [scheduleErr, setScheduleErr] = useState("");
  const [query, setQuery] = useState("");

  const canSchedule = canViewSchedule(hasPerm) && hasPerm("schedule.view");

  const loadTasks = useCallback(async () => {
    setTaskErr("");
    try {
      const cards: TaskCard[] = [];
      const results = await Promise.all(
        divisions.map(async (d) => {
          try {
            return await eternalApi.divisionTasks(d.id, "mine");
          } catch {
            return { tasks: [] as DivisionTask[] };
          }
        })
      );
      results.forEach((res, i) => {
        const workspaceId = divisions[i]?.id;
        cards.push(
          ...mergeTaskCards(
            (res.tasks || [])
              .map((t) => (workspaceId ? { ...t, division: workspaceId } : t))
              .filter((t) => isActionableDivisionTask(t) && taskBelongsToWorkspace(t, workspaceId))
          )
        );
      });
      if (canApproveDesign(hasPerm) || canUploadSample(hasPerm)) {
        try {
          const ac = await eternalApi.actionItems();
          if (canApproveDesign(hasPerm)) {
            const design = (ac.actions || []).find((a) => a.key === "design_approve");
            for (const o of design?.items || []) {
              cards.push({
                key: `design_approve:${o.id}`,
                orderId: o.id,
                po: o.po_number || "—",
                customer: o.customer || "—",
                item: o.item || "Approve Design",
                qtyLabel: o.design_status || "Waiting",
                status: o.design_status || "Waiting",
                division: "design_approve",
                actionLabel: "Approve Design",
              });
            }
          }
          if (canUploadSample(hasPerm)) {
            const sample = (ac.actions || []).find((a) => a.key === "sample_upload");
            for (const o of sample?.items || []) {
              cards.push({
                key: `sample_upload:${o.id}`,
                orderId: o.id,
                po: o.po_number || "—",
                customer: o.customer || "—",
                item: o.item || "Upload Sampel Cetak",
                qtyLabel: "Sampel",
                status: "Siap",
                division: "sample_upload",
                actionLabel: "Upload Sampel Cetak",
              });
            }
          }
        } catch {
          /* action-items optional; division tasks still shown */
        }
      }
      setTaskCards(cards);
    } catch (e) {
      setTaskCards([]);
      setTaskErr(apiErrorMessage(e, "Gagal memuat tugas"));
    }
  }, [divisions, hasPerm]);

  const loadSchedule = useCallback(async () => {
    setScheduleErr("");
    if (!canSchedule) {
      setScheduleRows([]);
      return;
    }
    try {
      const res = await eternalApi.productionSchedule();
      setScheduleRows((res.rows || []).map(scheduleRow));
    } catch (e) {
      setScheduleRows([]);
      setScheduleErr(apiErrorMessage(e, "Gagal memuat jadwal produksi"));
    }
  }, [canSchedule]);

  const loadAll = useCallback(
    async (isRefresh = false) => {
      if (isRefresh) setRefreshing(true);
      else {
        setLoadingTasks(true);
        setLoadingSchedule(true);
      }
      try {
        await Promise.all([
          loadTasks().finally(() => setLoadingTasks(false)),
          loadSchedule().finally(() => setLoadingSchedule(false)),
        ]);
      } finally {
        setRefreshing(false);
      }
    },
    [loadTasks, loadSchedule]
  );

  useEffect(() => {
    setActiveDiv((prev) => persistSelectedWorkspace(prev || (Array.isArray(ws) ? ws[0] : ws), workspaceIds));
  }, [workspaceIds, ws]);

  useFocusEffect(
    useCallback(() => {
      loadAll();
    }, [loadAll])
  );

  const filteredTasks = useMemo(() => {
    const q = query.trim().toLowerCase();
    return tasksForSelectedWorkspace(taskCards, activeDiv).filter((r) => {
      if (!q) return true;
      return (
        r.po.toLowerCase().includes(q) ||
        r.customer.toLowerCase().includes(q) ||
        r.item.toLowerCase().includes(q)
      );
    });
  }, [taskCards, query, activeDiv]);

  const selectWorkspace = (id: string) => {
    setActiveDiv(id);
    router.setParams({ ws: id });
  };

  const filteredSchedule = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return scheduleRows;
    return scheduleRows.filter(
      (r) =>
        r.po.toLowerCase().includes(q) ||
        r.customer.toLowerCase().includes(q) ||
        r.outlet.toLowerCase().includes(q) ||
        r.status.toLowerCase().includes(q)
    );
  }, [scheduleRows, query]);

  const onLogout = async () => {
    await logout();
    router.replace("/login");
  };

  const openWork = (orderId: string, division?: string) => {
    const task = division && workspaceIds.includes(division) ? division : activeDiv;
    router.push({
      pathname: "/(app)/po/[id]",
      params: { id: orderId, divisi: task || "", from: "task", ws: activeDiv || "" },
    });
  };

  const openScheduleRow = (orderId: string) => {
    router.push({
      pathname: "/(app)/po/[id]",
      params: { id: orderId },
    });
  };

  return (
    <View style={ui.screen} testID="schedule-screen">
      <Stack.Screen
        options={{
          title: "Produksi",
          headerRight: () => (
            <Pressable onPress={onLogout} style={styles.logoutBtn} hitSlop={8}>
              <Text style={styles.logoutText}>Keluar</Text>
            </Pressable>
          ),
        }}
      />

      <ScrollView
        style={{ flex: 1 }}
        refreshControl={
          <RefreshControl
            refreshing={refreshing}
            onRefresh={() => loadAll(true)}
            tintColor={colors.primary}
          />
        }
        contentContainerStyle={{ paddingBottom: 48 }}
      >
        <View style={styles.top}>
          <Text style={typography.subtitle}>
            {user?.name || user?.email} · {user?.role}
          </Text>
          <TextInput
            testID="schedule-search"
            value={query}
            onChangeText={setQuery}
            placeholder="Cari PO / customer"
            placeholderTextColor={colors.muted}
            style={[ui.input, { marginTop: space.sm }]}
          />
        </View>

        {/* ——— TUGAS SAYA (personal cards) ——— */}
        <View style={styles.section} testID="tugas-saya-section">
          <View style={styles.sectionHead}>
            <Text style={typography.title}>Tugas Saya</Text>
            <GhostButton title="Refresh" onPress={() => loadAll(true)} style={styles.refreshBtn} />
          </View>
          <Text style={[typography.subtitle, { marginBottom: space.sm }]} testID="tugas-saya-context">
            {workContextHeader(activeDiv).title
              ? `Sedang di ${workContextHeader(activeDiv).title}`
              : "Pilih menu tugas."}
          </Text>
          <TaskWorkspaceMenu
            workspaces={workspaces}
            selectedId={activeDiv}
            onSelect={selectWorkspace}
          />

          {loadingTasks ? <LoadingState label="Memuat tugas…" /> : null}
          {!loadingTasks && taskErr ? <ErrorState message={taskErr} onRetry={() => loadAll()} /> : null}
          {!loadingTasks && !taskErr && !divisions.length && !canApproveDesign(hasPerm) && !canUploadSample(hasPerm) ? (
            <EmptyState message="Tidak ada divisi kerja yang diizinkan untuk akun ini." />
          ) : null}
          {!loadingTasks && !taskErr && workspaces.length > 0 && filteredTasks.length === 0 ? (
            <EmptyState message={taskEmptyMessage(activeDiv)} />
          ) : null}

          {!loadingTasks && !taskErr
            ? filteredTasks.map((item) => {
                const header = workContextHeader(item.division);
                return (
                <Pressable
                  key={item.key}
                  testID={`task-card-${item.orderId}`}
                  style={ui.cardFlush}
                  onPress={() => openWork(item.orderId, item.division)}
                >
                  <SectionCardHeader tone={header.tone} title={header.title} />
                  <View style={ui.cardBody}>
                  <View style={styles.cardTop}>
                    <Text style={typography.mono}>{item.po}</Text>
                    <View style={[ui.chip, item.hold && { backgroundColor: colors.warnBg }]}>
                      <Text style={[ui.chipText, item.hold && { color: colors.warn }]}>
                        {item.status}
                      </Text>
                    </View>
                  </View>
                  <Text style={[typography.body, { marginTop: 4 }]} numberOfLines={1}>
                    {item.customer}
                  </Text>
                  {item.item ? (
                    <Text style={[typography.subtitle, { marginTop: 2 }]} numberOfLines={2}>
                      {item.item}
                    </Text>
                  ) : null}
                  <View style={styles.cardMeta}>
                    <Text style={typography.subtitle}>Tgl {fmtDate(item.workDate)}</Text>
                    <Text style={typography.subtitle}>{item.qtyLabel}</Text>
                  </View>
                  <Text style={styles.actionHint}>{item.actionLabel || "Kerjakan"} →</Text>
                  </View>
                </Pressable>
                );
              })
            : null}
        </View>

        {/* ——— JADWAL PRODUKSI (shared table) ——— */}
        <View style={styles.section} testID="jadwal-produksi-section">
          <View style={styles.sectionHead}>
            <Text style={typography.title}>Jadwal Produksi</Text>
            {canSchedule ? (
              <GhostButton title="Refresh" onPress={() => loadSchedule()} style={styles.refreshBtn} />
            ) : null}
          </View>
          <Text style={[typography.subtitle, { marginBottom: space.sm }]}>
            Jadwal produksi bersama (read). Aksi tetap mengikuti permission Anda.
          </Text>

          {!canSchedule ? (
            <EmptyState message="Akun ini tidak punya permission schedule.view untuk melihat jadwal." />
          ) : null}

          {canSchedule && loadingSchedule ? <LoadingState label="Memuat jadwal…" /> : null}
          {canSchedule && !loadingSchedule && scheduleErr ? (
            <ErrorState message={scheduleErr} onRetry={() => loadSchedule()} />
          ) : null}
          {canSchedule && !loadingSchedule && !scheduleErr && filteredSchedule.length === 0 ? (
            <EmptyState message="Tidak ada jadwal produksi." />
          ) : null}

          {canSchedule && !loadingSchedule && !scheduleErr && filteredSchedule.length > 0 ? (
            <ScrollView
              horizontal
              showsHorizontalScrollIndicator
              bounces={false}
              contentContainerStyle={{ minWidth: TABLE_MIN_WIDTH }}
            >
              <View style={styles.table} testID="jadwal-table">
                <View style={styles.headerRow}>
                  <HeaderCell label="Tanggal" width={COL.tanggal} />
                  <HeaderCell label="No. PO" width={COL.po} />
                  <HeaderCell label="Customer" width={COL.customer} />
                  <HeaderCell label="Outlet" width={COL.outlet} />
                  <HeaderCell label="Qty" width={COL.qty} />
                  <HeaderCell label="Status" width={COL.status} />
                </View>
                {filteredSchedule.map((item, idx) => (
                  <Pressable
                    key={item.key}
                    testID={`schedule-row-${item.orderId}`}
                    onPress={() => openScheduleRow(item.orderId)}
                    style={[styles.bodyRow, idx % 2 === 1 && styles.bodyRowAlt]}
                    accessibilityRole="button"
                    accessibilityLabel={`Lihat PO ${item.po}`}
                  >
                    <BodyCell value={item.tanggal} width={COL.tanggal} />
                    <BodyCell value={item.po} width={COL.po} mono />
                    <BodyCell value={item.customer} width={COL.customer} />
                    <BodyCell value={item.outlet} width={COL.outlet} />
                    <BodyCell value={item.qty} width={COL.qty} mono />
                    <BodyCell value={item.status} width={COL.status} warn={item.hold} />
                  </Pressable>
                ))}
              </View>
            </ScrollView>
          ) : null}
        </View>
      </ScrollView>
    </View>
  );
}

const styles = StyleSheet.create({
  top: {
    paddingHorizontal: space.md,
    paddingTop: space.sm,
    paddingBottom: space.sm,
    borderBottomWidth: 1,
    borderBottomColor: colors.line,
    backgroundColor: colors.surface,
  },
  section: {
    paddingHorizontal: space.md,
    paddingTop: space.lg,
  },
  sectionHead: {
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "space-between",
    gap: 8,
    marginBottom: 4,
  },
  refreshBtn: {
    minHeight: 40,
    paddingHorizontal: 12,
  },
  cardTop: {
    flexDirection: "row",
    justifyContent: "space-between",
    alignItems: "center",
    gap: 8,
  },
  cardMeta: {
    flexDirection: "row",
    justifyContent: "space-between",
    marginTop: 8,
    gap: 8,
  },
  actionHint: {
    marginTop: 10,
    color: colors.primary,
    fontWeight: "700",
    fontSize: 14,
  },
  table: {
    borderWidth: 1,
    borderColor: colors.line,
    borderRadius: 10,
    overflow: "hidden",
    backgroundColor: colors.surface,
  },
  headerRow: {
    flexDirection: "row",
    backgroundColor: colors.header,
    minHeight: 44,
    alignItems: "center",
  },
  headerText: {
    color: "#F8FAFC",
    fontSize: 12,
    fontWeight: "700",
    letterSpacing: 0.3,
    textTransform: "uppercase",
  },
  bodyRow: {
    flexDirection: "row",
    minHeight: 52,
    alignItems: "center",
    borderTopWidth: StyleSheet.hairlineWidth,
    borderTopColor: colors.line,
    backgroundColor: colors.surface,
  },
  bodyRowAlt: {
    backgroundColor: "#F8FAFC",
  },
  cell: {
    paddingHorizontal: 8,
    paddingVertical: 8,
    justifyContent: "center",
  },
  cellText: {
    color: colors.ink,
    fontSize: 13,
    lineHeight: 18,
  },
  monoText: {
    fontWeight: "700",
    fontVariant: ["tabular-nums"],
  },
  warnText: {
    color: colors.warn,
    fontWeight: "700",
  },
  logoutBtn: {
    paddingHorizontal: 8,
    paddingVertical: 6,
  },
  logoutText: {
    color: "#E2E8F0",
    fontWeight: "700",
    fontSize: 14,
  },
});
