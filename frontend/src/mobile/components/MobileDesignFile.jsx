import { designFileMeta, latestDesignFile, openBrowserSafeFile } from "../../lib/mobileFileOpen";

export default function MobileDesignFile({ order }) {
  const file = latestDesignFile(order);
  const meta = designFileMeta(order, file);
  const open = () => {
    const result = openBrowserSafeFile(file);
    if (!result.ok || result.opened === false) {
      window.alert(result.reason || "File tidak dapat dibuka.");
    }
  };
  return (
    <div className="mop-file" data-testid="mobile-design-file">
      <strong>File Desain</strong>
      <p className="mop-card-title">{meta.name}</p>
      <p className="mop-card-meta">
        <span>{meta.type || "File"}</span>
        <span>{meta.approval}</span>
      </p>
      <button type="button" className="mop-btn-ghost" style={{ width: "100%", marginTop: 8 }} onClick={open} disabled={!file} data-testid="mobile-lihat-desain">
        Lihat Desain
      </button>
    </div>
  );
}
