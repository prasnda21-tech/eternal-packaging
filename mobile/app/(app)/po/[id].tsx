import React, { useCallback, useMemo, useState } from "react";
import {
  Alert,
  Image,
  Pressable,
  ScrollView,
  StyleSheet,
  Text,
  View,
} from "react-native";
import { Stack, useLocalSearchParams } from "expo-router";
import { useFocusEffect } from "expo-router";
import * as WebBrowser from "expo-web-browser";
import { useAuth } from "../../../src/auth/AuthContext";
import { eternalApi } from "../../../src/api/eternal";
import { uriToDataUrl } from "../../../src/utils/dataUrl";
import {
  assetFileMeta,
  pickFromCamera,
  pickFromGallery,
} from "../../../src/utils/mediaPicker";
import {
  EmptyState,
  ErrorState,
  GhostButton,
  LoadingState,
  MetricStrip,
  PrimaryButton,
  QtyField,
} from "../../../src/components/Ui";
import { colors, space, typography, ui } from "../../../src/theme";
import { apiErrorMessage, fmtQty, newActionId } from "../../../src/utils/format";
import {
  defaultWorkDivision,
  permittedWorkDivisions,
} from "../../../src/utils/permissions";
import {
  canConfirmDelivered,
  helperModel,
  MIN_DELIVERY_PROOFS,
  operatorCanSubmit,
  operatorMyLines,
  screenerModel,
} from "../../../src/utils/workModels";

export default function PoWorkScreen() {
  const { id, divisi } = useLocalSearchParams<{ id: string; divisi?: string }>();
  const { user, hasPerm, token } = useAuth();
  const divisions = useMemo(() => permittedWorkDivisions(hasPerm), [hasPerm]);
  const [activeDiv, setActiveDiv] = useState<string | null>(
    () => defaultWorkDivision(hasPerm, divisi || null)
  );
  const [order, setOrder] = useState<Record<string, any> | null>(null);
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);
  const [drafts, setDrafts] = useState<Record<string, string>>({});
  const [actionErr, setActionErr] = useState("");

  const load = useCallback(async () => {
    if (!id) return;
    setLoading(true);
    setErr("");
    setActionErr("");
    try {
      const res = await eternalApi.getOrder(String(id));
      setOrder(res.order);
    } catch (e) {
      setErr(apiErrorMessage(e, "Gagal memuat PO"));
      setOrder(null);
    } finally {
      setLoading(false);
    }
  }, [id]);

  useFocusEffect(
    useCallback(() => {
      load();
    }, [load])
  );

  const division = activeDiv || defaultWorkDivision(hasPerm, divisi || null);

  const onConfirmQty = async (payload: {
    kind: "screener" | "helper" | "operator";
    qty: number;
    lineIdx?: number;
    splitId?: string;
  }) => {
    if (!order || busy) return;
    if (!payload.qty) {
      setActionErr("Qty wajib diisi.");
      return;
    }
    setBusy(true);
    setActionErr("");
    const actionId = newActionId();
    try {
      let res;
      if (payload.kind === "screener") {
        res = await eternalApi.updateScreenerInfo(order.id, { qty: payload.qty, action_id: actionId });
      } else if (payload.kind === "helper") {
        res = await eternalApi.updateHelperPrepItems(order.id, [
          { line_idx: Number(payload.lineIdx), qty: payload.qty, action_id: actionId },
        ]);
      } else {
        if (!payload.splitId) throw new Error("Assignment operator tidak ditemukan");
        res = await eternalApi.updateOperatorProgress(
          order.id,
          payload.splitId,
          payload.qty,
          actionId
        );
      }
      setOrder(res.order);
      setDrafts({});
      Alert.alert("Tersimpan", "Pekerjaan berhasil dikonfirmasi.");
    } catch (e) {
      setActionErr(apiErrorMessage(e, "Gagal menyimpan"));
    } finally {
      setBusy(false);
    }
  };

  const pickAsset = async (source: "camera" | "gallery") => {
    const result = source === "camera" ? await pickFromCamera() : await pickFromGallery();
    if (result.status === "cancel") return null;
    if (result.status === "denied") {
      setActionErr(result.message);
      return null;
    }
    return result.asset;
  };

  const onPickProof = async (source: "camera" | "gallery") => {
    if (!order || busy) return;
    setBusy(true);
    setActionErr("");
    try {
      const asset = await pickAsset(source);
      if (!asset) return;
      const { name, mime } = assetFileMeta(asset, "bukti");
      const res = await eternalApi.uploadDeliveryProof(order.id, asset.uri, name, mime);
      setOrder(res.order);
      Alert.alert(
        "Foto terunggah",
        "Upload foto tidak menandai terkirim. Tekan Konfirmasi Terkirim setelah minimal 2 foto."
      );
    } catch (e) {
      setActionErr(apiErrorMessage(e, "Gagal mengunggah foto"));
    } finally {
      setBusy(false);
    }
  };

  const onPickSample = async (source: "camera" | "gallery") => {
    if (!order || busy) return;
    if (!hasPerm("operator.sample_upload")) {
      setActionErr("Tidak punya izin upload sampel cetak.");
      return;
    }
    setBusy(true);
    setActionErr("");
    try {
      const asset = await pickAsset(source);
      if (!asset) return;
      const { name, mime } = assetFileMeta(asset, "sampel");
      const dataUrl = await uriToDataUrl(asset.uri, mime);
      const res = await eternalApi.addAttachment(String(order.id), {
        name,
        data_url: dataUrl,
        kind: "sample",
      });
      setOrder(res.order);
      Alert.alert("Tersimpan", "Sampel cetak berhasil diunggah.");
    } catch (e) {
      setActionErr(apiErrorMessage(e, "Gagal mengunggah sampel cetak"));
    } finally {
      setBusy(false);
    }
  };

  const onConfirmDelivered = async () => {
    if (!order || busy) return;
    const proofs = order.delivery_proofs || [];
    if (!canConfirmDelivered(proofs)) {
      setActionErr(`Minimal ${MIN_DELIVERY_PROOFS} foto bukti sebelum konfirmasi terkirim.`);
      return;
    }
    setBusy(true);
    setActionErr("");
    try {
      const res = await eternalApi.confirmDelivered(order.id, newActionId());
      setOrder(res.order);
      Alert.alert("Terkirim", "Pengiriman dikonfirmasi.");
    } catch (e) {
      setActionErr(apiErrorMessage(e, "Gagal konfirmasi terkirim"));
    } finally {
      setBusy(false);
    }
  };

  const openSuratJalan = async (fileId: string) => {
    if (!hasPerm("delivery_note.view_file")) {
      setActionErr("Tidak punya izin melihat file Surat Jalan.");
      return;
    }
    const url = eternalApi.suratJalanFileUrl(String(order?.id), fileId, token);
    await WebBrowser.openBrowserAsync(url);
  };

  if (loading) return <LoadingState label="Memuat pekerjaan…" />;
  if (err) return <ErrorState message={err} onRetry={load} />;
  if (!order) return <EmptyState message="PO tidak ditemukan." />;
  if (!division) {
    return (
      <EmptyState message="Akun ini tidak punya permission pekerjaan divisi pada mobile v1." />
    );
  }

  return (
    <ScrollView style={ui.screen} contentContainerStyle={{ padding: space.md, paddingBottom: 48 }}>
      <Stack.Screen options={{ title: order.po_number || "Pekerjaan PO" }} />

      <View style={ui.card}>
        <Text style={typography.label}>No PO</Text>
        <Text style={typography.title}>{order.po_number}</Text>
        <Text style={[typography.subtitle, { marginTop: 4 }]}>
          {order.customer_name || order.customer || "—"}
        </Text>
        {order.hold ? (
          <View style={[ui.chip, { backgroundColor: colors.warnBg, marginTop: 8 }]}>
            <Text style={[ui.chipText, { color: colors.warn }]}>HOLD</Text>
          </View>
        ) : null}
      </View>

      {divisions.length > 1 ? (
        <View style={styles.tabs}>
          {divisions.map((d) => {
            const active = division === d.id;
            return (
              <Pressable
                key={d.id}
                onPress={() => setActiveDiv(d.id)}
                style={[styles.tab, active && styles.tabActive]}
              >
                <Text style={[styles.tabText, active && styles.tabTextActive]}>{d.label}</Text>
              </Pressable>
            );
          })}
        </View>
      ) : null}

      {actionErr ? (
        <View style={[ui.errorBox, { marginBottom: space.sm }]}>
          <Text style={ui.errorText}>{actionErr}</Text>
        </View>
      ) : null}

      {division === "screener" && hasPerm("screen.update") ? (
        <ScreenerPanel
          order={order}
          draft={drafts.screener || ""}
          setDraft={(v) => setDrafts({ screener: v })}
          busy={busy}
          onConfirm={(qty) => onConfirmQty({ kind: "screener", qty })}
        />
      ) : null}

      {division === "helper" && hasPerm("helper.prepare") ? (
        <HelperPanel
          order={order}
          drafts={drafts}
          setDraft={(key, v) => setDrafts((p) => ({ ...p, [key]: v }))}
          busy={busy}
          onConfirm={(lineIdx, qty) => onConfirmQty({ kind: "helper", qty, lineIdx })}
        />
      ) : null}

      {division === "operator" && hasPerm("operator.update") ? (
        <OperatorPanel
          order={order}
          user={user}
          hasPerm={hasPerm}
          drafts={drafts}
          setDraft={(key, v) => setDrafts((p) => ({ ...p, [key]: v }))}
          busy={busy}
          onConfirm={(splitId, qty) => onConfirmQty({ kind: "operator", qty, splitId })}
          onPickSample={onPickSample}
        />
      ) : null}

      {division === "terkirim" && hasPerm("delivery.update") ? (
        <DriverPanel
          order={order}
          token={token}
          hasPerm={hasPerm}
          busy={busy}
          onPickProof={onPickProof}
          onConfirmDelivered={onConfirmDelivered}
          onOpenSj={openSuratJalan}
        />
      ) : null}

      <GhostButton title="Muat ulang" onPress={load} style={{ marginTop: space.md }} />
    </ScrollView>
  );
}

function ScreenerPanel({
  order,
  draft,
  setDraft,
  busy,
  onConfirm,
}: {
  order: any;
  draft: string;
  setDraft: (v: string) => void;
  busy: boolean;
  onConfirm: (qty: number) => void;
}) {
  const model = screenerModel(order);
  return (
    <View style={ui.card} testID="screener-panel">
      <Text style={typography.title}>{model.title}</Text>
      <Text style={typography.subtitle}>{model.itemLabel}</Text>
      <MetricStrip
        target={fmtQty(model.target)}
        done={fmtQty(model.done)}
        remain={fmtQty(model.remaining)}
        status={model.status}
        showRemain={false}
      />
      {model.gateLocked ? (
        <Text style={[typography.subtitle, { color: colors.warn }]}>{model.gateMsg}</Text>
      ) : (
        <>
          <QtyField label="Qty Screener" value={draft} onChange={setDraft} testID="screener-qty" />
          <PrimaryButton
            title="Konfirmasi Screener"
            busy={busy}
            onPress={() => onConfirm(Number(draft || 0))}
            style={{ marginTop: space.md }}
          />
        </>
      )}
    </View>
  );
}

function HelperPanel({
  order,
  drafts,
  setDraft,
  busy,
  onConfirm,
}: {
  order: any;
  drafts: Record<string, string>;
  setDraft: (key: string, v: string) => void;
  busy: boolean;
  onConfirm: (lineIdx: number, qty: number) => void;
}) {
  const model = helperModel(order);
  return (
    <View style={ui.card} testID="helper-panel">
      <Text style={typography.title}>{model.title}</Text>
      <MetricStrip
        target={fmtQty(model.target)}
        done={fmtQty(model.done)}
        remain={fmtQty(model.remaining)}
        status={model.status}
      />
      {model.gateLocked ? (
        <Text style={[typography.subtitle, { color: colors.warn }]}>{model.gateMsg}</Text>
      ) : null}
      {model.lines.map((line: any) => (
        <View key={line.lineIdx} style={styles.lineBlock}>
          <Text style={typography.body}>
            {line.name}
            {line.color ? ` · ${line.color}` : ""}
          </Text>
          <Text style={typography.subtitle}>
            Target {fmtQty(line.target)} · Selesai {fmtQty(line.done)} · Sisa {fmtQty(line.remaining)}
          </Text>
          {!model.gateLocked ? (
            <>
              <QtyField
                label="Qty Prepared"
                value={drafts[`h-${line.lineIdx}`] || ""}
                onChange={(v) => setDraft(`h-${line.lineIdx}`, v)}
              />
              <PrimaryButton
                title="Konfirmasi Helper"
                busy={busy}
                disabled={line.remaining <= 0}
                onPress={() => onConfirm(line.lineIdx, Number(drafts[`h-${line.lineIdx}`] || 0))}
                style={{ marginTop: space.sm }}
              />
            </>
          ) : null}
        </View>
      ))}
      {!model.lines.length ? <Text style={typography.subtitle}>Tidak ada item cetak.</Text> : null}
    </View>
  );
}

function OperatorPanel({
  order,
  user,
  hasPerm,
  drafts,
  setDraft,
  busy,
  onConfirm,
  onPickSample,
}: {
  order: any;
  user: any;
  hasPerm: (p: string) => boolean;
  drafts: Record<string, string>;
  setDraft: (key: string, v: string) => void;
  busy: boolean;
  onConfirm: (splitId: string, qty: number) => void;
  onPickSample: (source: "camera" | "gallery") => void;
}) {
  const lines = operatorMyLines(order, user);
  const target = lines.reduce((s, l) => s + l.target, 0);
  const done = lines.reduce((s, l) => s + l.done, 0);
  const remain = Math.max(0, target - done);
  const gate = operatorCanSubmit(order);
  const canUploadSample = hasPerm("operator.sample_upload");
  const sampleReady = order?.workflow_ready?.sample_upload === true;
  const sampleLock =
    order?.screener_gate_reason || "Qty Screener harus diisi sebelum Cetak Sample.";
  const samples = Array.isArray(order?.sample_files) ? order.sample_files : [];
  return (
    <View style={ui.card} testID="operator-panel">
      <Text style={typography.title}>Pekerjaan Operator</Text>
      <Text style={typography.subtitle}>Assignment pribadi Anda</Text>
      <MetricStrip
        target={fmtQty(target)}
        done={fmtQty(done)}
        remain={fmtQty(remain)}
        status={done <= 0 ? "Belum" : remain <= 0 ? "Selesai" : "Proses"}
      />
      {!gate.ok ? (
        <Text style={[typography.subtitle, { color: colors.warn }]}>{gate.reason}</Text>
      ) : null}
      {!lines.length ? (
        <Text style={typography.subtitle}>Tidak ada assignment operator untuk akun ini pada PO ini.</Text>
      ) : null}
      {lines.map((line) => (
        <View key={line.split.id} style={styles.lineBlock}>
          <Text style={typography.body}>
            {line.name}
            {line.color ? ` · ${line.color}` : ""}
          </Text>
          <Text style={typography.subtitle}>
            Target {fmtQty(line.target)} · Selesai {fmtQty(line.done)} · Sisa {fmtQty(line.remaining)}
          </Text>
          {gate.ok ? (
            <>
              <QtyField
                label="Qty Cetak"
                value={drafts[`o-${line.split.id}`] || ""}
                onChange={(v) => setDraft(`o-${line.split.id}`, v)}
              />
              <PrimaryButton
                title="Konfirmasi Operator"
                busy={busy}
                disabled={line.remaining <= 0}
                onPress={() =>
                  onConfirm(line.split.id, Number(drafts[`o-${line.split.id}`] || 0))
                }
                style={{ marginTop: space.sm }}
              />
            </>
          ) : null}
        </View>
      ))}

      {canUploadSample ? (
        <View style={styles.lineBlock} testID="sample-upload-panel">
          <Text style={typography.label}>Sampel Cetak</Text>
          <Text style={typography.subtitle}>Foto sample cetak — terpisah dari File Design.</Text>
          <View style={styles.proofGrid}>
            {samples.map((f: any, i: number) => {
              const uri = f.data_url || f.url || null;
              return (
                <View key={f.id || i} style={styles.proofThumb}>
                  {uri ? (
                    <Image source={{ uri }} style={styles.proofImg} />
                  ) : (
                    <Text style={typography.subtitle}>{f.name || `Sampel ${i + 1}`}</Text>
                  )}
                </View>
              );
            })}
          </View>
          {!sampleReady ? (
            <Text style={[typography.subtitle, { color: colors.warn }]}>{sampleLock}</Text>
          ) : (
            <>
              <PrimaryButton
                title="Ambil Foto Sampel"
                onPress={() => onPickSample("camera")}
                busy={busy}
                testID="sample-camera"
                style={{ marginTop: space.sm }}
              />
              <GhostButton
                title="Pilih dari Galeri"
                onPress={() => onPickSample("gallery")}
                disabled={busy}
                testID="sample-gallery"
                style={{ marginTop: 8 }}
              />
            </>
          )}
        </View>
      ) : null}
    </View>
  );
}

function DriverPanel({
  order,
  token,
  hasPerm,
  busy,
  onPickProof,
  onConfirmDelivered,
  onOpenSj,
}: {
  order: any;
  token: string | null;
  hasPerm: (p: string) => boolean;
  busy: boolean;
  onPickProof: (source: "camera" | "gallery") => void;
  onConfirmDelivered: () => void;
  onOpenSj: (fileId: string) => void;
}) {
  const proofs = order.delivery_proofs || [];
  const sjFiles = Array.isArray(order.surat_jalan_files) ? order.surat_jalan_files : [];
  const delivered = order.delivery_status === "Terkirim" || order.terkirim_confirmed;
  return (
    <View style={ui.card} testID="driver-panel">
      <Text style={typography.title}>Pengiriman / Terkirim</Text>
      <Text style={typography.subtitle}>
        Bukti: {proofs.length} foto (min {MIN_DELIVERY_PROOFS}). Upload tidak otomatis menandai terkirim.
      </Text>

      {hasPerm("delivery_note.view") || hasPerm("delivery_note.view_file") ? (
        <View style={{ marginTop: space.md }}>
          <Text style={typography.label}>Surat Jalan</Text>
          {!sjFiles.length ? (
            <Text style={typography.subtitle}>Belum ada file Surat Jalan.</Text>
          ) : (
            sjFiles.map((f: any) => (
              <GhostButton
                key={f.id || f.storage_path}
                title={f.name || f.original_filename || "Buka Surat Jalan"}
                onPress={() => f.id && onOpenSj(f.id)}
                disabled={!hasPerm("delivery_note.view_file") || !f.id}
                style={{ marginTop: 8 }}
              />
            ))
          )}
        </View>
      ) : null}

      <View style={{ marginTop: space.md }}>
        <Text style={typography.label}>Bukti Pengiriman</Text>
        <View style={styles.proofGrid}>
          {proofs.map((p: any, i: number) => {
            const uri =
              p.storage_path && token
                ? eternalApi.fileUrl(p.storage_path, token)
                : null;
            return (
              <View key={p.id || i} style={styles.proofThumb}>
                {uri ? (
                  <Image source={{ uri }} style={styles.proofImg} />
                ) : (
                  <Text style={typography.subtitle}>Foto {i + 1}</Text>
                )}
              </View>
            );
          })}
        </View>
        {!delivered ? (
          <>
            <PrimaryButton
              title="Ambil Foto"
              onPress={() => onPickProof("camera")}
              busy={busy}
              testID="proof-camera"
              style={{ marginTop: space.sm }}
            />
            <GhostButton
              title="Pilih dari Galeri"
              onPress={() => onPickProof("gallery")}
              disabled={busy}
              testID="proof-gallery"
              style={{ marginTop: 8 }}
            />
          </>
        ) : null}
      </View>

      {!delivered ? (
        <PrimaryButton
          title="Konfirmasi Terkirim"
          onPress={onConfirmDelivered}
          busy={busy}
          disabled={!canConfirmDelivered(proofs)}
          style={{ marginTop: space.md }}
        />
      ) : (
        <View style={[ui.chip, { backgroundColor: colors.okBg, marginTop: space.md }]}>
          <Text style={[ui.chipText, { color: colors.ok }]}>Sudah Terkirim</Text>
        </View>
      )}
    </View>
  );
}

const styles = StyleSheet.create({
  tabs: {
    flexDirection: "row",
    flexWrap: "wrap",
    gap: 8,
    marginBottom: space.sm,
  },
  tab: {
    paddingHorizontal: 14,
    paddingVertical: 10,
    borderRadius: 10,
    backgroundColor: colors.surface,
    borderWidth: 1,
    borderColor: colors.line,
    minHeight: 44,
    justifyContent: "center",
  },
  tabActive: {
    backgroundColor: colors.primary,
    borderColor: colors.primary,
  },
  tabText: { fontWeight: "700", color: colors.ink, fontSize: 14 },
  tabTextActive: { color: "#fff" },
  lineBlock: {
    marginTop: space.md,
    paddingTop: space.sm,
    borderTopWidth: 1,
    borderTopColor: colors.line,
  },
  proofGrid: {
    flexDirection: "row",
    flexWrap: "wrap",
    gap: 8,
    marginTop: 8,
  },
  proofThumb: {
    width: 88,
    height: 88,
    borderRadius: 10,
    backgroundColor: colors.bg,
    overflow: "hidden",
    alignItems: "center",
    justifyContent: "center",
  },
  proofImg: { width: "100%", height: "100%" },
});
