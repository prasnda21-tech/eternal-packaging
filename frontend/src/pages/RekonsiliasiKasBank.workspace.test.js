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
  cashBankDuplicateGroups: jest.fn(), cashBankPendingReview: jest.fn(), resolveCashBankStatementDuplicate: jest.fn(),
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
async function renderPage() {
  await act(async () => root.render(<RekonsiliasiKasBank />));
  await flush();
  await flush();
  await flush();
}
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
  api.cashBankPendingReview.mockResolvedValue({solo_lines:[], outside_period:[], unreachable:[], other_accounts:[], truncated:false});
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
  expect(container.querySelector('[data-testid=kas-bank-match-hint]').textContent).toBe('Belum memilih transaksi bank');
  await click(container.querySelector('[name=bank-line]')); await click(container.querySelector('[name=book-line]'));
  expect(container.querySelectorAll('tr.kb-row-selected')).toHaveLength(2);
  expect(container.querySelector('[data-testid=kas-bank-match-hint]').textContent).toBe('Satu transaksi di setiap sisi');
  expect(container.querySelectorAll('thead input')).toHaveLength(0);
  await click(byText('Cocokkan'));
  expect(api.createCashBankPair).toHaveBeenCalledTimes(1);
  expect(api.createCashBankPair.mock.calls[0][0]).toEqual(expect.objectContaining({cash_bank_id:'account-1',statement_line_id:'bank-1',journal_line_id:'book-1',idempotency_key:expect.any(String),start_date:expect.any(String),end_date:expect.any(String)}));
});
test('selecting a bank row highlights book candidates without saving a pair', async () => {
  const other = {...book, journal_line_id:'book-2', journal_number:'JE-19', description:'Biaya lain', absolute_amount:1000};
  workspace.book_lines=[book, other];
  workspace.visible_book_count=2;
  api.cashBankPairSuggestions.mockResolvedValue({ambiguous:false, applied:false, suggestions:[{journal_line_id:'book-1', journal_number:'JE-18', date:'2026-09-24', amount:750000, direction:'in', description:'Penerimaan pelanggan', reasons:['Nominal sama','Arah sama','Referensi muncul di buku','Beda tanggal 2 hari']}]});
  await renderPage();
  expect(container.querySelector('.kb-candidate-badge')).toBeNull();
  await click(container.querySelector('[name=bank-line]'));
  await flush();
  await flush();
  const candidate = container.querySelector('[data-testid=kas-bank-pair-book-table] tr.kb-row-candidate');
  expect(candidate).toBeTruthy();
  expect(candidate.textContent).toContain('JE-18');
  expect(candidate.textContent).toContain('Kandidat');
  expect(candidate.textContent).not.toContain('Kandidat Cocok');
  expect(candidate.textContent).toContain('Beda tanggal 2 hari');
  expect(candidate.textContent).toContain('Referensi muncul di buku');
  expect(candidate.textContent).not.toContain('Rekening sama');
  expect(candidate.querySelector('.kb-candidate-badge').textContent).toBe('Kandidat');
  expect(candidate.querySelector('.recon-badge.is-matched')).toBeNull();
  const selectedBank = container.querySelector('[data-testid=kas-bank-pair-bank-table] tr.kb-row-selected');
  expect(selectedBank).toBeTruthy();
  expect(selectedBank.querySelector('.kb-selected-badge').textContent).toBe('Terpilih');
  expect(selectedBank.textContent).not.toContain('Kandidat');
  expect(container.querySelector('[data-testid=kas-bank-match-hint]').textContent).toBe('Belum memilih transaksi pembukuan');
  expect(byText('Cocokkan').disabled).toBe(true);
  expect(container.querySelector('[data-testid=kas-bank-pair-link]').textContent).toContain('↔');
  const bookRows = [...container.querySelectorAll('[data-testid=kas-bank-pair-book-table] tbody tr')];
  expect(bookRows[1].classList.contains('kb-row-candidate')).toBe(false);
  expect(bookRows[1].textContent).not.toContain('Kandidat');
  expect(container.querySelector('[name=book-line]').checked).toBe(false);
  expect(api.createCashBankPair).not.toHaveBeenCalled();
  expect(container.querySelector('.recon-badge.is-matched')).toBeNull();
});
test('candidate rows are ordered for display without matching or auto-selecting', async () => {
  const far = {...book, journal_line_id:'book-far', journal_number:'JE-FAR', date:'2026-09-01', description:'Jauh'};
  const mid = {...book, journal_line_id:'book-mid', journal_number:'JE-MID', date:'2026-09-20', description:'Tengah'};
  const weak = {...book, journal_line_id:'book-weak', journal_number:'JE-WEAK', date:'2026-09-25', description:'Dekat lemah'};
  const gap = {...book, journal_line_id:'book-gap', journal_number:'JE-GAP', date:'2026-09-25', description:'Nominal jauh'};
  const exact = {...book, journal_line_id:'book-exact', journal_number:'JE-EXACT', date:'2026-09-25', description:'Paling dekat'};
  const other = {...book, journal_line_id:'book-other', journal_number:'JE-OTHER', description:'Bukan kandidat', absolute_amount:1000};
  workspace.book_lines=[far, other, mid, weak, gap, exact];
  workspace.visible_book_count=6;
  api.cashBankPairSuggestions.mockResolvedValue({ambiguous:true, applied:false, suggestions:[
    {journal_line_id:'book-far', journal_number:'JE-FAR', date:'2026-09-01', amount:750000, direction:'in', reasons:['Nominal sama','Referensi muncul di buku']},
    {journal_line_id:'book-weak', journal_number:'JE-WEAK', date:'2026-09-25', amount:750000, direction:'in', reasons:['Nominal sama','Referensi tidak sama']},
    {journal_line_id:'book-gap', journal_number:'JE-GAP', date:'2026-09-25', amount:100, direction:'in', reasons:['Nominal sama','Referensi muncul di buku']},
    {journal_line_id:'book-mid', journal_number:'JE-MID', date:'2026-09-20', amount:750000, direction:'in', reasons:['Nominal sama','Referensi muncul di buku']},
    {journal_line_id:'book-exact', journal_number:'JE-EXACT', date:'2026-09-25', amount:750000, direction:'in', reasons:['Nominal sama','Referensi muncul di buku']},
  ]});
  await renderPage();
  await click(container.querySelector('[name=bank-line]'));
  await flush();
  await flush();
  const labels = [...container.querySelectorAll('[data-testid=kas-bank-pair-book-table] tbody tr')].map((row) => row.textContent);
  const order = ['JE-EXACT','JE-GAP','JE-WEAK','JE-MID','JE-FAR','JE-OTHER'].map((token) => labels.findIndex((text) => text.includes(token)));
  expect(order).toEqual([0,1,2,3,4,5]);
  expect(container.querySelector('[name=book-line]').checked).toBe(false);
  expect(api.createCashBankPair).not.toHaveBeenCalled();
});
test('off-page suggestions stay visible without clearing the selected bank line', async () => {
  workspace.bank_lines=[{...bank, id:'bank-dup', matchable:false, block_reason:'Dugaan duplikat belum diputuskan'}, bank];
  workspace.book_lines=[book];
  api.cashBankPairSuggestions.mockResolvedValue({ambiguous:true, applied:false, suggestions:[
    {journal_line_id:'book-1', journal_number:'JE-18', date:'2026-09-26', amount:750000, direction:'in', reasons:['Nominal sama','Referensi muncul di buku']},
    {journal_line_id:'book-off', journal_number:'JE-99', date:'2026-09-20', amount:750000, direction:'in', description:'Transfer di luar halaman', reasons:['Nominal sama','Referensi muncul di buku']},
  ]});
  await renderPage();
  const dupRow = container.querySelector('[data-testid=kas-bank-pair-bank-table] tbody tr');
  expect(dupRow.textContent).toContain('Dugaan duplikat belum diputuskan');
  expect(dupRow.classList.contains('kb-row-candidate')).toBe(false);
  await click(dupRow);
  expect(container.querySelector('[data-testid=kas-bank-match-hint]').textContent).toBe('Transaksi menunggu keputusan duplikat');
  expect(byText('Cocokkan').disabled).toBe(true);
  expect(api.createCashBankPair).not.toHaveBeenCalled();
  const bankRadio = container.querySelectorAll('[name=bank-line]')[1];
  await click(bankRadio);
  await flush();
  await flush();
  const offPage = container.querySelector('[data-testid=kas-bank-offpage-candidates]');
  expect(offPage.textContent).toContain('1 kandidat di luar 50 baris ini');
  expect(offPage.textContent).toContain('JE-99');
  expect(offPage.textContent).toContain('Beda tanggal 6 hari');
  expect(offPage.textContent).toContain('Referensi muncul di buku');
  expect(offPage.textContent).not.toContain('halaman lain');
  expect(bankRadio.checked).toBe(true);
  expect(container.querySelector('[data-testid=kas-bank-pair-book-table] tr.kb-row-candidate')).toBeTruthy();
  expect(container.querySelector('[name=book-line]').checked).toBe(false);
  const workspaceCalls = api.cashBankPairWorkspace.mock.calls.length;
  await click(offPage.querySelector('[name=book-line]'));
  expect(api.cashBankPairWorkspace.mock.calls.length).toBe(workspaceCalls);
  expect(bankRadio.checked).toBe(true);
  expect(offPage.querySelector('[name=book-line]').checked).toBe(true);
  expect(api.createCashBankPair).not.toHaveBeenCalled();
  expect(byText('Cocokkan').disabled).toBe(false);
  await click(byText('Cocokkan'));
  expect(api.createCashBankPair).toHaveBeenCalledTimes(1);
  expect(api.createCashBankPair.mock.calls[0][0]).toEqual(expect.objectContaining({statement_line_id:'bank-1', journal_line_id:'book-off'}));
});
test('a rejected match keeps the selection and does not show a confirmed pair', async () => {
  api.createCashBankPair.mockRejectedValue({ response: { status: 409, data: { detail: 'Nominal bank dan buku berbeda' } } });
  await renderPage();
  await click(container.querySelector('[name=bank-line]'));
  await click(container.querySelector('[name=book-line]'));
  const live = container.querySelector('[data-testid=kas-bank-live-pair]');
  const recommendations = container.querySelector('[data-testid=kas-bank-recommendations]');
  expect(live.textContent).toContain('←');
  expect(recommendations.contains(byText('Cocokkan'))).toBe(true);
  expect(container.querySelectorAll('[data-testid=kas-bank-pair-match]')).toHaveLength(1);
  expect(live.textContent).not.toContain('(Dr)');
  expect(live.textContent).not.toContain('(Cr)');
  await click(byText('Cocokkan'));
  expect(container.querySelector('.recon-badge.is-matched')).toBeNull();
  expect(container.querySelector('[data-testid=kas-bank-workspace-pairs]')).toBeNull();
  expect(container.querySelector('#recon-panel-unmatched').textContent).not.toContain('✓ Cocok');
  expect(container.textContent).toContain('Nominal bank dan buku berbeda');
  expect(container.querySelector('[name=bank-line]').checked).toBe(true);
  expect(container.querySelector('[name=book-line]').checked).toBe(true);
  expect(container.querySelector('[name=recommendation-pair]').checked).toBe(true);
  expect(container.querySelector('[data-testid=kas-bank-recommendations]')).toBeTruthy();
  expect(container.querySelector('#recon-tab-unmatched').getAttribute('aria-selected')).toBe('true');
});
test('same-amount recommendations stay unselected until the user chooses one pair', async () => {
  const second = { ...book, journal_line_id: 'book-2', journal_number: 'JE-19', description: 'Penerimaan lain' };
  workspace.book_lines = [book, second];
  workspace.visible_book_count = 2;
  api.cashBankPairSuggestions.mockResolvedValue({
    ambiguous: true,
    applied: true,
    suggestions: [
      { journal_line_id: 'book-1', journal_number: 'JE-18', amount: 750000, direction: 'in', date: '2026-09-26', description: 'Penerimaan pelanggan', reasons: ['Nominal sama', 'Arah sama'] },
      { journal_line_id: 'book-2', journal_number: 'JE-19', amount: 750000, direction: 'in', date: '2026-09-26', description: 'Penerimaan lain', reasons: ['Nominal sama', 'Arah sama'] },
    ],
  });
  await renderPage();
  const block = container.querySelector('[data-testid=kas-bank-recommendations]');
  const rows = [...block.querySelectorAll('[data-testid=kas-bank-recommendation]')];
  expect(rows).toHaveLength(2);
  expect(block.querySelector('input:checked')).toBeNull();
  expect(block.textContent).not.toContain('✓ Cocok');
  expect(container.querySelector('.recon-badge.is-matched')).toBeNull();
  expect(rows[0].querySelectorAll('.kb-matched-side')[0].textContent).toContain('Transfer pelanggan');
  expect(rows[0].querySelectorAll('.kb-matched-side')[1].textContent).toContain('JE-18');
  expect(rows[1].querySelectorAll('.kb-matched-side')[1].textContent).toContain('JE-19');
  expect(rows[0].textContent).toContain('←');
  expect(byText('Cocokkan').disabled).toBe(true);
  expect(api.createCashBankPair).not.toHaveBeenCalled();
  await click(rows[1].querySelector('input'));
  expect(rows[1].querySelector('input').checked).toBe(true);
  expect(rows[0].querySelector('input').checked).toBe(false);
  expect(rows[1].querySelector('[data-testid=kas-bank-pair-match]')).toBe(byText('Cocokkan'));
  expect(rows[0].querySelector('[data-testid=kas-bank-pair-match]')).toBeNull();
  expect(container.querySelectorAll('[data-testid=kas-bank-pair-match]')).toHaveLength(1);
  expect(api.createCashBankPair).not.toHaveBeenCalled();
});
test('a confirmed match leaves unmatched recommendations and appears only on the matched tab', async () => {
  const bankB = { ...bank, id: 'bank-b', description: 'Transfer kedua', reference: 'TRF-19' };
  const bookB = { ...book, journal_line_id: 'book-2', journal_number: 'JE-19', description: 'Penerimaan lain' };
  workspace.bank_lines = [bank, bankB];
  workspace.book_lines = [book, bookB];
  workspace.unmatched_bank_count = 2;
  workspace.unmatched_book_count = 2;
  workspace.visible_bank_count = 2;
  workspace.visible_book_count = 2;
  const suggestionFor = (lineId) => ({
    ambiguous: lineId === 'bank-1',
    applied: false,
    suggestions: lineId === 'bank-1'
      ? [
        { journal_line_id: 'book-1', journal_number: 'JE-18', amount: 750000, direction: 'in', reasons: ['Nominal sama'] },
        { journal_line_id: 'book-2', journal_number: 'JE-19', amount: 750000, direction: 'in', reasons: ['Nominal sama'] },
      ]
      : [{ journal_line_id: 'book-1', journal_number: 'JE-18', amount: 750000, direction: 'in', reasons: ['Nominal sama'] }],
  });
  api.cashBankPairSuggestions.mockImplementation(async ({ statement_line_id }) => suggestionFor(statement_line_id));
  await renderPage();
  expect(container.querySelectorAll('[data-testid=kas-bank-recommendation]')).toHaveLength(3);
  expect(container.querySelector('.recon-badge.is-matched')).toBeNull();
  const chosen = [...container.querySelectorAll('[data-testid=kas-bank-recommendation]')].find((row) => row.textContent.includes('Transfer pelanggan') && row.textContent.includes('JE-18'));
  await click(chosen.querySelector('input'));
  const savedPair = { ...pair, statement_line_id: 'bank-1', journal_line_id: 'book-1' };
  api.cashBankPairWorkspace.mockImplementation(async () => ({
    ...workspace,
    bank_lines: [bankB],
    book_lines: [bookB],
    matched_pairs: [savedPair],
    unmatched_bank_count: 1,
    unmatched_book_count: 1,
    visible_bank_count: 1,
    visible_book_count: 1,
  }));
  api.cashBankPairSuggestions.mockResolvedValue({ ambiguous: false, applied: false, suggestions: [] });
  await click(byText('Cocokkan'));
  await flush();
  await flush();
  const unmatched = container.querySelector('#recon-panel-unmatched');
  expect(container.querySelector('#recon-tab-unmatched').getAttribute('aria-selected')).toBe('true');
  expect(unmatched.textContent).not.toContain('Transfer pelanggan');
  expect(unmatched.textContent).not.toContain('JE-18');
  expect(unmatched.textContent).not.toContain('✓ Cocok');
  expect(unmatched.querySelector('[data-testid=kas-bank-recommendation]')).toBeNull();
  expect(container.querySelector('[name=bank-line]:checked')).toBeNull();
  expect(api.createCashBankPair).toHaveBeenCalledTimes(1);
  await click(byText('Sudah Cocok'));
  const matched = container.querySelector('[data-testid=kas-bank-pair-matched]');
  expect(matched.textContent).toContain('JE-18');
  expect(matched.textContent).toContain('←');
  expect(matched.querySelector('.recon-badge.is-matched').textContent).toBe('✓ Cocok');
});
test('double-clicking Cocokkan submits one pair', async () => {
  let release;
  api.createCashBankPair.mockImplementation(() => new Promise((resolve) => { release = resolve; }));
  await renderPage();
  await click(container.querySelector('[data-testid=kas-bank-recommendation] input'));
  const button = byText('Cocokkan');
  await act(async () => {
    button.click();
    button.click();
  });
  expect(api.createCashBankPair).toHaveBeenCalledTimes(1);
  await act(async () => { release(pair); await Promise.resolve(); });
  await flush();
  expect(api.createCashBankPair).toHaveBeenCalledTimes(1);
});
test('cancelling a stored pair reloads both tabs from server eligibility', async () => {
  workspace.bank_lines = [{ ...bank, claim_id: 'pair-1', matchable: false }];
  workspace.book_lines = [{ ...book, claim_id: 'pair-1', matchable: false }];
  workspace.matched_pairs = [pair];
  await renderPage();
  expect(container.querySelector('[data-testid=kas-bank-recommendations]')).toBeNull();
  await click(byText('Sudah Cocok'));
  expect(container.querySelector('.recon-badge.is-matched').textContent).toBe('✓ Cocok');
  api.cashBankPairWorkspace.mockImplementation(async () => ({
    ...workspace,
    bank_lines: [bank],
    book_lines: [book],
    matched_pairs: [],
    unmatched_bank_count: 1,
    unmatched_book_count: 1,
  }));
  api.cashBankPairSuggestions.mockResolvedValue({ ambiguous: false, applied: false, suggestions: [{ journal_line_id: 'book-1', journal_number: 'JE-18', amount: 750000, direction: 'in', reasons: ['Nominal sama'] }] });
  await click(byText('Batalkan pencocokan'));
  await flush();
  await flush();
  expect(container.querySelector('[data-testid=kas-bank-pair-matched]').textContent).toContain('Belum ada pencocokan');
  expect(container.querySelector('.recon-badge.is-matched')).toBeNull();
  await click(byText('Belum Cocok'));
  expect(container.querySelector('[data-testid=kas-bank-recommendations]').textContent).toContain('JE-18');
  expect(container.querySelector('#recon-panel-unmatched').textContent).not.toContain('✓ Cocok');
  expect(container.querySelector('[data-testid=kas-bank-recommendations] input:checked')).toBeNull();
});
test('lines without a recommendation can still be matched manually', async () => {
  api.cashBankPairSuggestions.mockResolvedValue({ ambiguous: false, applied: false, suggestions: [] });
  await renderPage();
  expect(container.querySelector('[data-testid=kas-bank-recommendations]')).toBeNull();
  await click(container.querySelector('[name=bank-line]'));
  await click(container.querySelector('[name=book-line]'));
  const live = container.querySelector('[data-testid=kas-bank-live-pair]');
  expect(live.querySelector('[data-testid=kas-bank-pair-match]')).toBe(byText('Cocokkan'));
  expect(byText('Cocokkan').disabled).toBe(false);
  await click(byText('Cocokkan'));
  expect(api.createCashBankPair).toHaveBeenCalledTimes(1);
  expect(api.createCashBankPair.mock.calls[0][0]).toEqual(expect.objectContaining({ statement_line_id: 'bank-1', journal_line_id: 'book-1' }));
});
test('saved pairs stay aligned on the matched tab and keep a zero running balance', async () => {
  workspace.bank_lines = [
    { ...bank, claim_id: 'pair-1', matchable: false, block_reason: 'Sudah dicocokkan', running_balance: 0 },
    { ...bank, id: 'bank-open', description: 'Tanpa saldo berjalan', amount: 1000, running_balance: null },
  ];
  workspace.book_lines = [{ ...book, claim_id: 'pair-1', debit: 750000, credit: 0 }];
  workspace.matched_pairs = [{ ...pair, direction: 'in', book_snapshot: { ...pair.book_snapshot, debit: 750000, credit: 0 } }];
  workspace.visible_bank_count = 2;
  await renderPage();
  const unmatched = container.querySelector('#recon-panel-unmatched');
  expect(container.querySelector('[data-testid=kas-bank-workspace-pairs]')).toBeNull();
  expect(unmatched.textContent).not.toContain('✓ Cocok');
  const openTable = container.querySelector('[data-testid=kas-bank-pair-bank-table]');
  expect(openTable.textContent).toContain('Tanpa saldo berjalan');
  expect(openTable.textContent).not.toContain('Transfer pelanggan');
  expect(openTable.textContent).not.toContain('SALDO');
  expect(openTable.textContent).not.toContain('(Dr)');
  expect(openTable.textContent).not.toContain('(Cr)');
  await click(byText('Sudah Cocok'));
  const saved = container.querySelector('[data-testid=kas-bank-pair-matched]');
  expect(saved.textContent).toContain('✓ Cocok');
  expect(saved.textContent).toContain('JE-18');
  expect(saved.textContent).toContain('←');
  expect(saved.textContent).toContain('SALDO');
  expect(saved.textContent).toContain('Rp 0');
  expect(saved.textContent).toContain('(Dr)');
  expect(saved.textContent).not.toContain('(Cr)');
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
test('a pair outside the bank page stays visible without invented bank detail or balance', async () => {
  workspace.bank_lines=[{...bank, id:'bank-open', description:'Masih di halaman'}];
  workspace.book_lines=[];
  workspace.visible_bank_count=1;
  workspace.visible_book_count=0;
  workspace.matched_pairs=[{...pair, statement_line_id:'bank-offpage', direction:'in', amount:250000, book_snapshot:{date:'2026-09-29', journal_number:'JE-OFF', description:'Setoran di snapshot', debit:250000, credit:0}}];
  await renderPage();
  expect(container.querySelector('[data-testid=kas-bank-workspace-pairs]')).toBeNull();
  expect(container.querySelector('#recon-panel-unmatched').textContent).not.toContain('✓ Cocok');
  expect(container.querySelector('#recon-panel-unmatched').textContent).toContain('Masih di halaman');
  await click(byText('Sudah Cocok'));
  const saved=container.querySelector('[data-testid=kas-bank-pair-matched]');
  expect(saved.textContent).toContain('Detail bank tidak tersedia');
  expect(saved.textContent).toContain('bank-offpage');
  expect(saved.textContent).toContain('JE-OFF');
  expect(saved.textContent).toContain('Setoran di snapshot');
  expect(saved.textContent).toContain('✓ Cocok');
  expect(saved.textContent).toContain('(Dr)');
  expect(saved.textContent).toContain('Masuk');
  expect(saved.textContent).not.toContain('SALDO');
  expect(saved.textContent).not.toContain('Masih di halaman');
  expect(saved.textContent).not.toContain('(Cr)');
});
test('changing account or period hides the previous pairs and selection', async () => {
  workspace.cash_bank_id='account-1';
  workspace.search='';
  workspace.matched_pairs=[{...pair, book_snapshot:{...pair.book_snapshot, journal_number:'JE-LAMA', description:'Pasangan lama'}}];
  await renderPage();
  const requested=api.cashBankPairWorkspace.mock.calls[0][0];
  workspace.start_date=requested.start_date;
  workspace.end_date=requested.end_date;
  await click(container.querySelector('[aria-label="Muat ulang"]'));
  await flush();
  expect(container.querySelector('#recon-panel-unmatched').textContent).not.toContain('Pasangan lama');
  expect(container.querySelector('#recon-panel-unmatched').textContent).not.toContain('✓ Cocok');
  await click(byText('Sudah Cocok'));
  expect(container.textContent).toContain('Pasangan lama');
  await click(byText('Belum Cocok'));
  await click(container.querySelector('[name=bank-line]'));
  await click(container.querySelector('[name=book-line]'));
  expect(container.querySelector('[data-testid=kas-bank-recommendations]')).toBeTruthy();
  let resolveNext;
  api.cashBankPairWorkspace.mockImplementation(() => new Promise((resolve) => { resolveNext=resolve; }));
  const nextEnd=requested.end_date==='2026-09-02' ? '2026-09-03' : '2026-09-02';
  await change('[aria-label="Tanggal akhir"]', nextEnd);
  expect(container.textContent).not.toContain('Pasangan lama');
  expect(container.querySelector('[data-testid=kas-bank-recommendations]')).toBeNull();
  expect(container.querySelector('[data-testid=kas-bank-live-pair]')).toBeNull();
  expect(container.querySelector('[name=bank-line]:checked')).toBeNull();
  expect(container.querySelector('[name=recommendation-pair]:checked')).toBeNull();
  expect(byText('Cocokkan').disabled).toBe(true);
  await act(async () => {
    resolveNext({...workspace, cash_bank_id:'account-1', start_date:requested.start_date, end_date:nextEnd, search:'', bank_lines:[], book_lines:[], matched_pairs:[{...pair, book_snapshot:{date:'2026-09-02', journal_number:'JE-BARU', description:'Pasangan baru'}}], visible_bank_count:0, visible_book_count:0});
    await Promise.resolve();
    await Promise.resolve();
  });
  await flush();
  await flush();
  expect(container.querySelector('#recon-panel-unmatched').textContent).not.toContain('Pasangan baru');
  expect(container.querySelector('#recon-panel-unmatched').textContent).not.toContain('Pasangan lama');
  await click(byText('Sudah Cocok'));
  expect(container.textContent).toContain('Pasangan baru');
  expect(container.textContent).not.toContain('Pasangan lama');
});
test('changing the account hides the previous pairs and selection', async () => {
  api.cashBankAccounts.mockResolvedValue({items:[{id:'account-1',name:'Rekening uji',currency_code:'IDR'},{id:'account-2',name:'Rekening lain',currency_code:'IDR'}]});
  workspace.cash_bank_id='account-1';
  workspace.search='';
  workspace.matched_pairs=[{...pair, book_snapshot:{...pair.book_snapshot, journal_number:'JE-LAMA', description:'Pasangan lama'}}];
  await renderPage();
  const requested=api.cashBankPairWorkspace.mock.calls[0][0];
  workspace.start_date=requested.start_date;
  workspace.end_date=requested.end_date;
  await click(container.querySelector('[aria-label="Muat ulang"]'));
  await flush();
  expect(container.querySelector('#recon-panel-unmatched').textContent).not.toContain('Pasangan lama');
  await click(byText('Sudah Cocok'));
  expect(container.textContent).toContain('Pasangan lama');
  await click(byText('Belum Cocok'));
  await click(container.querySelector('[name=bank-line]'));
  await click(container.querySelector('[name=book-line]'));
  expect(container.querySelector('[data-testid=kas-bank-recommendations]')).toBeTruthy();
  let resolveNext;
  api.cashBankPairWorkspace.mockImplementation(() => new Promise((resolve) => { resolveNext=resolve; }));
  await change('[aria-label="Rekening"]', 'account-2');
  expect(container.textContent).not.toContain('Pasangan lama');
  expect(container.querySelector('[data-testid=kas-bank-recommendations]')).toBeNull();
  expect(container.querySelector('[data-testid=kas-bank-live-pair]')).toBeNull();
  expect(container.querySelector('[name=bank-line]:checked')).toBeNull();
  expect(container.querySelector('[name=recommendation-pair]:checked')).toBeNull();
  expect(byText('Cocokkan').disabled).toBe(true);
  await act(async () => {
    resolveNext({...workspace, cash_bank_id:'account-2', start_date:requested.start_date, end_date:requested.end_date, search:'', bank_lines:[{...bank, id:'bank-2', description:'Mutasi rekening lain'}], book_lines:[book], matched_pairs:[{...pair, id:'pair-2', statement_line_id:'bank-2', book_snapshot:{date:'2026-09-02', journal_number:'JE-BARU', description:'Pasangan rekening baru'}}], visible_bank_count:1, visible_book_count:1});
    await Promise.resolve();
    await Promise.resolve();
  });
  await flush();
  await flush();
  expect(container.querySelector('#recon-panel-unmatched').textContent).not.toContain('Pasangan lama');
  expect(container.querySelector('#recon-panel-unmatched').textContent).not.toContain('Pasangan rekening baru');
  expect(container.textContent).toContain('Mutasi rekening lain');
  await click(byText('Sudah Cocok'));
  expect(container.textContent).toContain('Pasangan rekening baru');
  expect(container.textContent).not.toContain('Pasangan lama');
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
  expect(container.querySelector('#recon-tab-duplicates').textContent).toBe('Review Duplikat (2)');
  expect(api.createCashBankPair).not.toHaveBeenCalled();
  await click(container.querySelector('#recon-tab-duplicates'));
  const panel = container.querySelector('[data-testid=kas-bank-duplicate-review]');
  expect(panel.textContent).toContain('PEMINDAHAN DANA RAKHA EDY SYAH');
  expect(panel.textContent).toContain('Rp 70.000.000');
  expect(panel.textContent).toContain('28 Sep 2026');
  expect(panel.textContent).toContain('Line A');
  expect(panel.textContent).toContain('mutasi-2809.csv');
  expect(panel.textContent).toContain('Line B');
  expect(panel.textContent).toContain('mutasi-2809-ulang.csv');
  expect(panel.textContent).toContain('Menunggu Keputusan');
  expect(panel.textContent).toContain('Keluar');
  expect(panel.textContent).toContain('↕');
  expect(panel.textContent).not.toContain('pending_review');
  expect(panel.textContent).toContain('belum dicocokkan');
  expect(container.querySelector('[data-testid=duplicate-keep-line-a]').textContent).toBe('Pertahankan transaksi ini');
  expect(container.querySelector('[data-testid=duplicate-exclude-line-a]').textContent).toBe('Kecualikan sebagai duplikat');
  expect(panel.querySelector('input[type=radio]')).toBeNull();
  expect(panel.textContent).not.toContain('Hapus');
  expect(panel.textContent).not.toContain('Gabungkan');
  expect(panel.textContent).not.toContain('disarankan');
  expect(container.querySelector('[data-testid=duplicate-keep-line-a]').disabled).toBe(true);
  await fillReason('line-a', 'transaksi asli');
  await click(container.querySelector('[data-testid=duplicate-keep-line-a]'));
  expect(api.resolveCashBankStatementDuplicate).toHaveBeenCalledWith('line-a', {action:'keep', reason:'transaksi asli', idempotency_key:expect.any(String)});
  expect(api.createCashBankPair).not.toHaveBeenCalled();
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
  await click(container.querySelector('#recon-tab-duplicates'));
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
  await click(container.querySelector('#recon-tab-duplicates'));
  expect(container.querySelector('#recon-tab-duplicates').textContent).toBe('Review Duplikat (1)');
  expect(container.querySelector('[data-testid=duplicate-line-line-b]').textContent).toContain('Menunggu Keputusan');
  expect(container.querySelector('[data-testid=duplicate-line-line-b]').textContent).not.toContain('pending_review');
  expect(api.createCashBankPair).not.toHaveBeenCalled();
  expect(container.querySelector('[data-testid=duplicate-keep-line-b]')).toBeTruthy();
  expect(container.querySelector('[data-testid=duplicate-resolution-line-a]').textContent).toContain('transaksi asli');
});
test('a failed decision retries with the same idempotency key', async () => {
  api.cashBankDuplicateGroups.mockResolvedValue({groups:[duplicateGroup()]});
  api.resolveCashBankStatementDuplicate.mockRejectedValueOnce(new Error('jaringan putus')).mockResolvedValue({duplicate_status:'kept'});
  await renderPage();
  await click(container.querySelector('#recon-tab-duplicates'));
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
  await click(container.querySelector('#recon-tab-duplicates'));
  await fillReason('line-b', 'salinan impor ulang');
  await click(container.querySelector('[data-testid=duplicate-exclude-line-b]'));
  expect(api.resolveCashBankStatementDuplicate).toHaveBeenCalledWith('line-b', {action:'exclude', reason:'salinan impor ulang', idempotency_key:expect.any(String)});
  expect(api.createCashBankPair).not.toHaveBeenCalled();
  expect(api.cashBankDuplicateGroups.mock.calls.length).toBeGreaterThan(1);
});
test('exclude stays disabled for same file, three candidates, a claim, or an active match', async () => {
  const sameFile = duplicateGroup();
  sameFile.lines[1] = {...sameFile.lines[1], import_id:'imp-a'};
  api.cashBankDuplicateGroups.mockResolvedValueOnce({groups:[sameFile]});
  await renderPage();
  await click(container.querySelector('#recon-tab-duplicates'));
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
  await click(container.querySelector('#recon-tab-duplicates'));
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
  await click(container.querySelector('#recon-tab-duplicates'));
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
  await click(container.querySelector('#recon-tab-duplicates'));
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
  await click(container.querySelector('#recon-tab-duplicates'));
  await fillReason('line-b', 'masih satu berkas');
  expect(container.querySelector('[data-testid=duplicate-exclude-line-b]').disabled).toBe(true);
  expect(container.querySelector('[data-testid=duplicate-keep-line-b]').disabled).toBe(false);
  expect(container.textContent).toContain('Kandidat berasal dari berkas impor yang sama.');

  const third = {statement_line_id:'line-c', import_id:'imp-c', filename:'mutasi-ketiga.csv', imported_at:'2026-09-30T01:00:00Z', date:'2026-09-28', amount:70000000, direction:'out', duplicate_status:'pending_review', active_claim:null, reconciliation_status:'unmatched'};
  const crowded = duplicateGroup({candidate_count:3, lines:[keptLine(openPair.lines[0]), openPair.lines[1], third]});
  api.cashBankDuplicateGroups.mockResolvedValueOnce({groups:[crowded]});
  await remount();
  await click(container.querySelector('#recon-tab-duplicates'));
  await fillReason('line-b', 'masih tiga kandidat');
  expect(container.querySelector('[data-testid=duplicate-exclude-line-b]').disabled).toBe(true);
  expect(container.textContent).toContain('Kandidat lebih dari dua.');

  const claimed = duplicateGroup({lines:[keptLine({...openPair.lines[0], active_claim:'claim-1'}), openPair.lines[1]]});
  api.cashBankDuplicateGroups.mockResolvedValueOnce({groups:[claimed]});
  await remount();
  await click(container.querySelector('#recon-tab-duplicates'));
  await fillReason('line-b', 'saudara masih diklaim');
  expect(container.querySelector('[data-testid=duplicate-exclude-line-b]').disabled).toBe(true);
  expect(container.querySelector('[data-testid=duplicate-keep-line-b]').disabled).toBe(false);

  const matched = duplicateGroup({lines:[keptLine({...openPair.lines[0], reconciliation_status:'active'}), openPair.lines[1]]});
  api.cashBankDuplicateGroups.mockResolvedValueOnce({groups:[matched]});
  await remount();
  await click(container.querySelector('#recon-tab-duplicates'));
  await fillReason('line-b', 'masih ada pasangan');
  expect(container.querySelector('[data-testid=duplicate-exclude-line-b]').disabled).toBe(true);
  expect(container.querySelector('[data-testid=duplicate-keep-line-b]').disabled).toBe(false);
  expect(container.textContent).toContain('Ada pencocokan aktif.');
});
test('zero statement and book balances stay zero while period close stays unavailable', async () => {
  workspace.book_opening_balance = 0;
  workspace.book_ending_balance = 0;
  workspace.difference = 0;
  workspace.statement_closing = {amount:0, as_of:'2026-09-30', label:'Dari kolom saldo impor', status:'dari_kolom_saldo_impor'};
  workspace.period_close_note = 'Penutupan periode rekonsiliasi belum disediakan.';
  workspace.pair_saved_note = 'Pasangan tersimpan bukan rekonsiliasi saldo selesai.';
  workspace.unmatched_bank_count = 0;
  workspace.unmatched_book_count = 0;
  workspace.bank_lines = [];
  workspace.book_lines = [];
  workspace.matched_pairs = [pair];
  await renderPage();
  expect(container.querySelector('.recon-metric-book strong').textContent).toBe('Rp 0');
  expect(container.querySelector('.recon-metric-bank strong').textContent).toBe('Rp 0');
  expect(container.querySelector('.recon-metric-bank').textContent).toContain('Dari kolom saldo impor');
  expect(container.querySelector('.recon-metric-bank').textContent).not.toContain('Belum tersedia');
  expect(container.querySelector('.recon-metric-diff strong').textContent).toBe('Rp 0');
  const note = container.querySelector('[data-testid=kas-bank-period-close]').textContent;
  expect(note).toContain('Penutupan periode rekonsiliasi belum disediakan.');
  expect(note).toContain('Pasangan tersimpan bukan rekonsiliasi saldo selesai.');
  expect(container.textContent).not.toContain('periode selesai direkonsiliasi');
});
test('a solo pending line stays reachable without an automatic decision', async () => {
  api.cashBankPendingReview.mockResolvedValue({
    solo_lines:[{statement_line_id:'solo-1', import_id:'imp-s', filename:'mutasi-tunggal.csv', imported_at:'2026-09-02T00:00:00Z', date:'2026-09-02', amount:1000, direction:'in', description:'Transfer tunggal', duplicate_status:'pending_review', active_claim:null, reconciliation_status:'unmatched', screen_reason:'Baris tunggal. Kelompok duplikat hanya menampilkan dua kandidat atau lebih.'}],
    outside_period:[{statement_line_id:'out-1', import_id:'imp-s', filename:'mutasi-lama.csv', imported_at:'2026-08-02T00:00:00Z', date:'2026-08-15', amount:2000, direction:'out', description:'Mutasi lama', duplicate_status:'pending_review', active_claim:null, reconciliation_status:'unmatched', screen_reason:'Di luar periode yang sedang dibuka.'}],
    unreachable:[{statement_line_id:'seal-1', import_id:'imp-s', filename:'mutasi-tersegel.csv', imported_at:'2026-09-04T00:00:00Z', date:'2026-09-04', amount:3000, direction:'in', description:'Belum tersegel', duplicate_status:'pending_review', active_claim:'claim-1', reconciliation_status:'unmatched', screen_reason:'Baris belum tersegel, sehingga kelompok periode tidak menampilkannya.'}],
    other_accounts:[{cash_bank_id:'other-1', name:'Rekening Lain', pending_count:2}],
    truncated:false,
  });
  await renderPage();
  expect(container.querySelector('#recon-tab-duplicates').textContent).toBe('Review Duplikat (3)');
  expect(container.querySelector('[data-testid=kas-bank-pending-other-accounts]').textContent).toContain('Rekening Lain (2)');
  await click(container.querySelector('#recon-tab-duplicates'));
  const solo = container.querySelector('[data-testid=kas-bank-pending-solo]');
  expect(solo.textContent).toContain('Transfer tunggal');
  expect(solo.textContent).toContain('mutasi-tunggal.csv');
  expect(solo.textContent).toContain('Baris tunggal');
  expect(container.querySelector('[data-testid=kas-bank-pending-outside]').textContent).toContain('Mutasi lama');
  expect(container.querySelector('[data-testid=kas-bank-pending-unreachable]').textContent).toContain('Ada klaim pencocokan aktif.');
  expect(container.querySelector('[data-testid=duplicate-exclude-seal-1]').disabled).toBe(true);
  expect(container.querySelector('[data-testid=duplicate-keep-seal-1]').disabled).toBe(true);
  const reason = container.querySelector('[data-testid=duplicate-reason-solo-1]');
  await act(async () => {
    Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set.call(reason, 'baris asli');
    reason.dispatchEvent(new Event('change', { bubbles:true }));
  });
  expect(container.querySelector('[data-testid=duplicate-exclude-solo-1]').disabled).toBe(false);
  expect(api.resolveCashBankStatementDuplicate).not.toHaveBeenCalled();
  await click(container.querySelector('[data-testid=duplicate-keep-solo-1]'));
  expect(api.resolveCashBankStatementDuplicate).toHaveBeenCalledWith('solo-1', expect.objectContaining({action:'keep', reason:'baris asli'}));
});
test('read-only duplicate review hides keep and exclude', async () => {
  mockPermissions = ['cash_bank.view'];
  api.cashBankDuplicateGroups.mockResolvedValue({groups:[duplicateGroup()]});
  await renderPage();
  await click(container.querySelector('#recon-tab-duplicates'));
  expect(container.querySelector('[data-testid=kas-bank-duplicate-review]').textContent).toContain('Line A');
  expect(byText('Pertahankan transaksi ini')).toBeUndefined();
  expect(byText('Kecualikan sebagai duplikat')).toBeUndefined();
  expect(api.createCashBankPair).not.toHaveBeenCalled();
  expect(api.resolveCashBankStatementDuplicate).not.toHaveBeenCalled();
});
test('validation errors remain in the drawer with row identity', async () => {
  api.validateCashBankStatement.mockResolvedValue({ok:false,errors:[{row:3,message:'Tanggal ambigu'}]}); await renderPage(); await upload(); await click(byText('Validasi'));
  expect(container.querySelector('dialog').textContent).toContain('Baris 3: Tanggal ambigu'); expect(api.commitCashBankStatement).not.toHaveBeenCalled();
});
test('import result lists counts and conflict rows without claiming every transaction was accepted', async () => {
  api.commitCashBankStatement.mockResolvedValue({
    status:'complete', fully_accepted:false, new_count:1, updated_count:2, protected_count:3, unchanged_count:4, conflict_count:1,
    conflicts:[{source_row:4, reason:'id_otoritatif_bertentangan', message:'ID transaksi bank sama, tetapi tanggal, arah, atau nominal berbeda', bank_txn_id:'TX-1'}],
  });
  await renderPage(); await upload(); await click(byText('Konfirmasi impor'));
  const panel = container.querySelector('[data-testid=kas-bank-import-result]');
  expect(panel.getAttribute('role')).toBe('alert');
  expect(panel.textContent).toContain('Impor belum menerima semua transaksi.');
  expect(panel.textContent).toContain('Jurnal tidak dibuat.');
  expect(panel.textContent).toContain('Baru 1');
  expect(panel.textContent).toContain('Diperbarui 2');
  expect(panel.textContent).toContain('Terlindungi 3');
  expect(panel.textContent).toContain('Tidak berubah 4');
  expect(panel.textContent).toContain('Konflik 1');
  expect(panel.textContent).toContain('Baris 4: ID transaksi bank sama, tetapi tanggal, arah, atau nominal berbeda (TX-1)');
  expect(panel.textContent).not.toContain('Impor selesai.');
  expect(container.textContent).toContain('Review Duplikat');
});
test('accepted import result shows the updated count', async () => {
  api.commitCashBankStatement.mockResolvedValue({
    status:'complete', fully_accepted:true, new_count:0, updated_count:1, protected_count:0, unchanged_count:0, conflict_count:0, conflicts:[],
  });
  await renderPage(); await upload(); await click(byText('Konfirmasi impor'));
  const panel = container.querySelector('[data-testid=kas-bank-import-result]');
  expect(panel.getAttribute('role')).toBe('status');
  expect(panel.textContent).toContain('Impor selesai.');
  expect(panel.textContent).toContain('Jurnal tidak dibuat.');
  expect(panel.textContent).toContain('Diperbarui 1');
  expect(panel.textContent).toContain('Konflik 0');
});
test('bca import result uses the same counts when a row conflicts', async () => {
  api.previewCashBankBcaStatement.mockResolvedValue({bank:'BCA',header_detected:true,fingerprint:'fp-bca',account_binding:'bind-bca',rows:[{tanggal:'2026-09-01',keterangan:'Setoran uji',debit:1500000,kredit:null,saldo:10000000,direction:'out',source_row:3}],errors:[],persisted_statement_lines:false,stored:false});
  api.commitCashBankBcaStatement.mockResolvedValue({
    status:'complete', fully_accepted:false, new_count:0, updated_count:0, protected_count:1, unchanged_count:0, conflict_count:1,
    conflicts:[{source_row:6, message:'ID transaksi bank milik baris yang dikecualikan dan tidak diubah', bank_txn_id:'TX-EX'}],
  });
  await renderPage();
  const bcaInput = container.querySelector('[data-testid=kas-bank-bca-file]');
  await act(async () => { Object.defineProperty(bcaInput, 'files', { configurable:true, value:[new File(['tanggal'], 'bca-uji.csv', {type:'text/csv'})] }); bcaInput.dispatchEvent(new Event('change', { bubbles:true })); });
  await click(container.querySelector('[data-testid=kas-bank-bca-import]'));
  const panel = container.querySelector('[data-testid=kas-bank-import-result]');
  expect(panel.getAttribute('role')).toBe('alert');
  expect(panel.textContent).toContain('Impor belum menerima semua transaksi.');
  expect(panel.textContent).toContain('Terlindungi 1');
  expect(panel.textContent).toContain('Baris 6: ID transaksi bank milik baris yang dikecualikan dan tidak diubah (TX-EX)');
  expect(container.querySelector('#recon-tab-duplicates')).toBeTruthy();
});
test('old complete import history shows a dash instead of invented success counts', async () => {
  api.cashBankStatementImports.mockResolvedValue({items:[
    {id:'batch-lama', filename:'lama.csv', status:'complete', stored_count:2, fully_accepted:null, new_count:null, updated_count:null, protected_count:null, unchanged_count:null, conflict_count:null, conflicts:null, result_note:''},
    {id:'batch-baru', filename:'baru.csv', status:'complete', stored_count:0, fully_accepted:false, new_count:0, updated_count:0, protected_count:0, unchanged_count:1, conflict_count:1, conflicts:[], result_note:'Impor belum menerima semua transaksi. Jurnal tidak dibuat.'},
  ]});
  await renderPage();
  await click(byText('Riwayat Impor'));
  const oldCell = container.querySelector('[data-testid=kas-bank-import-outcome-batch-lama]');
  const newCell = container.querySelector('[data-testid=kas-bank-import-outcome-batch-baru]');
  const panel = container.querySelector('[data-testid=kas-bank-pair-import-panel]');
  expect(oldCell.textContent).toBe('—');
  expect(newCell.textContent).toContain('Belum semua diterima');
  expect(newCell.textContent).toContain('Konflik 1');
  expect(panel.textContent).not.toContain('undefined');
  expect(panel.textContent).not.toContain('Semua baris diterima');
  expect(panel.textContent).not.toContain('Impor selesai');
  expect(panel.textContent).toContain('Selesai diimpor');
});
