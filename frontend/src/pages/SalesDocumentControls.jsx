import { useEffect, useMemo, useRef, useState } from 'react';
import { filterDocuments, moneyText, PAGE_SIZE, summarizeDocuments } from './salesDocumentsView';
import './salesDocumentsPremium.css';

export function useDocumentList(rows, kind, query) {
  const [filters,setFilters] = useState({status:'',start:'',end:'',columns:{}});
  const [page,setPage] = useState(1);
  const filtered = useMemo(() => filterDocuments(rows,kind,filters),[rows,kind,filters]);
  const pages = Math.max(1,Math.ceil(filtered.length/PAGE_SIZE));
  const current = Math.min(page,pages);
  useEffect(() => setPage(1),[filters,query]);
  const visible = useMemo(() => filtered.slice((current-1)*PAGE_SIZE,current*PAGE_SIZE),[filtered,current]);
  return {filters,setFilters,filtered,visible,page:current,pages,setPage,kind};
}
export function DocumentFilters({list}) {
  const {filters,setFilters,kind} = list;
  const options = kind === 'invoice'
    ? [['','Semua'],['unpaid','Belum lunas'],['partial','Sebagian'],['paid','Lunas'],['processing','Diproses'],['draft','Draft lama'],['cancelled','Dibatalkan']]
    : [['','Semua'],['active','Aktif'],['cancelled','Dibatalkan / dibalik'],['legacy','Data lama']];
  return <div className="doc-filters">
    <label>Status<select value={filters.status} onChange={e=>setFilters({...filters,status:e.target.value})}>{options.map(([v,label])=><option key={v} value={v}>{label}</option>)}</select></label>
    <div className="doc-period"><label>Dari<input aria-label="Tanggal mulai" type="date" value={filters.start} onChange={e=>setFilters({...filters,start:e.target.value})}/></label><span>–</span><label>Sampai<input aria-label="Tanggal akhir" type="date" min={filters.start || undefined} value={filters.end} onChange={e=>setFilters({...filters,end:e.target.value})}/></label></div>
    <button type="button" onClick={()=>setFilters({status:'',start:'',end:'',columns:{}})}>Reset filter</button>
  </div>;
}
export function ColumnFilter({list,field,children}) {
  const value = list.filters.columns[field] || '';
  return <div className="doc-column"><span>{children}</span><details onKeyDown={e=>{if(e.key==='Escape'){e.currentTarget.open=false; e.currentTarget.querySelector('summary')?.focus();}}}>
    <summary aria-label={`Filter ${children}`} className={value ? 'is-active' : ''}>⌕</summary>
    <label>Filter {children}<input aria-label={`Filter kolom ${children}`} value={value} onChange={e=>list.setFilters({...list.filters,columns:{...list.filters.columns,[field]:e.target.value}})}/></label>
  </details></div>;
}
export function DocumentSummary({list,loading,error}) {
  const labels = list.kind === 'invoice' ? ['Nilai faktur terbit','Terbayar / saldo terpakai','Sisa tagihan'] : ['Total penerimaan aktif','Alokasi awal ke faktur'];
  const values = summarizeDocuments(list.filtered,list.kind);
  return <section className="doc-summary" aria-label="Ringkasan hasil filter">
    {labels.map((label,i)=><div key={label}><span>{label}</span><strong>{loading || error ? '—' : moneyText(values[i])}</strong></div>)}
    <small>Hasil filter dari data termuat · bukan total seluruh database{list.kind==='receipt' ? ' · dibatalkan/dibalik dikecualikan' : ' · draft/proses/batal dikecualikan'}</small>
  </section>;
}
export function DocumentPager({list,loading,error}) {
  const count = list.filtered.length;
  return <div className="doc-pager"><span aria-live="polite">{loading ? 'Memuat…' : error ? 'Data belum dapat ditampilkan' : `${count ? (list.page-1)*PAGE_SIZE+1 : 0}–${Math.min(count,list.page*PAGE_SIZE)} dari ${count} hasil termuat`}</span><span>10 baris / halaman</span><button type="button" aria-label="Halaman sebelumnya" disabled={loading || !!error || list.page===1} onClick={()=>list.setPage(list.page-1)}>‹</button><strong>{list.page} / {list.pages}</strong><button type="button" aria-label="Halaman berikutnya" disabled={loading || !!error || list.page===list.pages} onClick={()=>list.setPage(list.page+1)}>›</button></div>;
}
export function DocumentActions({children}) {
  return <details className="doc-actions" onKeyDown={e=>{if(e.key==='Escape'){e.currentTarget.open=false;e.currentTarget.querySelector('summary')?.focus();}}}><summary aria-label="Aksi lainnya">•••</summary><div>{children}</div></details>;
}
// A focus boundary for the existing detail UI; nested confirmations keep their handlers.
export function useDetailDrawer(active,close,suspended=false) {
  const ref = useRef(null);
  const closeRef = useRef(close);
  closeRef.current = close;
  useEffect(()=>{
    if(!active || suspended || !ref.current) return undefined;
    const previous = document.activeElement;
    const node = ref.current;
    node.focus();
    const handler = event => {
      if(event.key==='Escape') { event.preventDefault();closeRef.current();return; }
      if(event.key!=='Tab') return;
      const items = [...node.querySelectorAll('button:not(:disabled),a[href],input:not(:disabled),select:not(:disabled),textarea:not(:disabled),summary,[tabindex="0"]')].filter(el=>el.getClientRects().length);
      if(!items.length){event.preventDefault();node.focus();return;}
      const first=items[0],last=items[items.length-1];
      if(event.shiftKey && (document.activeElement===first || document.activeElement===node)){event.preventDefault();last.focus();}
      else if(!event.shiftKey && (document.activeElement===last || !node.contains(document.activeElement))){event.preventDefault();first.focus();}
    };
    document.addEventListener('keydown',handler);
    return ()=>{document.removeEventListener('keydown',handler);if(previous?.isConnected)previous.focus();};
  },[active,suspended]);
  return ref;
}
