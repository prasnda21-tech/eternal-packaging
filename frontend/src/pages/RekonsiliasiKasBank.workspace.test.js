import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import { TextEncoder, TextDecoder } from 'util';
global.TextEncoder = global.TextEncoder || TextEncoder;
global.TextDecoder = global.TextDecoder || TextDecoder;
// Match the repository's Jest 27 router adapter; production uses the real router.
jest.mock('react-router-dom', () => {
  const React = require('react');
  return { Link: ({ to, children, ...props }) => React.createElement('a', { ...props, href: to }, children) };
}, { virtual: true });

jest.mock('../lib/api', () => ({ __esModule: true, default: {
  cashBankAccounts: jest.fn(), cashBankPairWorkspace: jest.fn(), cashBankStatementImports: jest.fn(),
  cashBankDuplicateGroups: jest.fn(), resolveCashBankStatementDuplicate: jest.fn(),
  cashBankPairSuggestions: jest.fn(), createCashBankPair: jest.fn(), cancelCashBankPair: jest.fn(),
  previewCashBankStatement: jest.fn(), previewCashBankBcaStatement: jest.fn(), commitCashBankBcaStatement: jest.fn(), validateCashBankStatement: jest.fn(), commitCashBankStatement: jest.fn(),
} }));
jest.mock('../lib/auth', () => ({ useAuth: () => ({ hasPerm: (perm) => mockPermissions.includes(perm) }) }));
const api = require('../lib/api').default;
const RekonsiliasiKasBank = require('./RekonsiliasiKasBank').default;
const originalShowModal = HTMLDialogElement.prototype.showModal;
const originalClose = HTMLDialogElement.prototype.close;
const originalActEnvironment = global.IS_REACT_ACT_ENVIRONMENT;
beforeAll(() => {
  global.IS_REACT_ACT_ENVIRONMENT = true;
  HTMLDialogElement.prototype.showModal = function () { this.setAttribute('open', ''); };
  HTMLDialogElement.prototype.close = function () { this.removeAttribute('open'); this.dispatchEvent(new Event('close')); };
});
afterAll(() => {
  HTMLDialogElement.prototype.showModal = originalShowModal;
  HTMLDialogElement.prototype.close = originalClose;
  global.IS_REACT_ACT_ENVIRONMENT = originalActEnvironment;
});
let mockPermissions;
let container, root, workspace;
const bank = { id:'bank-1', date:'2026-09-26', description:'Transfer pelanggan', reference:'TRF-18', direction:'in', amount:750000, currency_code:'IDR', matchable:true };
const book = { journal_line_id:'book-1', date:'2026-09-26', description:'Penerimaan pelanggan', journal_number:'JE-18', direction:'in', absolute_amount:750000, currency_code:'IDR', status:'posted', matchable:true };
const pair = { id:'pair-1', status:'active', review_state:'valid', statement_line_id:'bank-1', amount:750000, currency_code:'IDR', book_snapshot:{date:'2026-09-26', journal_number:'JE-18', description:'Penerimaan pelanggan'} };
const flush = () => act(async () => { await Promise.resolve(); });
const click = async (element) => { expect(element).toBeTruthy(); await act(async () => element.click()); };
const byText = (text) => [...container.querySelectorAll('button')].find((el) => el.textContent === text);
const change = async (selector, value) => { const el = container.querySelector(selector); await act(async () => { const proto = el.tagName === 'SELECT' ? HTMLSelectElement.prototype : HTMLInputElement.prototype; Object.getOwnPropertyDescriptor(proto, 'value').set.call(el, value); el.dispatchEvent(new Event('change', { bubbles:true })); }); };
async function renderPage() { await act(async () => root.render(<RekonsiliasiKasBank />)); await flush(); }
async function upload() { const input = container.querySelector('[type=file]'); await act(async () => { Object.defineProperty(input, 'files', { configurable:true, value:[new File(['tanggal,uraian,masuk,keluar\n2026-09-26,Transfer,750000,0'], 'mutasi.csv', {type:'text/csv'})] }); input.dispatchEvent(new Event('change', { bubbles:true })); }); }

beforeEach(() => {
  jest.clearAllMocks();
  mockPermissions = ['cash_bank.view','cash_bank.reconcile'];
  container = document.createElement('div'); document.body.appendChild(container); root = createRoot(container);
  workspace = {currency_code:'IDR',feature_ready:true,book_opening_balance:1000000,book_ending_balance:1750000,difference:null,statement_closing:null,unmatched_bank_count:1,unmatched_book_count:1,visible_bank_count:1,visible_book_count:1,bank_lines:[bank],book_lines:[book],matched_pairs:[]};
  api.cashBankAccounts.mockResolvedValue({items:[{id:'account-1',name:'Rekening uji',currency_code:'IDR'}]});
  api.cashBankPairWorkspace.mockImplementation(async () => workspace);
  api.cashBankStatementImports.mockResolvedValue({items:[]});
  api.cashBankDuplicateGroups.mockResolvedValue({groups:[]});
  api.resolveCashBankStatementDuplicate.mockResolvedValue({duplicate_status:'kept'});
  api.cashBankPairSuggestions.mockResolvedValue({ambiguous:true,suggestions:[{journal_line_id:'book-1',journal_number:'JE-18',reasons:['Nominal sama']}]});
  api.createCashBankPair.mockResolvedValue(pair); api.cancelCashBankPair.mockResolvedValue({status:'cancelled'});
  api.previewCashBankStatement.mockResolvedValue({fingerprint:'fp-1',account_binding:'bind-1',filename:'mutasi.csv',currency_code:'IDR',account_currency_code:'IDR',sample_rows:[['tanggal','uraian','masuk','keluar','referensi'],['2026-09-26','Transfer',750000,0,'REF-18']],persisted_statement_lines:false,stored:false});
  api.previewCashBankBcaStatement.mockResolvedValue({bank:'BCA',header_detected:true,rows:[{tanggal:'2026-09-01',keterangan:'Setoran uji',debit:1500000,kredit:null,saldo:10000000,direction:'out',source_row:3}],errors:[{row:4,message:'Tanggal tidak valid'}],persisted_statement_lines:false,stored:false});
  api.validateCashBankStatement.mockResolvedValue({ok:true,accepted_count:1,total_in:750000,total_out:0,errors:[]});
  api.commitCashBankStatement.mockResolvedValue({status:'complete'});
  api.commitCashBankBcaStatement.mockResolvedValue({status:'complete'});
});
afterEach(async () => { await act(async () => root.unmount()); container.remove(); });

test('single selection preserves match endpoint payload and highlights both rows', async () => {
  await renderPage();
  await click(container.querySelector('[name=bank-line]')); await click(container.querySelector('[name=book-line]'));
  expect(container.querySelectorAll('tr.kb-row-matched')).toHaveLength(2);
  expect(container.querySelectorAll('thead input')).toHaveLength(0);
  await click(byText('Cocokkan'));
  expect(api.createCashBankPair).toHaveBeenCalledTimes(1);
  expect(api.createCashBankPair.mock.calls[0][0]).toEqual(expect.objectContaining({cash_bank_id:'account-1',statement_line_id:'bank-1',journal_line_id:'book-1',idempotency_key:expect.any(String),start_date:expect.any(String),end_date:expect.any(String)}));
});
test('selecting a bank row highlights book candidates without saving a pair', async () => {
  const other = {...book, journal_line_id:'book-2', journal_number:'JE-19', description:'Biaya lain', absolute_amount:1000};
  workspace.book_lines=[book, other];
  workspace.visible_book_count=2;
  api.cashBankPairSuggestions.mockResolvedValue({ambiguous:false, applied:false, suggestions:[{journal_line_id:'book-1', journal_number:'JE-18', reasons:['Nominal sama','Arah sama']}]});
  await renderPage();
  expect(container.querySelector('.kb-candidate-badge')).toBeNull();
  await click(container.querySelector('[name=bank-line]'));
  await flush();
  await flush();
  const candidate = container.querySelector('[data-testid=kas-bank-pair-book-table] tr.kb-row-candidate');
  expect(candidate).toBeTruthy();
  expect(candidate.textContent).toContain('JE-18');
  expect(candidate.textContent).toContain('Kandidat Cocok');
  expect(candidate.textContent).toContain('✓ Nominal sama');
  expect(candidate.textContent).toContain('✓ Arah sesuai');
  expect(candidate.textContent).toContain('✓ Rekening sama');
  const bankCandidate = container.querySelector('[data-testid=kas-bank-pair-bank-table] tr.kb-row-candidate');
  expect(bankCandidate).toBeTruthy();
  expect(bankCandidate.textContent).toContain('Kandidat Cocok');
  expect(bankCandidate.textContent).toContain('✓ Nominal sama');
  expect(container.querySelector('[data-testid=kas-bank-pair-link]').textContent).toContain('↔');
  const bookRows = [...container.querySelectorAll('[data-testid=kas-bank-pair-book-table] tbody tr')];
  expect(bookRows[1].classList.contains('kb-row-candidate')).toBe(false);
  expect(bookRows[1].textContent).not.toContain('Kandidat Cocok');
  expect(container.querySelector('[name=book-line]').checked).toBe(false);
  expect(api.createCashBankPair).not.toHaveBeenCalled();
  expect(container.querySelector('.recon-badge.is-matched')).toBeNull();
});
test('off-page suggestions stay announced and duplicate rows stay unmarked', async () => {
  workspace.bank_lines=[{...bank, id:'bank-dup', matchable:false, block_reason:'Dugaan duplikat belum diputuskan'}, bank];
  workspace.book_lines=[book];
  api.cashBankPairSuggestions.mockResolvedValue({ambiguous:true, applied:false, suggestions:[{journal_line_id:'book-1', journal_number:'JE-18', reasons:['Nominal sama']},{journal_line_id:'book-off', journal_number:'JE-99', reasons:['Nominal sama']}]});
  await renderPage();
  const dupRow = container.querySelector('[data-testid=kas-bank-pair-bank-table] tbody tr');
  expect(dupRow.textContent).toContain('Dugaan duplikat belum diputuskan');
  expect(dupRow.classList.contains('kb-row-candidate')).toBe(false);
  await click(container.querySelectorAll('[name=bank-line]')[1]);
  await flush();
  await flush();
  expect(container.querySelector('[data-testid=kas-bank-offpage-candidates]').textContent).toBe('1 kandidat tersedia pada halaman lain');
  expect(container.querySelector('[data-testid=kas-bank-pair-book-table] tr.kb-row-candidate')).toBeTruthy();
  expect(container.querySelector('[name=book-line]').checked).toBe(false);
  expect(api.createCashBankPair).not.toHaveBeenCalled();
});
test('matched and import tabs never render a matching footer or empty bank/book panels', async () => {
  workspace.matched_pairs=[pair]; await renderPage();
  await click(byText('Sudah Cocok'));
  expect(container.querySelector('.kb-pair-footer')).toBeNull(); expect(container.querySelector('.kb-pair-columns')).toBeNull();
  expect(container.querySelector('[data-testid=kas-bank-pair-matched]').textContent).toContain('JE-18');
  const matchedBadge = container.querySelector('.recon-badge.is-matched');
  expect(matchedBadge.textContent).toBe('✓ Cocok');
  expect(container.querySelector('tr.kb-row-reconciled')).toBeTruthy();
  await click(byText('Batalkan pencocokan'));
  expect(api.cancelCashBankPair.mock.calls[0]).toEqual(['pair-1', expect.objectContaining({reason:'Batalkan pencocokan',idempotency_key:expect.any(String)})]);
  await click(byText('Riwayat Impor'));
  expect(container.querySelector('.kb-pair-footer')).toBeNull(); expect(container.querySelector('.kb-pair-columns')).toBeNull();
});
test('mapping uses named columns with sample values and sends unchanged numeric column indices', async () => {
  await renderPage(); await upload();
  const dialog=container.querySelector('dialog'); expect(dialog.open).toBe(true);
  expect(dialog.textContent).toContain('1. tanggal · 2026-09-26'); expect(dialog.textContent).toContain('halaman ini terbuka');
  expect(dialog.querySelector('input[type=number]')).toBeNull();
  await click(byText('Validasi'));
  const validated = api.validateCashBankStatement.mock.calls[0][0];
  expect(validated).toBeInstanceOf(FormData);
  expect(validated.get('cash_bank_id')).toBe('account-1');
  expect(validated.get('fingerprint')).toBe('fp-1');
  expect(validated.get('account_binding')).toBe('bind-1');
  expect(validated.get('account_currency_code')).toBeNull();
  expect(validated.get('file')).toBeTruthy();
  expect(JSON.parse(validated.get('mapping'))).toEqual(expect.objectContaining({date_column:0,description_column:1,in_column:2,out_column:3,reference_column:null,bank_txn_id_column:null,amount_mode:'in_out'}));
  await click(byText('Konfirmasi impor'));
  const committed = api.commitCashBankStatement.mock.calls[0][0];
  expect(committed).toBeInstanceOf(FormData);
  expect(committed.get('fingerprint')).toBe('fp-1');
  expect(committed.get('account_binding')).toBe('bind-1');
  expect(committed.get('cash_bank_id')).toBe('account-1');
  expect(JSON.parse(committed.get('mapping'))).toEqual(JSON.parse(validated.get('mapping')));
  expect(committed.get('file')).toBeTruthy();
  expect(dialog.open).toBe(false);
});
test('close/reopen preview preserves mapping and never confirms import', async () => {
  await renderPage(); await upload();
  const dateSelect=[...container.querySelectorAll('dialog label')].find(el=>el.textContent.startsWith('Tanggal')).querySelector('select');
  await act(async () => { dateSelect.value='4'; dateSelect.dispatchEvent(new Event('change',{bubbles:true})); });
  await click(container.querySelector('[aria-label="Tutup pratinjau impor"]'));
  await click(byText('Lanjutkan impor'));
  expect(dateSelect.value).toBe('4'); expect(api.commitCashBankStatement).not.toHaveBeenCalled();
});
test('bca preview dialog shows parsed rows without confirming import', async () => {
  await renderPage();
  expect(container.querySelectorAll('[type=file]')[0].getAttribute('data-testid')).toBe('kas-bank-pair-import');
  const bcaInput = container.querySelector('[data-testid=kas-bank-bca-file]');
  await act(async () => { Object.defineProperty(bcaInput, 'files', { configurable:true, value:[new File(['tanggal,keterangan,debit,kredit,saldo'], 'bca-uji.csv', {type:'text/csv'})] }); bcaInput.dispatchEvent(new Event('change', { bubbles:true })); });
  const dialog = container.querySelector('[data-testid=kas-bank-bca-preview]');
  expect(dialog.open).toBe(true);
  expect(dialog.textContent).toContain('Setoran uji');
  expect(dialog.textContent).toContain('1.500.000');
  expect(dialog.textContent).toContain('10.000.000');
  expect(dialog.textContent).toContain('Baris 4: Tanggal tidak valid');
  expect(dialog.textContent).not.toContain('Konfirmasi impor');
  expect(api.previewCashBankBcaStatement).toHaveBeenCalledTimes(1);
  expect(api.previewCashBankBcaStatement.mock.calls[0][0].get('cash_bank_id')).toBe('account-1');
  expect(api.previewCashBankStatement).not.toHaveBeenCalled();
  expect(api.commitCashBankStatement).not.toHaveBeenCalled();
  await upload();
  const generic = container.querySelector('dialog.recon-import-dialog:not(.recon-bca-dialog)');
  expect(generic.open).toBe(true);
  expect(generic.textContent).toContain('Konfirmasi impor');
  expect(generic.textContent).not.toContain('Setoran uji');
});
test('bca pend imports without a confirmation form and shows the temporary date', async () => {
  api.previewCashBankBcaStatement.mockResolvedValue({bank:'BCA',header_detected:true,fingerprint:'fp-pend',account_binding:'bind-pend',rows:[{tanggal:'2026-09-30',original_date:'PEND',date_source:'import_date',date_status:'temporary',keterangan:'TRSF E-BANKING CR 2909/FTSCY',debit:null,kredit:1500,saldo:9000,direction:'in',source_row:3}],errors:[],persisted_statement_lines:false,stored:false});
  workspace.bank_lines=[{...bank,original_date:'PEND',date_source:'import_date',date_status:'temporary',description:'TRSF E-BANKING CR 2909/FTSCY',date:'2026-09-30'}];
  await renderPage();
  expect(container.textContent).toContain('PEND - tanggal sementara');
  const bcaInput = container.querySelector('[data-testid=kas-bank-bca-file]');
  await act(async () => { Object.defineProperty(bcaInput, 'files', { configurable:true, value:[new File(['PEND'], 'bca-pend.csv', {type:'text/csv'})] }); bcaInput.dispatchEvent(new Event('change', { bubbles:true })); });
  const dialog = container.querySelector('[data-testid=kas-bank-bca-preview]');
  expect(dialog.textContent).toContain('PEND - tanggal sementara');
  expect(dialog.textContent).toContain('30 Sep 2026');
  expect(dialog.textContent).not.toContain('Konfirmasi PEND');
  expect(dialog.textContent).not.toContain('Tanggal belum pasti');
  const importButton = dialog.querySelector('[data-testid=kas-bank-bca-import]');
  expect(importButton.disabled).toBe(false);
  await click(importButton);
  const sent = api.commitCashBankBcaStatement.mock.calls[0][0];
  expect(sent.get('confirmations')).toBeNull();
  expect(sent.get('account_binding')).toBe('bind-pend');
  expect(api.commitCashBankStatement).not.toHaveBeenCalled();
});
test('read-only permissions hide mutation actions', async () => {
  mockPermissions=['cash_bank.view']; workspace.matched_pairs=[pair]; await renderPage();
  expect(byText('Impor Mutasi')).toBeUndefined(); expect(byText('Pratinjau CSV BCA')).toBeUndefined(); expect(byText('Cocokkan')).toBeUndefined();
  expect(container.querySelector('[name=bank-line]').disabled).toBe(true);
  await click(byText('Sudah Cocok')); expect(byText('Batalkan pencocokan')).toBeUndefined();
});
test('missing balance stays unavailable and USD is never presented as rupiah', async () => {
  workspace.currency_code='USD'; workspace.bank_lines=[{...bank,currency_code:'USD'}]; workspace.book_lines=[{...book,currency_code:'USD'}]; await renderPage();
  const summary=container.querySelector('.kb-pair-summary').textContent;
  expect(summary).toContain('USD'); expect(summary).not.toContain('Rp'); expect(summary).toContain('Belum tersedia');
  expect(container.querySelector('.recon-metric-diff strong').textContent).toBe('Belum tersedia');
});
test('server block reasons and source review state remain visible', async () => {
  workspace.bank_lines=[{...bank,matchable:false,block_reason:'Duplikat perlu tinjauan'}]; workspace.matched_pairs=[{...pair,review_state:'perlu_tinjauan'}]; await renderPage();
  expect(container.textContent).toContain('Duplikat perlu tinjauan'); expect(container.querySelector('[name=bank-line]').disabled).toBe(true);
  await click(byText('Sudah Cocok')); expect(container.textContent).toContain('Perlu tinjauan');
});
test('changing period clears selections before another match can be submitted', async () => {
  await renderPage(); await click(container.querySelector('[name=bank-line]')); await click(container.querySelector('[name=book-line]'));
  await change('[aria-label="Tanggal awal"]', container.querySelector('[aria-label="Tanggal awal"]').value === '2026-09-02' ? '2026-09-03' : '2026-09-02');
  expect(container.querySelector('[name=bank-line]').checked).toBe(false); expect(byText('Cocokkan').disabled).toBe(true);
});
const duplicateGroup = (overrides = {}) => ({
  description:'PEMINDAHAN DANA RAKHA EDY SYAH', business_date:'2026-09-28', direction:'out', amount:70000000,
  normalized_description:'pemindahan dana rakha edy syah', candidate_count:2,
  lines:[
    {statement_line_id:'line-a', import_id:'imp-a', filename:'mutasi-2809.csv', imported_at:'2026-09-28T02:14:00Z', date:'2026-09-28', amount:70000000, direction:'out', duplicate_status:'pending_review', active_claim:null, reconciliation_status:'unmatched'},
    {statement_line_id:'line-b', import_id:'imp-b', filename:'mutasi-2809-ulang.csv', imported_at:'2026-09-29T01:02:00Z', date:'2026-09-28', amount:70000000, direction:'out', duplicate_status:'pending_review', active_claim:null, reconciliation_status:'unmatched'},
  ],
  ...overrides,
});
const fillReason = async (lineId, value) => change(`[data-testid="duplicate-reason-${lineId}"]`, value);

const keptLine = (line, reason = 'transaksi asli') => ({...line, duplicate_status:'kept', resolution:{action:'keep', reason, at:'2026-09-30T06:00:00Z', actor:{id:'u-1', name:'Operator Uji'}}});
const excludedLine = (line, reason = 'salinan impor ulang') => ({...line, duplicate_status:'excluded', resolution:{action:'exclude', reason, at:'2026-09-30T07:00:00Z', actor:{id:'u-1', name:'Operator Uji'}}});
const remount = async () => {
  await act(async () => root.unmount());
  container.remove();
  container = document.createElement('div');
  document.body.appendChild(container);
  root = createRoot(container);
  await renderPage();
};

test('duplicate review tab shows candidates and posts keep then refreshes', async () => {
  const openPair = duplicateGroup();
  const afterKeep = duplicateGroup({lines:[keptLine(openPair.lines[0]), openPair.lines[1]]});
  api.cashBankDuplicateGroups.mockResolvedValueOnce({groups:[openPair]}).mockResolvedValue({groups:[afterKeep]});
  await renderPage();
  await click(byText('Review Duplikat'));
  const panel = container.querySelector('[data-testid=kas-bank-duplicate-review]');
  expect(panel.textContent).toContain('PEMINDAHAN DANA RAKHA EDY SYAH');
  expect(panel.textContent).toContain('Rp 70.000.000');
  expect(panel.textContent).toContain('28 Sep 2026');
  expect(panel.textContent).toContain('Line A');
  expect(panel.textContent).toContain('mutasi-2809.csv');
  expect(panel.textContent).toContain('Line B');
  expect(panel.textContent).toContain('mutasi-2809-ulang.csv');
  expect(panel.textContent).toContain('pending_review');
  expect(panel.textContent).toContain('belum dicocokkan');
  expect(panel.querySelector('input[type=radio]')).toBeNull();
  expect(panel.textContent).not.toContain('Hapus');
  expect(panel.textContent).not.toContain('Gabungkan');
  expect(panel.textContent).not.toContain('disarankan');
  expect(container.querySelector('[data-testid=duplicate-keep-line-a]').disabled).toBe(true);
  await fillReason('line-a', 'transaksi asli');
  await click(container.querySelector('[data-testid=duplicate-keep-line-a]'));
  expect(api.resolveCashBankStatementDuplicate).toHaveBeenCalledWith('line-a', {action:'keep', reason:'transaksi asli', idempotency_key:expect.any(String)});
  expect(api.cashBankDuplicateGroups.mock.calls.length).toBeGreaterThan(1);
  expect(api.cashBankPairWorkspace.mock.calls.length).toBeGreaterThan(1);
  const stayed = container.querySelector('[data-testid=kas-bank-duplicate-review]');
  expect(stayed.textContent).toContain('Line B');
  expect(stayed.textContent).toContain('Sudah dipertahankan');
  expect(stayed.textContent).toContain('Operator Uji');
  expect(container.querySelector('[data-testid=duplicate-keep-line-a]')).toBeNull();
  expect(container.querySelector('[data-testid=duplicate-keep-line-b]')).toBeTruthy();
  expect(container.querySelector('[data-testid=duplicate-exclude-line-b]')).toBeTruthy();
});
test('exclude leaves the other candidate visible and a finished group leaves the queue', async () => {
  const openPair = duplicateGroup();
  const afterExclude = duplicateGroup({lines:[openPair.lines[0], excludedLine(openPair.lines[1])]});
  api.cashBankDuplicateGroups.mockResolvedValueOnce({groups:[openPair]}).mockResolvedValueOnce({groups:[afterExclude]}).mockResolvedValue({groups:[]});
  await renderPage();
  await click(byText('Review Duplikat'));
  await fillReason('line-b', 'salinan impor ulang');
  await click(container.querySelector('[data-testid=duplicate-exclude-line-b]'));
  expect(api.resolveCashBankStatementDuplicate).toHaveBeenCalledWith('line-b', {action:'exclude', reason:'salinan impor ulang', idempotency_key:expect.any(String)});
  const stayed = container.querySelector('[data-testid=kas-bank-duplicate-review]');
  expect(stayed.textContent).toContain('Riwayat pengecualian');
  expect(stayed.textContent).toContain('Operator Uji');
  expect(container.querySelector('[data-testid=duplicate-exclude-line-b]')).toBeNull();
  expect(container.querySelector('[data-testid=duplicate-keep-line-a]')).toBeTruthy();
  await fillReason('line-a', 'baris yang dipakai');
  await click(container.querySelector('[data-testid=duplicate-keep-line-a]'));
  expect(container.querySelector('[data-testid=kas-bank-duplicate-review]').textContent).toContain('Tidak ada kelompok duplikat pada periode ini.');
});
test('refresh after the first decision still shows the remaining candidate', async () => {
  const openPair = duplicateGroup();
  api.cashBankDuplicateGroups.mockResolvedValue({groups:[duplicateGroup({lines:[keptLine(openPair.lines[0]), openPair.lines[1]]})]});
  await renderPage();
  await click(byText('Review Duplikat'));
  expect(container.querySelector('[data-testid=duplicate-line-line-b]').textContent).toContain('pending_review');
  expect(container.querySelector('[data-testid=duplicate-keep-line-b]')).toBeTruthy();
  expect(container.querySelector('[data-testid=duplicate-resolution-line-a]').textContent).toContain('transaksi asli');
});
test('a failed decision retries with the same idempotency key', async () => {
  api.cashBankDuplicateGroups.mockResolvedValue({groups:[duplicateGroup()]});
  api.resolveCashBankStatementDuplicate.mockRejectedValueOnce(new Error('jaringan putus')).mockResolvedValue({duplicate_status:'kept'});
  await renderPage();
  await click(byText('Review Duplikat'));
  await fillReason('line-a', 'transaksi asli');
  await click(container.querySelector('[data-testid=duplicate-keep-line-a]'));
  expect(container.textContent).toContain('jaringan putus');
  await click(container.querySelector('[data-testid=duplicate-keep-line-a]'));
  const [first, second] = api.resolveCashBankStatementDuplicate.mock.calls;
  expect(first[1].idempotency_key).toBe(second[1].idempotency_key);
  expect(first[1]).toEqual(second[1]);
});
test('exclude posts the existing resolution endpoint and unsafe groups keep the button disabled', async () => {
  api.cashBankDuplicateGroups.mockResolvedValue({groups:[duplicateGroup()]});
  await renderPage();
  await click(byText('Review Duplikat'));
  await fillReason('line-b', 'salinan impor ulang');
  await click(container.querySelector('[data-testid=duplicate-exclude-line-b]'));
  expect(api.resolveCashBankStatementDuplicate).toHaveBeenCalledWith('line-b', {action:'exclude', reason:'salinan impor ulang', idempotency_key:expect.any(String)});
  expect(api.cashBankDuplicateGroups.mock.calls.length).toBeGreaterThan(1);
});
test('exclude stays disabled for same file, three candidates, a claim, or an active match', async () => {
  const sameFile = duplicateGroup();
  sameFile.lines[1] = {...sameFile.lines[1], import_id:'imp-a'};
  api.cashBankDuplicateGroups.mockResolvedValueOnce({groups:[sameFile]});
  await renderPage();
  await click(byText('Review Duplikat'));
  await fillReason('line-a', 'dua baris satu berkas');
  expect(container.querySelector('[data-testid=duplicate-exclude-line-a]').disabled).toBe(true);
  expect(container.querySelector('[data-testid=duplicate-keep-line-a]').disabled).toBe(false);
  expect(container.textContent).toContain('Kandidat berasal dari berkas impor yang sama.');

  const crowded = duplicateGroup({candidate_count:3, lines:[...duplicateGroup().lines, {statement_line_id:'line-c', import_id:'imp-c', filename:'mutasi-ketiga.csv', imported_at:'2026-09-30T01:00:00Z', date:'2026-09-28', amount:70000000, direction:'out', duplicate_status:'pending_review', active_claim:null, reconciliation_status:'unmatched'}]});
  api.cashBankDuplicateGroups.mockResolvedValueOnce({groups:[crowded]});
  await act(async () => root.unmount());
  container.remove();
  container = document.createElement('div'); document.body.appendChild(container); root = createRoot(container);
  await renderPage();
  await click(byText('Review Duplikat'));
  await fillReason('line-a', 'tiga kandidat');
  expect(container.querySelector('[data-testid=duplicate-exclude-line-a]').disabled).toBe(true);
  expect(container.textContent).toContain('Kandidat lebih dari dua.');

  const claimed = duplicateGroup();
  claimed.lines[0] = {...claimed.lines[0], active_claim:'claim-1'};
  api.cashBankDuplicateGroups.mockResolvedValueOnce({groups:[claimed]});
  await act(async () => root.unmount());
  container.remove();
  container = document.createElement('div'); document.body.appendChild(container); root = createRoot(container);
  await renderPage();
  await click(byText('Review Duplikat'));
  await fillReason('line-a', 'sedang diklaim');
  await fillReason('line-b', 'saudara belum diklaim');
  expect(container.querySelector('[data-testid=duplicate-keep-line-a]').disabled).toBe(true);
  expect(container.querySelector('[data-testid=duplicate-exclude-line-a]').disabled).toBe(true);
  expect(container.querySelector('[data-testid=duplicate-exclude-line-b]').disabled).toBe(true);
  expect(container.querySelector('[data-testid=duplicate-keep-line-b]').disabled).toBe(false);

  const matched = duplicateGroup();
  matched.lines[0] = {...matched.lines[0], reconciliation_status:'active', reconciliation_match_id:'match-1'};
  api.cashBankDuplicateGroups.mockResolvedValueOnce({groups:[matched]});
  await act(async () => root.unmount());
  container.remove();
  container = document.createElement('div'); document.body.appendChild(container); root = createRoot(container);
  await renderPage();
  await click(byText('Review Duplikat'));
  await fillReason('line-b', 'masih ada pasangan');
  expect(container.querySelector('[data-testid=duplicate-exclude-line-b]').disabled).toBe(true);
  expect(container.querySelector('[data-testid=duplicate-keep-line-b]').disabled).toBe(false);
  expect(container.textContent).toContain('Ada pencocokan aktif.');
});
test('safety rules still use the whole group after the first decision', async () => {
  const openPair = duplicateGroup();
  const sameFile = duplicateGroup({lines:[keptLine({...openPair.lines[0], import_id:'imp-a'}), {...openPair.lines[1], import_id:'imp-a'}]});
  api.cashBankDuplicateGroups.mockResolvedValueOnce({groups:[sameFile]});
  await renderPage();
  await click(byText('Review Duplikat'));
  await fillReason('line-b', 'masih satu berkas');
  expect(container.querySelector('[data-testid=duplicate-exclude-line-b]').disabled).toBe(true);
  expect(container.querySelector('[data-testid=duplicate-keep-line-b]').disabled).toBe(false);
  expect(container.textContent).toContain('Kandidat berasal dari berkas impor yang sama.');

  const third = {statement_line_id:'line-c', import_id:'imp-c', filename:'mutasi-ketiga.csv', imported_at:'2026-09-30T01:00:00Z', date:'2026-09-28', amount:70000000, direction:'out', duplicate_status:'pending_review', active_claim:null, reconciliation_status:'unmatched'};
  const crowded = duplicateGroup({candidate_count:3, lines:[keptLine(openPair.lines[0]), openPair.lines[1], third]});
  api.cashBankDuplicateGroups.mockResolvedValueOnce({groups:[crowded]});
  await remount();
  await click(byText('Review Duplikat'));
  await fillReason('line-b', 'masih tiga kandidat');
  expect(container.querySelector('[data-testid=duplicate-exclude-line-b]').disabled).toBe(true);
  expect(container.textContent).toContain('Kandidat lebih dari dua.');

  const claimed = duplicateGroup({lines:[keptLine({...openPair.lines[0], active_claim:'claim-1'}), openPair.lines[1]]});
  api.cashBankDuplicateGroups.mockResolvedValueOnce({groups:[claimed]});
  await remount();
  await click(byText('Review Duplikat'));
  await fillReason('line-b', 'saudara masih diklaim');
  expect(container.querySelector('[data-testid=duplicate-exclude-line-b]').disabled).toBe(true);
  expect(container.querySelector('[data-testid=duplicate-keep-line-b]').disabled).toBe(false);

  const matched = duplicateGroup({lines:[keptLine({...openPair.lines[0], reconciliation_status:'active'}), openPair.lines[1]]});
  api.cashBankDuplicateGroups.mockResolvedValueOnce({groups:[matched]});
  await remount();
  await click(byText('Review Duplikat'));
  await fillReason('line-b', 'masih ada pasangan');
  expect(container.querySelector('[data-testid=duplicate-exclude-line-b]').disabled).toBe(true);
  expect(container.querySelector('[data-testid=duplicate-keep-line-b]').disabled).toBe(false);
  expect(container.textContent).toContain('Ada pencocokan aktif.');
});
test('read-only duplicate review hides keep and exclude', async () => {
  mockPermissions = ['cash_bank.view'];
  api.cashBankDuplicateGroups.mockResolvedValue({groups:[duplicateGroup()]});
  await renderPage();
  await click(byText('Review Duplikat'));
  expect(container.querySelector('[data-testid=kas-bank-duplicate-review]').textContent).toContain('Line A');
  expect(byText('Pertahankan')).toBeUndefined();
  expect(byText('Kecualikan')).toBeUndefined();
});
test('validation errors remain in the drawer with row identity', async () => {
  api.validateCashBankStatement.mockResolvedValue({ok:false,errors:[{row:3,message:'Tanggal ambigu'}]}); await renderPage(); await upload(); await click(byText('Validasi'));
  expect(container.querySelector('dialog').textContent).toContain('Baris 3: Tanggal ambigu'); expect(api.commitCashBankStatement).not.toHaveBeenCalled();
});
