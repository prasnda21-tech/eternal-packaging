/** Shared identity fragments for Smart Workspace list cache keys. */

export function listIdentityUserId(user) {
  return user?.id || user?.email || user?.sub || "";
}

export function listIdentityBranchId(user) {
  return user?.branch_id || user?.active_branch_id || user?.branchId || "";
}
