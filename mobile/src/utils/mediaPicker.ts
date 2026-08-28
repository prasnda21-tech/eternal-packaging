import { Platform } from "react-native";
import * as ImagePicker from "expo-image-picker";

export type PickerOk = { status: "ok"; asset: ImagePicker.ImagePickerAsset };
export type PickerCancel = { status: "cancel" };
export type PickerDenied = { status: "denied"; message: string };
export type PickerResult = PickerOk | PickerCancel | PickerDenied;

const DELAY_MS = 350;

function delay(ms: number) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function androidApiLevel(): number {
  if (Platform.OS !== "android") return 0;
  const raw = Platform.Version;
  return typeof raw === "number" ? raw : parseInt(String(raw), 10) || 0;
}

function needsMediaLibraryPermission(): boolean {
  if (Platform.OS !== "android") return true;
  return androidApiLevel() < 33;
}

const IMAGE_MEDIA_TYPES = ["images"] as unknown as ImagePicker.MediaType[];

export function assetFileMeta(
  asset: ImagePicker.ImagePickerAsset,
  prefix: string
): { name: string; mime: string } {
  const mime = (asset.mimeType || "image/jpeg").toLowerCase();
  const ext = mime.includes("png")
    ? "png"
    : mime.includes("webp")
      ? "webp"
      : mime.includes("gif")
        ? "gif"
        : "jpg";
  const raw = String(asset.fileName || "").trim();
  const name = raw && /\.[a-z0-9]+$/i.test(raw) ? raw : `${prefix}-${Date.now()}.${ext}`;
  return { name, mime: mime || "image/jpeg" };
}

export async function pickFromCamera(): Promise<PickerResult> {
  const existing = await ImagePicker.getCameraPermissionsAsync();
  let granted = existing.granted;
  if (!granted) {
    const perm = await ImagePicker.requestCameraPermissionsAsync();
    granted = perm.granted;
    if (granted) await delay(DELAY_MS);
  }
  if (!granted) {
    return {
      status: "denied",
      message:
        "Izin kamera ditolak. Aktifkan izin kamera di pengaturan perangkat, lalu coba lagi.",
    };
  }
  const shot = await ImagePicker.launchCameraAsync({
    quality: 0.7,
    allowsEditing: false,
  });
  if (shot.canceled || !shot.assets?.[0]) return { status: "cancel" };
  return { status: "ok", asset: shot.assets[0] };
}

export async function pickFromGallery(): Promise<PickerResult> {
  if (needsMediaLibraryPermission()) {
    const existing = await ImagePicker.getMediaLibraryPermissionsAsync();
    let granted = existing.granted;
    if (!granted) {
      const lib = await ImagePicker.requestMediaLibraryPermissionsAsync();
      granted = lib.granted;
      if (granted) await delay(DELAY_MS);
    }
    if (!granted) {
      return {
        status: "denied",
        message:
          "Izin galeri ditolak. Aktifkan izin foto di pengaturan perangkat, lalu coba lagi.",
      };
    }
  }
  const pick = await ImagePicker.launchImageLibraryAsync({
    quality: 0.7,
    allowsMultipleSelection: false,
    mediaTypes: IMAGE_MEDIA_TYPES,
  });
  if (pick.canceled || !pick.assets?.[0]) return { status: "cancel" };
  return { status: "ok", asset: pick.assets[0] };
}
