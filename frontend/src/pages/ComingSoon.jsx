export default function ComingSoon({ title, eyebrow, testid }) {
  return (
    <div className="coming-soon-page" data-testid={testid || "coming-soon-page"}>
      <div className="page-heading">
        <div>
          <p className="eyebrow">{eyebrow || "Modul"}</p>
          <h1>{title}</h1>
          <p className="subhead">Modul ini akan tersedia pada pengembangan berikutnya.</p>
        </div>
      </div>
      <div className="coming-soon-note">
        Halaman ini masih berupa placeholder. Tidak ada data transaksi, dan belum terhubung ke proses operasional.
      </div>
    </div>
  );
}
