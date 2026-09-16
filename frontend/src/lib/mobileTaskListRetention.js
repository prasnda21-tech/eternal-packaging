/**
 * In-memory last-good mobile task list snapshot.
 * Survives MobileTasksPage unmount (list → detail → back).
 * Never writes task payloads to sessionStorage / localStorage.
 *
 * Validity is current client principal + division + mine scope + the workspace
 * permission that authorized the snapshot. Key equality alone is not enough.
 */

import { storageUserKey } from "./poListReturn";

const TASK_LIST_SCOPE = "mine";
const snapshots = new Map();

export function mobileTaskListRetentionKey(user, division, scope = TASK_LIST_SCOPE, permission = "") {
  if (!user || typeof user !== "object") return "";
  const userKey = storageUserKey(user);
  if (!userKey || userKey === "anon") return "";
  const div = String(division || "").trim();
  const sc = String(scope || TASK_LIST_SCOPE).trim();
  const perm = String(permission || "").trim();
  if (!div || !sc || !perm) return "";
  return `${userKey}::${div}::${sc}::${perm}`;
}

export function isMobileTaskListRetentionAuthorized({
  user,
  division,
  scope = TASK_LIST_SCOPE,
  permission,
  hasPerm,
} = {}) {
  if (typeof hasPerm !== "function") return false;
  const perm = String(permission || "").trim();
  if (!perm || !hasPerm(perm)) return false;
  return Boolean(mobileTaskListRetentionKey(user, division, scope, perm));
}

function clonePayload(payload) {
  if (!payload || typeof payload !== "object") return { tasks: [] };
  const tasks = Array.isArray(payload.tasks) ? payload.tasks.slice() : [];
  return { ...payload, tasks };
}

function pruneOtherUsers(keepUserKey) {
  const keep = String(keepUserKey || "");
  if (!keep || keep === "anon") {
    snapshots.clear();
    return;
  }
  const prefix = `${keep}::`;
  for (const key of [...snapshots.keys()]) {
    if (!key.startsWith(prefix)) snapshots.delete(key);
  }
}

export function readMobileTaskListRetention({
  user,
  division,
  scope = TASK_LIST_SCOPE,
  permission,
  hasPerm,
} = {}) {
  if (!isMobileTaskListRetentionAuthorized({ user, division, scope, permission, hasPerm })) {
    return null;
  }
  const key = mobileTaskListRetentionKey(user, division, scope, permission);
  pruneOtherUsers(storageUserKey(user));
  const snap = snapshots.get(key);
  return snap ? clonePayload(snap) : null;
}

export function writeMobileTaskListRetention({
  user,
  division,
  scope = TASK_LIST_SCOPE,
  permission,
  hasPerm,
  payload,
} = {}) {
  if (!isMobileTaskListRetentionAuthorized({ user, division, scope, permission, hasPerm })) {
    return;
  }
  const key = mobileTaskListRetentionKey(user, division, scope, permission);
  pruneOtherUsers(storageUserKey(user));
  snapshots.set(key, clonePayload(payload));
}

export function deleteMobileTaskListRetention({
  user,
  division,
  scope = TASK_LIST_SCOPE,
  permission,
} = {}) {
  const key = mobileTaskListRetentionKey(user, division, scope, permission);
  if (key) snapshots.delete(key);
}

export function clearAllMobileTaskListRetention() {
  snapshots.clear();
}

export function httpStatusFromError(err) {
  const n = Number(err?.response?.status ?? err?.status);
  return Number.isFinite(n) && n > 0 ? n : 0;
}

/** Axios-style 401 only. Do not treat 403 or generic failures as session loss. */
export function isAuthSessionExpiredError(err) {
  return httpStatusFromError(err) === 401;
}
