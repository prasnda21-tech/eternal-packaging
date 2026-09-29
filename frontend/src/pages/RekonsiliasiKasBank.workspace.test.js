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
  cashBankPairSuggestions: jest.fn(), createCashBankPair: jest.fn(), cancelCashBankPair: jest.fn(),
  previewCashBankStatement: jest.fn(), validateCashBankStatement: jest.fn(), commitCashBankStatement: jest.fn(),
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
  api.cashBankPairSuggestions.mockResolvedValue({ambiguous:true,suggestions:[{journal_line_id:'book-1',journal_number:'JE-18',reasons:['Nominal sama']}]});
  api.createCashBankPair.mockResolvedValue(pair); api.cancelCashBankPair.mockResolvedValue({status:'cancelled'});
  api.previewCashBankStatement.mockResolvedValue({fingerprint:'fp-1',account_binding:'bind-1',filename:'mutasi.csv',currency_code:'IDR',account_currency_code:'IDR',sample_rows:[['tanggal','uraian','masuk','keluar','referensi'],['2026-09-26','Transfer',750000,0,'REF-18']],persisted_statement_lines:false,stored:false});
  api.validateCashBankStatement.mockResolvedValue({ok:true,accepted_count:1,total_in:750000,total_out:0,errors:[]});
  api.commitCashBankStatement.mockResolvedValue({status:'complete'});
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
test('matched and import tabs never render a matching footer or empty bank/book panels', async () => {
  workspace.matched_pairs=[pair]; await renderPage();
  await click(byText('Sudah Cocok'));
  expect(container.querySelector('.kb-pair-footer')).toBeNull(); expect(container.querySelector('.kb-pair-columns')).toBeNull();
  expect(container.querySelector('[data-testid=kas-bank-pair-matched]').textContent).toContain('JE-18');
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
test('read-only permissions hide mutation actions', async () => {
  mockPermissions=['cash_bank.view']; workspace.matched_pairs=[pair]; await renderPage();
  expect(byText('Impor Mutasi')).toBeUndefined(); expect(byText('Cocokkan')).toBeUndefined();
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
test('validation errors remain in the drawer with row identity', async () => {
  api.validateCashBankStatement.mockResolvedValue({ok:false,errors:[{row:3,message:'Tanggal ambigu'}]}); await renderPage(); await upload(); await click(byText('Validasi'));
  expect(container.querySelector('dialog').textContent).toContain('Baris 3: Tanggal ambigu'); expect(api.commitCashBankStatement).not.toHaveBeenCalled();
});
