import React, {act} from 'react';
import {createRoot} from 'react-dom/client';
import FakturPenjualan from './FakturPenjualan';
import PenerimaanPenjualan from './PenerimaanPenjualan';
import api from '../lib/api';
import {filterDocuments,documentStatus,summarizeDocuments,moneyText} from './salesDocumentsView';
const mockSearch = new URLSearchParams();
jest.mock('react-router-dom',()=>({Link:({children,to,...props})=><a href={to} {...props}>{children}</a>,useNavigate:()=>jest.fn(),useSearchParams:()=>[mockSearch,jest.fn()]}),{virtual:true});
jest.mock('../lib/auth',()=>({useAuth:()=>({user:{role:'staff'},hasPerm:p=>(global.__docPerms||[]).includes(p)})}),{virtual:true});
jest.mock('../lib/api',()=>({__esModule:true,default:{listSalesInvoices:jest.fn(),listSalesReceipts:jest.fn(),getSalesInvoice:jest.fn(),getSalesReceipt:jest.fn(),salesInvoiceChargeAccounts:jest.fn(),deleteSalesInvoice:jest.fn(),bulkDeleteSalesInvoices:jest.fn()}}),{virtual:true});
jest.mock('../lib/constants',()=>({formatDisplayDate:v=>v||'—'}),{virtual:true});
jest.mock('../components/form-standard',()=>({FormSection:({children})=><section>{children}</section>,FormField:({children})=><div>{children}</div>,FormProgress:()=>null,computeStatus:()=>''}),{virtual:true});
jest.mock('../lib/salesReceipts',()=>({filterEligibleInvoices:rows=>rows,resolvePreselectedInvoiceId:()=>'',computeReceiptPreview:()=>null}),{virtual:true});
jest.mock('../lib/salesReceiptPaymentProof',()=>({RECEIPT_PROOF_ACCEPT:'.pdf',buildReceiptProofPayload:()=>({}),validateReceiptProofFile:()=>''}),{virtual:true});
jest.mock('./SalesReceiptProofViewer',()=>()=>null,{virtual:true});
let container,root;
const invoices=Array.from({length:12},(_,i)=>({id:`i${i}`,customer:`Pelanggan ${i}`,invoice_number:`INV-${i}`,po_id:`po${i}`,po_number:`PO-${i}`,financial_status:'draft',invoice_date:'2026-09-01',grand_total:100,outstanding_amount:100,paid_total:0,line_items:[]}));
beforeEach(()=>{global.IS_REACT_ACT_ENVIRONMENT=true;global.__docPerms=['sales.invoice.create'];container=document.createElement('div');document.body.append(container);root=createRoot(container);jest.clearAllMocks();api.listSalesInvoices.mockResolvedValue({invoices});api.listSalesReceipts.mockResolvedValue({receipts:[]});api.salesInvoiceChargeAccounts.mockResolvedValue({items:[]});api.deleteSalesInvoice.mockResolvedValue({});});
afterEach(async()=>{await act(async()=>root.unmount());container.remove();});
const click=async selector=>act(async()=>container.querySelector(selector).click());
const change=async(selector,value)=>act(async()=>{const el=container.querySelector(selector);Object.getOwnPropertyDescriptor(el instanceof HTMLSelectElement?HTMLSelectElement.prototype:HTMLInputElement.prototype,'value').set.call(el,value);el.dispatchEvent(new Event(el instanceof HTMLSelectElement?'change':'input',{bubbles:true}));});
test('10 rows and select-all remain scoped to the visible draft page',async()=>{
 await act(async()=>root.render(<FakturPenjualan/>));
 expect(container.querySelectorAll('[data-testid^="invoice-row-"]')).toHaveLength(10);
 await click('[data-testid="select-all-invoices"]');
 expect(container.querySelectorAll('tbody input:checked')).toHaveLength(10);
 await click('[aria-label="Halaman berikutnya"]');
 expect(container.querySelectorAll('[data-testid^="invoice-row-"]')).toHaveLength(2);
 expect(container.querySelectorAll('tbody input:checked')).toHaveLength(0);
});
test('column filters return matches beyond the first page and reset pagination',async()=>{
 await act(async()=>root.render(<FakturPenjualan/>));await click('[aria-label="Halaman berikutnya"]');
 await change('[aria-label="Filter kolom Pelanggan / No. Faktur"]','Pelanggan 0');
 expect(container.querySelectorAll('[data-testid^="invoice-row-"]')).toHaveLength(1);
 expect(container.querySelector('[data-testid="invoice-row-i0"]')).not.toBeNull();
});
test('issued invoice uses dedicated delete permission and hides manual posting',async()=>{
 const issued={...invoices[0],issue_state:'issued',financial_status:'finalized',accounting_status:'posted'};
 api.listSalesInvoices.mockResolvedValue({invoices:[issued]});api.getSalesInvoice.mockResolvedValue({invoice:issued});
 await act(async()=>root.render(<FakturPenjualan/>));
 expect(container.querySelector('[data-testid="btn-delete-invoice-i0"]')).toBeNull();
 await click('[data-testid="view-invoice-btn-i0"]');
 expect(container.querySelector('[role="dialog"]')).not.toBeNull();
 expect(container.querySelector('[data-testid="btn-finalize-invoice"]')).toBeNull();
 expect(container.querySelector('[data-testid="btn-post-accounting"]')).toBeNull();
 expect(container.querySelector('[data-testid="btn-delete-issued-invoice"]')).toBeNull();
 await act(async()=>document.dispatchEvent(new KeyboardEvent('keydown',{key:'Escape',bubbles:true})));
 expect(container.querySelector('[data-testid="invoices-table"]')).not.toBeNull();
});
test('delete-authorized issued invoice preserves API operation key and blocker message',async()=>{
 global.__docPerms=['sales.invoice.delete'];
 api.listSalesInvoices.mockResolvedValue({invoices:[{...invoices[0],issue_state:'issued',financial_status:'finalized'}]});
 api.deleteSalesInvoice.mockRejectedValue({response:{data:{detail:{message:'Penerimaan aktif',blockers:[{number:'RCV-1',amount:100}]}}}});
 await act(async()=>root.render(<FakturPenjualan/>));await click('[data-testid="btn-delete-invoice-i0"]');
 expect(api.deleteSalesInvoice).toHaveBeenCalledWith('i0',expect.any(String));
 expect(container.querySelector('[data-testid="delete-error"]').textContent).toContain('RCV-1');
});
test('receipt page has 10 rows and detail drawer retains authoritative allocations',async()=>{
 const receipts=Array.from({length:12},(_,i)=>({id:`r${i}`,receipt_number:`RCV-${i}`,customer:'Kopi',amount:100,allocated_amount:60,financial_status:'active',receipt_date:'2026-09-01',can_delete:false}));
 api.listSalesReceipts.mockResolvedValue({receipts});api.getSalesReceipt.mockResolvedValue({receipt:receipts[0]});
 await act(async()=>root.render(<PenerimaanPenjualan/>));
 expect(container.querySelectorAll('tbody tr')).toHaveLength(10);
 await click('[data-testid="receipt-number-r0"]');
 expect(container.querySelector('[role="dialog"]').textContent).toContain('Rp 60');
});
test('summary excludes cancelled, pending and draft invoices without deriving cash from balance',()=>{
 const good={financial_status:'finalized',issue_state:'issued',grand_total:100,paid_total:30,outstanding_amount:70};
 expect(summarizeDocuments([good,{...good,issue_state:'issuing'},{...good,financial_status:'draft'},{...good,financial_status:'cancelled'}],'invoice')).toEqual([100,30,70]);
 expect(summarizeDocuments([{...good,paid_total:undefined}],'invoice')[1]).toBeNull();
 expect(moneyText(undefined)).toBe('—');expect(moneyText(0)).toBe('Rp 0');
});
test('receipt summary excludes reversals, uses legacy allocation fallback, and does not claim available credit',()=>{
 expect(summarizeDocuments([{amount:100,allocated_amount:60},{amount:40},{amount:100,accounting_status:'reversed'},{amount:100,financial_status:'cancelled'}],'receipt')).toEqual([140,100]);
});
test('processing invoices never appear in unpaid tabs; inclusive date range excludes missing dates',()=>{
 expect(documentStatus({issue_state:'issuing',payment_status:'LUNAS'},'invoice')).toBe('processing');
 expect(filterDocuments([{id:1,invoice_date:'2026-09-01'},{id:2,invoice_date:'2026-09-29'},{id:3}], 'invoice',{start:'2026-09-01',end:'2026-09-29'}).map(r=>r.id)).toEqual([1,2]);
});

test('cancelled issued invoices retain cancellation label and localized status filter',async()=>{
 const row={...invoices[0],issue_state:'issued',financial_status:'cancelled'};
 api.listSalesInvoices.mockResolvedValue({invoices:[row]});
 await act(async()=>root.render(<FakturPenjualan/>));
 expect(container.querySelector('[data-testid="invoice-row-i0"]').textContent).not.toContain('Terbit');
 expect(filterDocuments([row],'invoice',{columns:{financial_status:'Dibatalkan'}})).toHaveLength(1);
 expect(filterDocuments([{...row,financial_status:'finalized'}],'invoice',{columns:{financial_status:'Terbit'}})).toHaveLength(1);
});


test.each(['invoice','receipt'])('five/ten row selection resets %s pagination and keeps remaining rows reachable',async kind=>{
 const receipts=Array.from({length:12},(_,i)=>({id:`r${i}`,receipt_number:`RCV-${i}`,customer:'Kopi',amount:100,receipt_date:'2026-09-01'}));
 api.listSalesReceipts.mockResolvedValue({receipts});
 await act(async()=>root.render(kind==='invoice'?<FakturPenjualan/>:<PenerimaanPenjualan/>));
 await click('[aria-label="Halaman berikutnya"]');
 await change('[aria-label="Jumlah baris per halaman"]','5');
 expect(container.querySelectorAll('tbody tr')).toHaveLength(5);
 expect(container.querySelector('.doc-pager').textContent).toContain('1–5 dari 12');
 await click('[aria-label="Halaman berikutnya"]');
 expect(container.querySelector('.doc-pager').textContent).toContain('6–10 dari 12');
 await change('[aria-label="Jumlah baris per halaman"]','10');
 expect(container.querySelectorAll('tbody tr')).toHaveLength(10);
 expect(container.querySelector('.doc-pager').textContent).toContain('1–10 dari 12');
});
