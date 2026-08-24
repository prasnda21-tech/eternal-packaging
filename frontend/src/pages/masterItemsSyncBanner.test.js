import { nextMasterSyncBanner, SYNC_FAIL_MSG } from "./masterItemsSyncBanner";

describe("Master Barang/Jasa stale sync banner", () => {
  test("FAIL then SUCCESS clears the old error", () => {
    const failed = nextMasterSyncBanner("fail");
    expect(failed.error).toBe(SYNC_FAIL_MSG);
    expect(failed.result).toBeNull();

    const started = nextMasterSyncBanner("start");
    expect(started.error).toBe("");
    expect(started.result).toBeNull();

    const ok = nextMasterSyncBanner("success", {
      status: "ok",
      created: 0,
      updated: 8,
      message: "Baru: 0 · Diperbarui: 8 · Tidak Berubah: 0 · Gagal: 0",
    });
    expect(ok.error).toBe("");
    expect(ok.result.status).toBe("ok");
    expect(ok.result.updated).toBe(8);
  });

  test("page reload does not restore a previous failure", () => {
    nextMasterSyncBanner("fail");
    const reloaded = nextMasterSyncBanner("reload");
    expect(reloaded.error).toBe("");
    expect(reloaded.result).toBeNull();
  });
});
