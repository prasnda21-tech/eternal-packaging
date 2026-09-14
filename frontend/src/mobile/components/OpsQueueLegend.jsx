/** Presentation-only legend + footer matching the approved ops-queue mockup. */

import { Cog, Truck } from "lucide-react";

export default function OpsQueueLegend() {
  return (
    <>
      <section className="ops-queue-legend" data-testid="ops-queue-legend" aria-label="Keterangan antrian operasional">
        <div className="ops-ql-block">
          <span className="ops-ql-icon" aria-hidden="true"><Cog size={16} /></span>
          <div>
            <p className="ops-ql-title">Persiapan Cup & Screen</p>
            <p className="ops-ql-copy">Berdasarkan Jadwal Produksi dari hasil rencana produksi harian.</p>
          </div>
        </div>
        <div className="ops-ql-block">
          <span className="ops-ql-icon" aria-hidden="true"><Truck size={16} /></span>
          <div>
            <p className="ops-ql-title">Packing, Loading & Pengiriman</p>
            <p className="ops-ql-copy">Berdasarkan Jadwal Pengiriman. Mengikuti rencana pengiriman ke customer.</p>
          </div>
        </div>
        <div className="ops-ql-keys">
          <div className="ops-ql-key">
            <span className="ops-ql-dot is-urgent" aria-hidden="true" />
            <div>
              <strong>Urgent</strong>
              <span>Terlambat / kirim hari ini belum siap</span>
            </div>
          </div>
          <div className="ops-ql-key">
            <span className="ops-ql-dot is-priority" aria-hidden="true" />
            <div>
              <strong>Prioritas</strong>
              <span>Hari ini / perlu segera dikerjakan</span>
            </div>
          </div>
          <div className="ops-ql-key">
            <span className="ops-ql-dot is-normal" aria-hidden="true" />
            <div>
              <strong>Normal</strong>
              <span>Sesuai jadwal</span>
            </div>
          </div>
          <div className="ops-ql-key">
            <span className="ops-ql-dot is-done" aria-hidden="true" />
            <div>
              <strong>Selesai</strong>
              <span>Sudah dikirim</span>
            </div>
          </div>
        </div>
      </section>
      <footer className="ops-queue-footer" data-testid="ops-queue-footer">
        <div className="ops-qf-brand">
          <span className="ops-qf-mark" aria-hidden="true" />
          ETERNAL PACKAGING
        </div>
        <p className="ops-qf-tag">Satu Data. Setiap Peran. Operasional Lebih Efisien.</p>
        <p className="ops-qf-meta">v1.0.0</p>
      </footer>
    </>
  );
}
