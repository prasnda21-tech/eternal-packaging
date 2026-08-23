import { useEffect, useRef, useState } from "react";

/**
 * useAutoRefresh — lightweight polling untuk read-only server state (Batch 2).
 *
 * Sifat:
 *   - Berjalan HANYA saat tab visible (`document.visibilityState === "visible"`).
 *   - Bersih otomatis pada unmount (clearInterval + removeEventListener).
 *   - Tidak menjalankan mutation.
 *   - `fn` dipanggil setiap `intervalMs`. `onData(result)` menerima hasil.
 *     `onError` dipanggil ketika `fn` throw.
 *   - `enabled=false` menonaktifkan (mis. saat form sedang dirty).
 *   - `paused=true` menghentikan sementara tanpa unmount (mis. saat modal edit
 *     terbuka — pemakai bertanggung jawab menyetel `paused` sesuai kebutuhan).
 *   - Return `{ lastUpdated, isPolling, refreshNow }` untuk tombol Refresh manual.
 */
export default function useAutoRefresh(fn, {
  intervalMs = 30_000,
  enabled = true,
  paused = false,
  onData,
  onError,
} = {}) {
  const [lastUpdated, setLastUpdated] = useState(null);
  const [isPolling, setIsPolling] = useState(false);
  const timerRef = useRef(null);
  const inFlightRef = useRef(false);
  // Store latest callbacks in refs → interval callback selalu memanggil versi
  // terbaru tanpa perlu restart interval.
  const fnRef = useRef(fn);
  const onDataRef = useRef(onData);
  const onErrorRef = useRef(onError);
  fnRef.current = fn;
  onDataRef.current = onData;
  onErrorRef.current = onError;

  const runOnce = async () => {
    if (inFlightRef.current) return;
    if (typeof document !== "undefined" && document.visibilityState !== "visible") return;
    inFlightRef.current = true;
    setIsPolling(true);
    try {
      const r = await fnRef.current();
      onDataRef.current?.(r);
      setLastUpdated(Date.now());
    } catch (e) {
      onErrorRef.current?.(e);
    } finally {
      inFlightRef.current = false;
      setIsPolling(false);
    }
  };

  useEffect(() => {
    if (!enabled || paused) return undefined;
    const start = () => {
      if (timerRef.current) return;
      timerRef.current = setInterval(runOnce, intervalMs);
    };
    const stop = () => {
      if (!timerRef.current) return;
      clearInterval(timerRef.current);
      timerRef.current = null;
    };
    const onVisChange = () => {
      if (document.visibilityState === "visible") start();
      else stop();
    };
    if (typeof document === "undefined" || document.visibilityState === "visible") start();
    if (typeof document !== "undefined") document.addEventListener("visibilitychange", onVisChange);
    return () => {
      stop();
      if (typeof document !== "undefined") document.removeEventListener("visibilitychange", onVisChange);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [enabled, paused, intervalMs]);

  return { lastUpdated, isPolling, refreshNow: runOnce };
}
