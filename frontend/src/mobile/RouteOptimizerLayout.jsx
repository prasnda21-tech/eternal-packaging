/**
 * Shared /optimasi-rute/* layout: keep installed PWA users inside the mobile ops shell
 * instead of swapping to desktop AppShell (which breaks standalone context on Android).
 */
import { useMemo } from "react";
import AppShell from "../components/AppShell";
import { preferMobileOpsShell } from "../lib/mobileOpsShell";
import MobileOperationalLayout from "./MobileOperationalLayout";

export default function RouteOptimizerLayout() {
  const useMobile = useMemo(() => preferMobileOpsShell(), []);
  if (useMobile) return <MobileOperationalLayout />;
  return <AppShell />;
}
