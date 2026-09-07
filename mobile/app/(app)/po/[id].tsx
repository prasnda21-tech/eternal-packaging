import React, { useCallback, useEffect, useMemo, useState } from "react";
import {
  Alert,
  Image,
  KeyboardAvoidingView,
  Platform,
  Pressable,
  ScrollView,
  StyleSheet,
  Text,
  View,
} from "react-native";
import { Stack, useLocalSearchParams } from "expo-router";
import { useFocusEffect } from "expo-router";
import * as ImagePicker from "expo-image-picker";
import * as WebBrowser from "expo-web-browser";
import { useAuth } from "../../../src/auth/AuthContext";
import { eternalApi } from "../../../src/api/eternal";
import {
  EmptyState,
  ErrorState,
  GhostButton,
  LoadingState,
  MetricStrip,
  PrimaryButton,
  QtyField,
} from "../../../src/components/Ui";
import DriverDeliveryPanel from "../../../src/components/DriverDeliveryPanel";
import { SectionCardHeader } from "../../../src/components/SectionCardHeader";
import { colors, space, typography, ui } from "../../../src/theme";
import { apiErrorMessage, fmtQty, newActionId } from "../../../src/utils/format";
import {
  canApproveDesign,
  canUploadSample,
  defaultWorkDivision,
  permittedWorkDivisions,
} from "../../../src/utils/permissions";
import { assetFileMeta, pickFromCamera, pickFromGallery } from "../../../src/utils/mediaPicker";
import { uriToDataUrl } from "../../../src/utils/dataUrl";
import {
  canShowSamplePanel,
  canShowSampleUploadActions,
  pickerUserMessage,
  sampleAttachmentPayload,
  sampleFilePreviewUri,
  sampleFiles,
  sampleGateMessage,
  sampleUploadErrorMessage,
  shouldUploadPicked,
} from "../../../src/utils/sampleUpload";
import {
  helperModel,
  loadingModel,
  operatorCanSubmit,
  operatorConfirmEnabled,
  operatorPanelModel,
  packingModel,
  screenerModel,
} from "../../../src/utils/workModels";
import { deliveryTaskReady } from "../../../src/utils/taskEligibility";
import {
  canOpenDesignFile,
  currentDesignFile,
  designFileIsImage,
  designFileLabel,
  designFileOpenRef,
  designFilePreviewUri,
  designFiles,
  designStatusLabel,
} from "../../../src/utils/designFiles";

const WORK_PANEL_IDS = [
  "screener",
  "helper",
  "operator",
  "packing",
  "loading",
  "terkirim",
  "design_approve",
] as const;

export default function PoWorkScreen() {
  const { id, divisi } = useLocalSearchParams<{ id: string; divisi?: string }>();
  const { user, hasPerm, token } = useAuth();
  const preferredDiv = Array.isArray(divisi) ? divisi[0] : divisi || null;
  const divisions = useMemo(() => permittedWorkDivisions(hasPerm), [hasPerm]);
  const [activeDiv, setActiveDiv] = useState<string | null>(
    () => defaultWorkDivision(hasPerm, preferredDiv)
  );
  const [order, setOrder] = useState<Record<string, any> | null>(null);
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);
  const [drafts, setDrafts] = useState<Record<string, string>>({});
  const [actionErr, setActionErr] = useState("");
  const [drawingSignature, setDrawingSignature] = useState(false);

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

  useEffect(() => {
    setActiveDiv(defaultWorkDivision(hasPerm, preferredDiv));
  }, [id, preferredDiv, hasPerm]);

  const visiblePanelIds = useMemo(() => {
    const ids: string[] = [];
    for (const d of divisions) {
      if ((WORK_PANEL_IDS as readonly string[]).includes(d.id)) ids.push(d.id);
    }
    if (canApproveDesign(hasPerm)) ids.push("design_approve");
    if (canShowSamplePanel(hasPerm, order)) ids.push("sample_upload");
    const focus = activeDiv && ids.includes(activeDiv) ? activeDiv : ids[0] || null;
    if (!focus) return ids;
    return [focus, ...ids.filter((x) => x !== focus)];
  }, [divisions, activeDiv, hasPerm, order]);

  const onConfirmQty = async (payload: {
    kind: "screener" | "helper" | "operator" | "packing";
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
      } else if (payload.kind === "packing") {
        res = await eternalApi.savePackingInfo(order.id, [
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

  const onPickProof = async () => {
    if (!order || busy) return;
    const perm = await ImagePicker.requestCameraPermissionsAsync();
    const lib = await ImagePicker.requestMediaLibraryPermissionsAsync();
    if (!perm.granted && !lib.granted) {
      setActionErr("Izin kamera/galeri diperlukan untuk bukti pengiriman.");
      return;
    }
    Alert.alert("Bukti Pengiriman", "Pilih sumber foto", [
      {
        text: "Kamera",
        onPress: async () => {
          const shot = await ImagePicker.launchCameraAsync({
            quality: 0.7,
            allowsEditing: false,
          });
          if (!shot.canceled && shot.assets[0]) await uploadProof(shot.assets[0]);
        },
      },
      {
        text: "Galeri",
        onPress: async () => {
          const pick = await ImagePicker.launchImageLibraryAsync({
            quality: 0.7,
            allowsMultipleSelection: false,
            mediaTypes: ["images"],
          });
          if (!pick.canceled && pick.assets[0]) await uploadProof(pick.assets[0]);
        },
      },
      { text: "Batal", style: "cancel" },
    ]);
  };

  const uploadProof = async (asset: ImagePicker.ImagePickerAsset) => {
    if (!order) return;
    setBusy(true);
    setActionErr("");
    try {
      const name = asset.fileName || `bukti-${Date.now()}.jpg`;
      const mime = asset.mimeType || "image/jpeg";
      const res = await eternalApi.uploadDeliveryProof(order.id, asset.uri, name, mime);
      setOrder(res.order);
      Alert.alert(
        "Foto terunggah",
        "Upload foto tidak menandai terkirim. Setelah minimal 2 foto, isi nama penerima dan kedua tanda tangan, lalu Konfirmasi Terkirim."
      );
    } catch (e) {
      setActionErr(apiErrorMessage(e, "Gagal mengunggah foto"));
    } finally {
      setBusy(false);
    }
  };

  const openDesignFile = async (file: any) => {
    const ref = designFileOpenRef(file);
    if (!ref.kind) {
      setActionErr("File desain tidak dapat dibuka.");
      return;
    }
    const url =
      ref.kind === "storage" ? eternalApi.fileUrl(ref.value, token) : ref.value;
    await WebBrowser.openBrowserAsync(url);
  };

  const openSuratJalan = async (fileId: string) => {
    if (!hasPerm("delivery_note.view_file")) {
      setActionErr("Tidak punya izin melihat file Surat Jalan.");
      return;
    }
    const url = eternalApi.suratJalanFileUrl(String(order?.id), fileId, token);
    await WebBrowser.openBrowserAsync(url);
  };

  const onConfirmLoading = async (suratJalanId: string, lines: { line_idx: number; qty: number }[]) => {
    if (!order || busy) return;
    if (!suratJalanId) {
      setActionErr("Pilih Surat Jalan yang dikonfirmasi.");
      return;
    }
    if (!lines.length) {
      setActionErr("Qty Loading wajib diisi.");
      return;
    }
    setBusy(true);
    setActionErr("");
    try {
      const res = await eternalApi.confirmLoading(order.id, {
        surat_jalan_id: suratJalanId,
        lines,
        action_id: newActionId(),
      });
      setOrder(res.order);
      setDrafts({});
      Alert.alert("Tersimpan", "Konfirmasi Loading berhasil.");
    } catch (e) {
      setActionErr(apiErrorMessage(e, "Gagal konfirmasi loading"));
    } finally {
      setBusy(false);
    }
  };

  const onApproveDesign = async (approved: boolean) => {
    if (!order || busy) return;
    setBusy(true);
    setActionErr("");
    try {
      const res = await eternalApi.approveDesign(order.id, approved);
      setOrder(res.order);
      Alert.alert(approved ? "Design di-approve" : "Approval dibatalkan", "Status design diperbarui.");
    } catch (e) {
      setActionErr(apiErrorMessage(e, "Gagal approve design"));
    } finally {
      setBusy(false);
    }
  };

  const onPickSample = async (source: "camera" | "gallery") => {
    if (!order || busy) return;
    if (!canUploadSample(hasPerm)) {
      setActionErr("Tidak punya izin upload sampel.");
      return;
    }
    if (!canShowSampleUploadActions(hasPerm, order)) {
      setActionErr(sampleGateMessage(order));
      return;
    }
    setBusy(true);
    setActionErr("");
    try {
      const picked = source === "camera" ? await pickFromCamera() : await pickFromGallery();
      if (!shouldUploadPicked(picked)) {
        const msg = pickerUserMessage(picked);
        if (msg) setActionErr(msg);
        return;
      }
      if (picked.status !== "ok") return;
      const { name, mime } = assetFileMeta(picked.asset, "sampel");
      const dataUrl = await uriToDataUrl(picked.asset.uri, mime);
      await eternalApi.addAttachment(String(order.id), sampleAttachmentPayload(name, dataUrl));
      const res = await eternalApi.getOrder(String(order.id));
      setOrder(res.order);
      Alert.alert("Tersimpan", "Sampel cetak berhasil diunggah.");
    } catch (e) {
      setActionErr(sampleUploadErrorMessage(e));
    } finally {
      setBusy(false);
    }
  };

  if (loading) return <LoadingState label="Memuat pekerjaan…" />;
  if (err) return <ErrorState message={err} onRetry={load} />;
  if (!order) return <EmptyState message="PO tidak ditemukan." />;
  if (!visiblePanelIds.length && !hasPerm("po.view")) {
    return (
      <EmptyState message="Akun ini tidak punya permission pekerjaan pada PO ini." />
    );
  }

  const renderWorkPanel = (panelId: string) => {
    if (panelId === "screener" && hasPerm("screen.update")) {
      return (
        <ScreenerPanel
          key="screener"
          order={order}
          draft={drafts.screener || ""}
          setDraft={(v) => setDrafts((p) => ({ ...p, screener: v }))}
          busy={busy}
          onConfirm={(qty) => onConfirmQty({ kind: "screener", qty })}
        />
      );
    }
    if (panelId === "helper" && hasPerm("helper.prepare")) {
      return (
        <HelperPanel
          key="helper"
          order={order}
          drafts={drafts}
          setDraft={(key, v) => setDrafts((p) => ({ ...p, [key]: v }))}
          busy={busy}
          onConfirm={(lineIdx, qty) => onConfirmQty({ kind: "helper", qty, lineIdx })}
        />
      );
    }
    if (panelId === "packing" && hasPerm("packing.update")) {
      return (
        <PackingPanel
          key="packing"
          order={order}
          drafts={drafts}
          setDraft={(key, v) => setDrafts((p) => ({ ...p, [key]: v }))}
          busy={busy}
          onConfirm={(lineIdx, qty) => onConfirmQty({ kind: "packing", qty, lineIdx })}
        />
      );
    }
    if (panelId === "loading" && hasPerm("loading.update")) {
      return (
        <LoadingPanel
          key="loading"
          order={order}
          drafts={drafts}
          setDraft={(key, v) => setDrafts((p) => ({ ...p, [key]: v }))}
          busy={busy}
          onConfirm={onConfirmLoading}
        />
      );
    }
    if (panelId === "design_approve" && canApproveDesign(hasPerm)) {
      return <DesignApprovePanel key="design_approve" order={order} busy={busy} onApprove={onApproveDesign} />;
    }
    if (panelId === "operator" && hasPerm("operator.update")) {
      return (
        <OperatorPanel
          key="operator"
          order={order}
          user={user}
          drafts={drafts}
          setDraft={(key, v) => setDrafts((p) => ({ ...p, [key]: v }))}
          busy={busy}
          onConfirm={(splitId, qty) => onConfirmQty({ kind: "operator", qty, splitId })}
        />
      );
    }
    if (panelId === "sample_upload" && canShowSamplePanel(hasPerm, order)) {
      return (
        <SamplePanel
          key="sample_upload"
          order={order}
          busy={busy}
          canUpload={canShowSampleUploadActions(hasPerm, order)}
          onPickCamera={() => onPickSample("camera")}
          onPickGallery={() => onPickSample("gallery")}
        />
      );
    }
    if (panelId === "terkirim" && hasPerm("delivery.update")) {
      const delivery = deliveryTaskReady(order);
      const delivered = order.delivery_status === "Terkirim" || !!order.terkirim_confirmed;
      if (!delivery.actionable && !delivered) {
        return (
          <View key="terkirim" style={ui.cardFlush} testID="driver-panel-locked">
            <SectionCardHeader tone="delivery" title="Pengiriman" />
            <View style={ui.cardBody}>
              <Text style={[typography.subtitle, { color: colors.warn }]}>
                {delivery.lockedReason || "Belum ada qty eligible pengiriman"}
              </Text>
            </View>
          </View>
        );
      }
      return (
        <DriverDeliveryPanel
          key="terkirim"
          order={order}
          token={token}
          user={user}
          hasPerm={hasPerm}
          busy={busy}
          onPickProof={onPickProof}
          onOpenSj={openSuratJalan}
          onUpdated={(next) => setOrder(next)}
          onError={setActionErr}
          onDrawingChange={setDrawingSignature}
        />
      );
    }
    return null;
  };

  return (
    <KeyboardAvoidingView
      style={ui.screen}
      behavior={Platform.OS === "ios" ? "padding" : undefined}
    >
    <ScrollView
      style={ui.screen}
      keyboardShouldPersistTaps="handled"
      keyboardDismissMode="on-drag"
      scrollEnabled={!drawingSignature}
      contentContainerStyle={{ padding: space.md, paddingBottom: 48 }}
    >
      <Stack.Screen options={{ title: order.po_number || "Pekerjaan PO" }} />

      <View style={ui.cardFlush}>
        <SectionCardHeader tone="po" title="Informasi PO" />
        <View style={ui.cardBody}>
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
      </View>

      <DesignFilesCard order={order} onOpen={openDesignFile} />

      {visiblePanelIds.length > 1 ? (
        <View style={styles.tabs}>
          {divisions.filter((d) => (WORK_PANEL_IDS as readonly string[]).includes(d.id)).map((d) => {
            const active = activeDiv === d.id;
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
          {canApproveDesign(hasPerm) ? (
            <Pressable
              onPress={() => setActiveDiv("design_approve")}
              style={[styles.tab, activeDiv === "design_approve" && styles.tabActive]}
            >
              <Text style={[styles.tabText, activeDiv === "design_approve" && styles.tabTextActive]}>
                Approve Design
              </Text>
            </Pressable>
          ) : null}
          {canShowSamplePanel(hasPerm, order) ? (
            <Pressable
              onPress={() => setActiveDiv("sample_upload")}
              style={[styles.tab, activeDiv === "sample_upload" && styles.tabActive]}
            >
              <Text style={[styles.tabText, activeDiv === "sample_upload" && styles.tabTextActive]}>
                Upload Sampel Cetak
              </Text>
            </Pressable>
          ) : null}
        </View>
      ) : null}

      {actionErr ? (
        <View style={[ui.errorBox, { marginBottom: space.sm }]}>
          <Text style={ui.errorText}>{actionErr}</Text>
        </View>
      ) : null}

      {visiblePanelIds.map((panelId) => renderWorkPanel(panelId))}

      <GhostButton title="Muat ulang" onPress={load} style={{ marginTop: space.md }} />
    </ScrollView>
    </KeyboardAvoidingView>
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
    <View style={ui.cardFlush} testID="screener-panel">
      <SectionCardHeader tone="screener" title={model.title} />
      <View style={ui.cardBody}>
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
    <View style={ui.cardFlush} testID="helper-panel">
      <SectionCardHeader tone="helper" title={model.title} />
      <View style={ui.cardBody}>
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
    </View>
  );
}

function PackingPanel({
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
  const model = packingModel(order);
  return (
    <View style={ui.cardFlush} testID="packing-panel">
      <SectionCardHeader tone="packing" title={model.title} />
      <View style={ui.cardBody}>
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
                label="Qty Packing"
                value={drafts[`p-${line.lineIdx}`] || ""}
                onChange={(v) => setDraft(`p-${line.lineIdx}`, v)}
              />
              <PrimaryButton
                title="Konfirmasi Packing"
                busy={busy}
                disabled={line.remaining <= 0}
                onPress={() => onConfirm(line.lineIdx, Number(drafts[`p-${line.lineIdx}`] || 0))}
                style={{ marginTop: space.sm }}
              />
            </>
          ) : null}
        </View>
      ))}
      </View>
    </View>
  );
}

function LoadingPanel({
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
  onConfirm: (suratJalanId: string, lines: { line_idx: number; qty: number }[]) => void;
}) {
  const [sjId, setSjId] = useState("");
  const model = loadingModel(order, sjId);
  const fileIds = model.files.map((f: any) => f.id).join("|");
  useEffect(() => {
    if (!sjId && model.selectedSjId) setSjId(model.selectedSjId);
    if (sjId && !model.files.some((f: any) => f.id === sjId) && model.selectedSjId) {
      setSjId(model.selectedSjId);
    }
  }, [sjId, model.selectedSjId, fileIds]);
  const activeSj = model.files.find((f: any) => f.id === sjId) || model.files[0];
  return (
    <View style={ui.cardFlush} testID="loading-panel">
      <SectionCardHeader tone="loading" title={model.title} />
      <View style={ui.cardBody}>
      <Text style={typography.subtitle}>Konfirmasi item & qty berdasarkan Surat Jalan</Text>
      <MetricStrip
        target={fmtQty(model.target)}
        done={fmtQty(model.done)}
        remain={fmtQty(model.remaining)}
        status={model.status}
      />
      {model.gateLocked ? (
        <Text style={[typography.subtitle, { color: colors.warn }]} testID="loading-gate-note">
          {model.gateMsg}
        </Text>
      ) : null}
      {model.files.length > 1 ? (
        <View style={{ marginTop: space.sm }} testID="loading-sj-picker">
          <Text style={typography.label}>Pilih Surat Jalan</Text>
          <View style={styles.tabs}>
            {model.files.map((f: any) => {
              const active = (sjId || model.selectedSjId) === f.id;
              return (
                <Pressable
                  key={f.id}
                  onPress={() => setSjId(f.id)}
                  style={[styles.tab, active && styles.tabActive]}
                  testID={`loading-sj-${f.id}`}
                >
                  <Text style={[styles.tabText, active && styles.tabTextActive]}>
                    {f.name || f.original_filename || f.id}
                  </Text>
                </Pressable>
              );
            })}
          </View>
        </View>
      ) : activeSj ? (
        <Text style={typography.subtitle}>SJ: {activeSj.name || activeSj.id}</Text>
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
          {!model.gateLocked && line.remaining > 0 ? (
            <QtyField
              label="Qty Loading"
              value={drafts[`l-${line.lineIdx}`] || ""}
              onChange={(v) => setDraft(`l-${line.lineIdx}`, v)}
            />
          ) : null}
        </View>
      ))}
      {!model.gateLocked ? (
        <PrimaryButton
          title="Konfirmasi Loading"
          busy={busy}
          disabled={model.remaining <= 0 || !(sjId || model.selectedSjId)}
          onPress={() => {
            const lines = model.lines
              .map((line: any) => ({
                line_idx: line.lineIdx,
                qty: Number(drafts[`l-${line.lineIdx}`] || 0),
              }))
              .filter((l: any) => l.qty > 0);
            onConfirm(sjId || model.selectedSjId, lines);
          }}
          style={{ marginTop: space.md }}
        />
      ) : null}
      </View>
    </View>
  );
}

function DesignFilesCard({
  order,
  onOpen,
}: {
  order: any;
  onOpen: (file: any) => void;
}) {
  const files = designFiles(order);
  const current = currentDesignFile(order);
  const status = designStatusLabel(order);
  const older = files.length > 1 ? files.slice(0, -1) : [];
  const preview = current && designFileIsImage(current) ? designFilePreviewUri(current) : null;

  return (
    <View style={ui.cardFlush} testID="design-files-card">
      <SectionCardHeader tone="design" title="File Desain" />
      <View style={ui.cardBody}>
      <Text style={typography.label}>Referensi desain</Text>
      {!current ? (
        <Text style={[typography.subtitle, { marginTop: 6 }]} testID="design-files-empty">
          File desain belum tersedia
        </Text>
      ) : (
        <>
          <Text style={[typography.body, { marginTop: 6 }]}>{designFileLabel(current)}</Text>
          {status ? (
            <Text style={typography.subtitle}>Status: {status}</Text>
          ) : null}
          {files.length > 1 ? (
            <Text style={typography.subtitle}>Revisi: {files.length} file (terbaru di atas)</Text>
          ) : null}
          {preview ? (
            <Image
              source={{ uri: preview }}
              style={styles.designPreview}
              resizeMode="contain"
              testID="design-file-preview"
            />
          ) : null}
          {canOpenDesignFile(current) ? (
            <PrimaryButton
              title="Lihat Desain"
              onPress={() => onOpen(current)}
              style={{ marginTop: space.sm }}
              testID="design-file-open"
            />
          ) : (
            <Text style={[typography.subtitle, { marginTop: 6 }]}>
              File desain tidak dapat dibuka di perangkat ini.
            </Text>
          )}
          {older.length
            ? older
                .slice()
                .reverse()
                .map((file: any, i: number) => (
                  <Pressable
                    key={file.id || `older-${i}`}
                    onPress={() => canOpenDesignFile(file) && onOpen(file)}
                    style={styles.designOlder}
                    testID={`design-file-older-${file.id || i}`}
                  >
                    <Text style={typography.subtitle}>{designFileLabel(file)}</Text>
                  </Pressable>
                ))
            : null}
        </>
      )}
      </View>
    </View>
  );
}

function DesignApprovePanel({
  order,
  busy,
  onApprove,
}: {
  order: any;
  busy: boolean;
  onApprove: (approved: boolean) => void;
}) {
  const files = order?.design_files || [];
  const approved = order?.design_status === "Approved" || order?.design_bypassed;
  const waiting = files.length > 0 && !approved;
  return (
    <View style={ui.cardFlush} testID="design-approve-panel">
      <SectionCardHeader tone="designApprove" title="Approve Design" />
      <View style={ui.cardBody}>
      <Text style={typography.subtitle}>
        Status: {order?.design_status || "Waiting"} · File: {files.length}
      </Text>
      {waiting ? (
        <PrimaryButton
          title="Approve Design"
          busy={busy}
          onPress={() => onApprove(true)}
          style={{ marginTop: space.md }}
        />
      ) : (
        <Text style={typography.subtitle}>
          {approved ? "Design sudah di-approve." : "Upload design diperlukan sebelum approval."}
        </Text>
      )}
      </View>
    </View>
  );
}

function SamplePanel({
  order,
  busy,
  canUpload,
  onPickCamera,
  onPickGallery,
}: {
  order: any;
  busy: boolean;
  canUpload: boolean;
  onPickCamera: () => void;
  onPickGallery: () => void;
}) {
  const files = sampleFiles(order);
  return (
    <View style={ui.cardFlush} testID="sample-panel">
      <SectionCardHeader tone="sample" title="Upload Sampel Cetak" />
      <View style={ui.cardBody}>
      <Text style={typography.subtitle}>Foto sampel cetak Operator — terpisah dari File Design.</Text>
      <View style={styles.proofGrid}>
        {files.map((f: any, i: number) => {
          const uri = sampleFilePreviewUri(f);
          return (
            <View key={f.id || i} style={styles.proofThumb} testID={`sample-file-${f.id || i}`}>
              {uri ? (
                <Image source={{ uri }} style={styles.proofImg} />
              ) : (
                <Text style={typography.subtitle}>{f.name || `Foto ${i + 1}`}</Text>
              )}
            </View>
          );
        })}
      </View>
      {!files.length ? <Text style={typography.subtitle}>Belum ada file sampel.</Text> : null}
      {canUpload ? (
        <>
          <PrimaryButton
            title="Ambil Foto Sampel"
            busy={busy}
            onPress={onPickCamera}
            style={{ marginTop: space.md }}
            testID="sample-camera-button"
          />
          <GhostButton
            title="Pilih dari Galeri"
            onPress={onPickGallery}
            disabled={busy}
            style={{ marginTop: space.sm }}
            testID="sample-gallery-button"
          />
        </>
      ) : (
        <Text style={[typography.subtitle, { color: colors.warn, marginTop: space.md }]} testID="sample-gate-note">
          {sampleGateMessage(order)}
        </Text>
      )}
      </View>
    </View>
  );
}

function OperatorPanel({
  order,
  user,
  drafts,
  setDraft,
  busy,
  onConfirm,
}: {
  order: any;
  user: any;
  drafts: Record<string, string>;
  setDraft: (key: string, v: string) => void;
  busy: boolean;
  onConfirm: (splitId: string, qty: number) => void;
}) {
  const model = operatorPanelModel(order, user);
  const gate = operatorCanSubmit(order);
  return (
    <View style={ui.cardFlush} testID="operator-panel">
      <SectionCardHeader tone="operator" title={model.title} />
      <View style={ui.cardBody}>
      <Text style={typography.subtitle}>Assignment pribadi Anda</Text>
      <MetricStrip
        target={fmtQty(model.target)}
        done={fmtQty(model.done)}
        remain={fmtQty(model.remaining)}
        status={model.status}
      />
      {!gate.ok ? (
        <Text style={[typography.subtitle, { color: colors.warn }]}>{gate.reason}</Text>
      ) : null}
      {!model.lines.length ? (
        <Text style={typography.subtitle}>Tidak ada assignment operator untuk akun ini pada PO ini.</Text>
      ) : null}
      {model.lines.map((line) => {
        const draftKey = `o-${line.splitId}`;
        const draft = drafts[draftKey] || "";
        const inputQty = Number(draft || 0);
        const canConfirm = operatorConfirmEnabled({
          hasOperatorUpdate: true,
          workflowEligible: gate.ok,
          remaining: line.remaining,
          inputQty,
          submitting: busy,
        });
        return (
          <View key={line.splitId} style={styles.lineBlock}>
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
                  value={draft}
                  placeholder={`Sisa ${fmtQty(line.remaining)}`}
                  onChange={(v) => setDraft(draftKey, v)}
                  testID={`operator-qty-${line.splitId}`}
                />
                <PrimaryButton
                  title="Konfirmasi Operator"
                  busy={busy}
                  disabled={!canConfirm}
                  onPress={() => onConfirm(String(line.split?.id || line.splitId), inputQty)}
                  style={{ marginTop: space.sm }}
                  testID={`operator-confirm-${line.splitId}`}
                />
              </>
            ) : null}
          </View>
        );
      })}
      </View>
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
  designPreview: {
    width: "100%",
    height: 160,
    marginTop: 8,
    borderRadius: 10,
    backgroundColor: colors.bg,
  },
  designOlder: {
    marginTop: 8,
    paddingVertical: 8,
    borderTopWidth: 1,
    borderTopColor: colors.line,
    minHeight: 40,
    justifyContent: "center",
  },
});
