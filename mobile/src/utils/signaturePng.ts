/** Encode finger strokes as a PNG data URL. No extra native/canvas dependency. */

export type StrokePoint = { x: number; y: number };
export type Stroke = StrokePoint[];

const PNG_MAGIC = [137, 80, 78, 71, 13, 10, 26, 10];

const CRC_TABLE = (() => {
  const table = new Uint32Array(256);
  for (let n = 0; n < 256; n += 1) {
    let c = n;
    for (let k = 0; k < 8; k += 1) {
      c = c & 1 ? 0xedb88320 ^ (c >>> 1) : c >>> 1;
    }
    table[n] = c >>> 0;
  }
  return table;
})();

function crc32(bytes: Uint8Array): number {
  let c = 0xffffffff;
  for (let i = 0; i < bytes.length; i += 1) {
    c = CRC_TABLE[(c ^ bytes[i]) & 0xff] ^ (c >>> 8);
  }
  return (c ^ 0xffffffff) >>> 0;
}

function adler32(bytes: Uint8Array): number {
  let a = 1;
  let b = 0;
  const MOD = 65521;
  for (let i = 0; i < bytes.length; i += 1) {
    a = (a + bytes[i]) % MOD;
    b = (b + a) % MOD;
  }
  return ((b << 16) | a) >>> 0;
}

function u32(n: number): Uint8Array {
  return new Uint8Array([(n >>> 24) & 255, (n >>> 16) & 255, (n >>> 8) & 255, n & 255]);
}

function concat(parts: Uint8Array[]): Uint8Array {
  const total = parts.reduce((s, p) => s + p.length, 0);
  const out = new Uint8Array(total);
  let o = 0;
  for (const p of parts) {
    out.set(p, o);
    o += p.length;
  }
  return out;
}

function chunk(type: string, data: Uint8Array): Uint8Array {
  const typeBytes = Uint8Array.from(type.split("").map((ch) => ch.charCodeAt(0)));
  const body = concat([typeBytes, data]);
  return concat([u32(data.length), body, u32(crc32(body))]);
}

function zlibStore(raw: Uint8Array): Uint8Array {
  const blocks: Uint8Array[] = [new Uint8Array([0x78, 0x01])];
  let offset = 0;
  while (offset < raw.length) {
    const len = Math.min(65535, raw.length - offset);
    const last = offset + len >= raw.length;
    const header = new Uint8Array(5);
    header[0] = last ? 1 : 0;
    header[1] = len & 255;
    header[2] = (len >>> 8) & 255;
    header[3] = (~len) & 255;
    header[4] = ((~len) >>> 8) & 255;
    blocks.push(header, raw.subarray(offset, offset + len));
    offset += len;
  }
  blocks.push(u32(adler32(raw)));
  return concat(blocks);
}

function b64(bytes: Uint8Array): string {
  const chars = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";
  let out = "";
  for (let i = 0; i < bytes.length; i += 3) {
    const a = bytes[i];
    const b = i + 1 < bytes.length ? bytes[i + 1] : 0;
    const c = i + 2 < bytes.length ? bytes[i + 2] : 0;
    const triple = (a << 16) | (b << 8) | c;
    out += chars[(triple >> 18) & 63];
    out += chars[(triple >> 12) & 63];
    out += i + 1 < bytes.length ? chars[(triple >> 6) & 63] : "=";
    out += i + 2 < bytes.length ? chars[triple & 63] : "=";
  }
  return out;
}

function setPixel(buf: Uint8Array, w: number, x: number, y: number, h: number) {
  if (x < 0 || y < 0 || x >= w || y >= h) return;
  const i = (y * w + x) * 4;
  buf[i] = 17;
  buf[i + 1] = 17;
  buf[i + 2] = 17;
  buf[i + 3] = 255;
}

function stamp(buf: Uint8Array, w: number, h: number, x: number, y: number, radius: number) {
  const r2 = radius * radius;
  const xi = Math.round(x);
  const yi = Math.round(y);
  for (let dy = -radius; dy <= radius; dy += 1) {
    for (let dx = -radius; dx <= radius; dx += 1) {
      if (dx * dx + dy * dy <= r2) setPixel(buf, w, xi + dx, yi + dy, h);
    }
  }
}

function drawLine(
  buf: Uint8Array,
  w: number,
  h: number,
  x0: number,
  y0: number,
  x1: number,
  y1: number,
  radius: number
) {
  const dx = x1 - x0;
  const dy = y1 - y0;
  const steps = Math.max(1, Math.ceil(Math.hypot(dx, dy)));
  for (let i = 0; i <= steps; i += 1) {
    const t = i / steps;
    stamp(buf, w, h, x0 + dx * t, y0 + dy * t, radius);
  }
}

export function hasDrawableStrokes(strokes: Stroke[]): boolean {
  return (strokes || []).some((s) => Array.isArray(s) && s.length >= 2);
}

export function strokesToPngDataUrl(
  strokes: Stroke[],
  width: number,
  height: number,
  radius = 2
): string | null {
  const w = Math.max(8, Math.round(width));
  const h = Math.max(8, Math.round(height));
  if (!hasDrawableStrokes(strokes)) return null;

  const pixels = new Uint8Array(w * h * 4);
  pixels.fill(255);

  for (const stroke of strokes) {
    if (!stroke || stroke.length < 2) continue;
    for (let i = 1; i < stroke.length; i += 1) {
      drawLine(pixels, w, h, stroke[i - 1].x, stroke[i - 1].y, stroke[i].x, stroke[i].y, radius);
    }
  }

  const raw = new Uint8Array((w * 4 + 1) * h);
  for (let y = 0; y < h; y += 1) {
    const row = y * (w * 4 + 1);
    raw[row] = 0;
    raw.set(pixels.subarray(y * w * 4, (y + 1) * w * 4), row + 1);
  }

  const ihdr = concat([
    u32(w),
    u32(h),
    new Uint8Array([8, 6, 0, 0, 0]),
  ]);
  const png = concat([
    Uint8Array.from(PNG_MAGIC),
    chunk("IHDR", ihdr),
    chunk("IDAT", zlibStore(raw)),
    chunk("IEND", new Uint8Array(0)),
  ]);
  return `data:image/png;base64,${b64(png)}`;
}
