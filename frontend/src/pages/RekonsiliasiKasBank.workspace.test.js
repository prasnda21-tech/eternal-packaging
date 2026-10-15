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
  cashBankPairGroupCandidates: jest.fn(), createCashBankPairGroup: jest.fn(), cancelCashBankPairGroup: jest.fn(),
  previewCashBankStatement: jest.fn(), previewCashBankBcaStatement: jest.fn(), commitCashBankBcaStatement: jest.fn(), validateCashBankStatement: jest.fn(), commitCashBankStatement: jest.fn(),
  previewCashBankUnmatchedPurge: jest.fn(), executeCashBankUnmatchedPurge: jest.fn(),
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
const pair = { id:'pair-1', match_kind:'pair', status:'active', review_state:'valid', statement_line_id:'bank-1', amount:750000, currency_code:'IDR', book_snapshot:{date:'2026-09-26', journal_number:'JE-18', description:'Penerimaan pelanggan'} };
const group = {
  id:'group-1', match_kind:'pair_group', status:'active', review_state:'valid', amount:1000000, currency_code:'IDR',
  journal_line_id:'book-g', member_count:3, statement_line_ids:['b1','b2','b3'],
  members:[
    { statement_line_id:'b1', amount:200000, direction:'in', date:'2026-09-01', description:'M1' },
    { statement_line_id:'b2', amount:500000, direction:'in', date:'2026-09-02', description:'M2' },
    { statement_line_id:'b3', amount:300000, direction:'in', date:'2026-09-03', description:'M3' },
  ],
  book_snapshot:{ date:'2026-09-03', journal_number:'JE-G', description:'Gabungan' },
};
const flush = () => act(async () => { await Promise.resolve(); });
const click = async (element) => { expect(element).toBeTruthy(); await act(async () => element.click()); };
const byText = (text) => [...container.querySelectorAll('button')].find((el) => el.textContent.replace(/\s+/g, '') === text.replace(/\s+/g, '') || el.textContent.includes(text));
const actionButtons = () => container.querySelector('[data-testid=kas-bank-card-actions]');
async function renderPage() {
  await act(async () => root.render(<RekonsiliasiKasBank />));
  await flush();
  await flush();
  await flush();
}

beforeEach(() => {
  jest.clearAllMocks();
  mockPermissions = ['cash_bank.view','cash_bank.reconcile'];
  container = document.createElement('div'); document.body.appendChild(container); root = createRoot(container);
  workspace = {currency_code:'IDR',feature_ready:true,book_opening_balance:1000000,book_ending_balance:1750000,difference:null,statement_closing:null,unmatched_bank_count:1,unmatched_book_count:1,visible_bank_count:1,visible_book_count:1,bank_total:1,book_total:1,bank_lines:[bank],book_lines:[book],matched_pairs:[]};
  api.cashBankAccounts.mockResolvedValue({items:[{id:'account-1',name:'Rekening uji',currency_code:'IDR'}]});
  api.cashBankPairWorkspace.mockImplementation(async () => workspace);
  api.cashBankStatementImports.mockResolvedValue({items:[]});
  api.cashBankDuplicateGroups.mockResolvedValue({groups:[], pending_total:0, truncated:false, group_total:0, page:1, review_revision:'rev-0'});
  api.cashBankPendingReview.mockResolvedValue({solo_lines:[], outside_period:[], unreachable:[], other_accounts:[], truncated:false, pending_total:0, total:0, page:1, review_revision:'rev-0'});
  api.resolveCashBankStatementDuplicate.mockResolvedValue({duplicate_status:'kept'});
  api.cashBankPairSuggestions.mockResolvedValue({ambiguous:false, applied:false, suggestions:[{journal_line_id:'book-1',journal_number:'JE-18',date:'2026-09-26',amount:750000,direction:'in',description:'Penerimaan pelanggan',reasons:['Nominal sama','Arah sama']}]});
  api.createCashBankPair.mockResolvedValue(pair);
  api.cancelCashBankPair.mockResolvedValue({status:'cancelled'});
  api.cashBankPairGroupCandidates.mockResolvedValue({
    journal_line_id:'book-1', target_amount:1000000, direction:'in', journal_number:'JE-18', description:'Gabungan',
    items:[
      { id:'b1', date:'2026-09-01', description:'M1', amount:200000, direction:'in', currency_code:'IDR' },
      { id:'b2', date:'2026-09-02', description:'M2', amount:500000, direction:'in', currency_code:'IDR' },
      { id:'b3', date:'2026-09-03', description:'M3', amount:300000, direction:'in', currency_code:'IDR' },
      { id:'b4', date:'2026-09-04', description:'M4', amount:100000, direction:'in', currency_code:'IDR' },
    ],
    total:4, page:1, page_size:50,
  });
  api.createCashBankPairGroup.mockResolvedValue(group);
  api.cancelCashBankPairGroup.mockResolvedValue({ ...group, status:'cancelled' });
  api.previewCashBankStatement.mockResolvedValue({fingerprint:'fp-1',account_binding:'bind-1',filename:'mutasi.csv',currency_code:'IDR',account_currency_code:'IDR',sample_rows:[['tanggal','uraian','masuk','keluar','referensi'],['2026-09-26','Transfer',750000,0,'REF-18']],persisted_statement_lines:false,stored:false});
  api.previewCashBankBcaStatement.mockResolvedValue({bank:'BCA',header_detected:true,rows:[{tanggal:'2026-09-01',keterangan:'Setoran uji',debit:1500000,kredit:null,saldo:10000000,direction:'out',source_row:3}],errors:[{row:4,message:'Tanggal tidak valid'}],persisted_statement_lines:false,stored:false});
  api.validateCashBankStatement.mockResolvedValue({ok:true,accepted_count:1,total_in:750000,total_out:0,errors:[]});
  api.commitCashBankStatement.mockResolvedValue({status:'complete'});
  api.commitCashBankBcaStatement.mockResolvedValue({status:'complete'});
});
afterEach(async () => { await act(async () => root.unmount()); container.remove(); });

test('Cocok 1:1 from unique recommendation without radio selection', async () => {
  await renderPage();
  const offered = container.querySelector('[data-testid=kas-bank-recommendation][data-offered=true]');
  expect(offered).toBeTruthy();
  expect(offered.querySelectorAll('.kb-txn-card.is-offered')).toHaveLength(2);
  expect(container.querySelector('[data-testid=kas-bank-pair-link]').textContent).toContain('↔');
  const actions = offered.querySelector('[data-testid=kas-bank-card-actions]');
  const labels = [...actions.querySelectorAll('button')].map((btn) => btn.textContent.trim());
  expect(labels).toEqual(['Cocok', 'Cari', 'Buat']);
  const cocok = actions.querySelector('[data-testid=kas-bank-pair-match]');
  expect(cocok.disabled).toBe(false);
  await click(cocok);
  expect(api.createCashBankPair).toHaveBeenCalledTimes(1);
  expect(api.createCashBankPair.mock.calls[0][0]).toEqual(expect.objectContaining({
    cash_bank_id:'account-1', statement_line_id:'bank-1', journal_line_id:'book-1',
    idempotency_key:expect.any(String), start_date:expect.any(String), end_date:expect.any(String),
  }));
});

test('Cocok disabled when recommendation is ambiguous', async () => {
  api.cashBankPairSuggestions.mockResolvedValue({
    ambiguous:true, applied:false,
    suggestions:[
      {journal_line_id:'book-1',journal_number:'JE-18',date:'2026-09-26',amount:750000,direction:'in',description:'A',reasons:['Nominal sama']},
      {journal_line_id:'book-2',journal_number:'JE-19',date:'2026-09-26',amount:750000,direction:'in',description:'B',reasons:['Nominal sama']},
    ],
  });
  workspace.book_lines = [book, {...book, journal_line_id:'book-2', journal_number:'JE-19', description:'B'}];
  workspace.visible_book_count = 2;
  workspace.book_total = 2;
  await renderPage();
  const row = container.querySelector('[data-ambiguous=true]');
  expect(row).toBeTruthy();
  expect(row.querySelector('.kb-txn-card.is-offered')).toBeNull();
  expect(row.querySelector('[data-testid=kas-bank-pair-match]').disabled).toBe(true);
  expect(container.querySelector('[data-testid=kas-bank-ambiguous-flag]')).toBeTruthy();
});

test('saved matched status differs from offered blue recommendation', async () => {
  workspace.matched_pairs = [pair];
  workspace.bank_lines = [];
  workspace.book_lines = [];
  workspace.unmatched_bank_count = 0;
  workspace.unmatched_book_count = 0;
  api.cashBankPairSuggestions.mockResolvedValue({ambiguous:false, suggestions:[]});
  await renderPage();
  await click([...container.querySelectorAll('[role=tab]')].find((el) => el.textContent.includes('Sudah Cocok')));
  const matched = container.querySelector('[data-testid=kas-bank-pair-matched]');
  expect(matched.textContent).toContain('Tersimpan');
  expect(matched.querySelector('.is-saved, .kb-row-reconciled')).toBeTruthy();
  expect(matched.textContent).toContain('1:1');
});

test('Cari opens dialog, multi-select totals, confirm only when selisih nol', async () => {
  await renderPage();
  await click(container.querySelector('[data-testid=kas-bank-pair-search]'));
  await flush();
  const dialog = container.querySelector('[data-testid=kas-bank-cari-dialog]');
  expect(dialog.getAttribute('open')).not.toBeNull();
  expect(dialog.textContent).toContain('Cari Pencocokan Transaksi');
  expect(container.querySelector('[data-testid=kas-bank-cari-confirm]').disabled).toBe(true);
  await click(container.querySelector('[data-testid=kas-bank-cari-check-b1]'));
  await click(container.querySelector('[data-testid=kas-bank-cari-check-b2]'));
  expect(container.querySelector('[data-testid=kas-bank-cari-confirm]').disabled).toBe(true);
  await click(container.querySelector('[data-testid=kas-bank-cari-check-b3]'));
  expect(container.querySelector('[data-testid=kas-bank-cari-diff]').textContent).toMatch(/0/);
  expect(container.querySelector('[data-testid=kas-bank-cari-confirm]').disabled).toBe(false);
  await click(container.querySelector('[data-testid=kas-bank-cari-check-b4]'));
  expect(container.querySelector('[data-testid=kas-bank-cari-confirm]').disabled).toBe(true);
});

test('Cari confirm calls group endpoint once and double-click does not duplicate', async () => {
  let resolveCreate;
  api.createCashBankPairGroup.mockImplementation(() => new Promise((resolve) => { resolveCreate = resolve; }));
  await renderPage();
  await click(container.querySelector('[data-testid=kas-bank-pair-search]'));
  await flush();
  await click(container.querySelector('[data-testid=kas-bank-cari-check-b1]'));
  await click(container.querySelector('[data-testid=kas-bank-cari-check-b2]'));
  await click(container.querySelector('[data-testid=kas-bank-cari-check-b3]'));
  const confirm = container.querySelector('[data-testid=kas-bank-cari-confirm]');
  await act(async () => { confirm.click(); confirm.click(); });
  expect(api.createCashBankPairGroup).toHaveBeenCalledTimes(1);
  expect(api.createCashBankPairGroup.mock.calls[0][0]).toEqual(expect.objectContaining({
    cash_bank_id:'account-1',
    journal_line_id:'book-1',
    statement_line_ids: expect.arrayContaining(['b1','b2','b3']),
    idempotency_key: expect.any(String),
  }));
  workspace.matched_pairs = [group];
  workspace.bank_lines = [];
  workspace.book_lines = [];
  await act(async () => { resolveCreate(group); await Promise.resolve(); });
  await flush();
  await flush();
  expect(container.querySelector('[data-testid=kas-bank-pair-matched]').textContent).toContain('Grup 1:N');
});

test('after cancel group status updates', async () => {
  workspace.matched_pairs = [group];
  workspace.bank_lines = [];
  workspace.book_lines = [];
  workspace.unmatched_bank_count = 0;
  workspace.unmatched_book_count = 0;
  api.cashBankPairSuggestions.mockResolvedValue({ambiguous:false, suggestions:[]});
  await renderPage();
  await click([...container.querySelectorAll('[role=tab]')].find((el) => el.textContent.includes('Sudah Cocok')));
  await click(byText('Batalkan pencocokan'));
  expect(api.cancelCashBankPairGroup).toHaveBeenCalledWith('group-1', expect.objectContaining({
    idempotency_key: expect.any(String),
    reason: expect.any(String),
  }));
});

test('Buat opens official receipt/payment choice by bank direction', async () => {
  await renderPage();
  await click(container.querySelector('[data-testid=kas-bank-pair-create]'));
  await flush();
  const dialog = container.querySelector('[data-testid=kas-bank-buat-dialog]');
  expect(dialog.getAttribute('open')).not.toBeNull();
  expect(container.querySelector('[data-testid=kas-bank-buat-receipt]').getAttribute('href')).toContain('/kas-bank/penerimaan');
  expect(container.querySelector('[data-testid=kas-bank-buat-payment]')).toBeNull();
});

test('view-only permission hides reconcile actions enablement', async () => {
  mockPermissions = ['cash_bank.view'];
  await renderPage();
  expect(container.querySelector('[data-testid=kas-bank-match-hint]').textContent).toContain('Tidak berizin');
  expect(container.querySelector('[data-testid=kas-bank-pair-match]').disabled).toBe(true);
});

test('mobile pane switch keeps action buttons reachable', async () => {
  await renderPage();
  await click([...container.querySelectorAll('.kb-pane-switch button')].find((el) => el.textContent === 'Jurnal'));
  expect(actionButtons()).toBeTruthy();
  expect(actionButtons().querySelector('[data-testid=kas-bank-pair-create]')).toBeTruthy();
});

test('import and duplicate tabs still render', async () => {
  await renderPage();
  await click([...container.querySelectorAll('[role=tab]')].find((el) => el.textContent.includes('Riwayat Impor')));
  expect(container.querySelector('[data-testid=kas-bank-pair-import-panel]')).toBeTruthy();
  await click([...container.querySelectorAll('[role=tab]')].find((el) => el.textContent.includes('Review Duplikat')));
  expect(container.querySelector('[data-testid=kas-bank-duplicate-review]')).toBeTruthy();
});

test('Cari search keeps input focus while typing continuously', async () => {
  let resolveCandidates;
  api.cashBankPairGroupCandidates.mockImplementation(() => new Promise((resolve) => { resolveCandidates = resolve; }));
  await renderPage();
  await click(container.querySelector('[data-testid=kas-bank-pair-search]'));
  await flush();
  // Resolve initial openCari load
  await act(async () => {
    resolveCandidates({
      journal_line_id:'book-1', target_amount:1000000, direction:'in',
      items:[{ id:'b1', date:'2026-09-01', description:'M1', amount:200000, direction:'in' }],
      total:1, page:1, page_size:50,
    });
    await Promise.resolve();
  });
  await flush();
  const input = container.querySelector('[data-testid=kas-bank-cari-search]');
  expect(input.disabled).toBe(false);
  await act(async () => { input.focus(); });
  let typed = '';
  for (const ch of ['A', 'B', 'C', 'D', 'E', '1', '2', '3']) {
    typed += ch;
    // Keep a pending request mid-typing to prove input stays enabled + focused.
    let resolveNext;
    api.cashBankPairGroupCandidates.mockImplementationOnce(() => new Promise((resolve) => { resolveNext = resolve; }));
    await act(async () => {
      const proto = HTMLInputElement.prototype;
      Object.getOwnPropertyDescriptor(proto, 'value').set.call(input, typed);
      input.dispatchEvent(new Event('input', { bubbles:true }));
      input.dispatchEvent(new Event('change', { bubbles:true }));
    });
    expect(input.disabled).toBe(false);
    expect(document.activeElement).toBe(input);
    expect(input.value).toBe(typed);
    expect(container.querySelector('[data-testid=kas-bank-cari-loading]')).toBeTruthy();
    await act(async () => {
      resolveNext({
        journal_line_id:'book-1', target_amount:1000000, direction:'in',
        items:[{ id:'b1', date:'2026-09-01', description:`hit-${typed}`, amount:200000, direction:'in' }],
        total:1, page:1, page_size:50,
      });
      await Promise.resolve();
    });
    await flush();
    expect(document.activeElement).toBe(input);
    expect(input.value).toBe(typed);
  }
  expect(input.value).toBe('ABCDE123');
  expect(container.querySelector('[data-testid=kas-bank-cari-dialog]').getAttribute('open')).not.toBeNull();
});

test('Cari pagination retains checkbox selection across pages', async () => {
  api.cashBankPairGroupCandidates.mockImplementation(async (params = {}) => {
    const page = Number(params.page || 1);
    if (page === 1) {
      return {
        journal_line_id:'book-1', target_amount:1000000, direction:'in',
        items:[
          { id:'p1', date:'2026-09-01', description:'P1', amount:200000, direction:'in', currency_code:'IDR' },
          { id:'p2', date:'2026-09-02', description:'P2', amount:500000, direction:'in', currency_code:'IDR' },
        ],
        total:4, page:1, page_size:2,
      };
    }
    return {
      journal_line_id:'book-1', target_amount:1000000, direction:'in',
      items:[
        { id:'p3', date:'2026-09-03', description:'P3', amount:300000, direction:'in', currency_code:'IDR' },
        { id:'p4', date:'2026-09-04', description:'P4', amount:100000, direction:'in', currency_code:'IDR' },
      ],
      total:4, page:2, page_size:2,
    };
  });
  await renderPage();
  await click(container.querySelector('[data-testid=kas-bank-pair-search]'));
  await flush();
  await click(container.querySelector('[data-testid=kas-bank-cari-check-p1]'));
  await click(container.querySelector('[data-testid=kas-bank-cari-check-p2]'));
  expect(container.querySelector('[data-testid=kas-bank-cari-selected-count]').textContent).toContain('2');
  await click(container.querySelector('[data-testid=kas-bank-cari-more]'));
  await flush();
  expect(container.querySelector('[data-testid=kas-bank-cari-check-p1]').checked).toBe(true);
  expect(container.querySelector('[data-testid=kas-bank-cari-check-p3]')).toBeTruthy();
  await click(container.querySelector('[data-testid=kas-bank-cari-check-p3]'));
  expect(container.querySelector('[data-testid=kas-bank-cari-diff]').textContent).toMatch(/0/);
  expect(container.querySelector('[data-testid=kas-bank-cari-confirm]').disabled).toBe(false);
  expect(container.querySelector('[data-testid=kas-bank-cari-selected-count]').textContent).toContain('3');
});

test('Sudah Cocok group detail exposes all members', async () => {
  workspace.matched_pairs = [group];
  workspace.bank_lines = [];
  workspace.book_lines = [];
  api.cashBankPairSuggestions.mockResolvedValue({ambiguous:false, suggestions:[]});
  await renderPage();
  await click([...container.querySelectorAll('[role=tab]')].find((el) => el.textContent.includes('Sudah Cocok')));
  const members = container.querySelector('[data-testid=kas-bank-group-members]');
  expect(members).toBeTruthy();
  expect(members.querySelectorAll('li')).toHaveLength(3);
  expect(container.querySelector('[data-testid=kas-bank-group-total]').textContent).toMatch(/1\.000\.000|1000000/);
  expect(container.querySelector('[data-testid=kas-bank-group-expand]')).toBeTruthy();
  await click(container.querySelector('[data-testid=kas-bank-group-expand]'));
  expect(members.className).toContain('is-expanded');
  expect(container.textContent).toContain('Tersimpan');
  expect(byText('Batalkan pencocokan')).toBeTruthy();
});

test('Cari modal closes safely via Batal', async () => {
  await renderPage();
  await click(container.querySelector('[data-testid=kas-bank-pair-search]'));
  await flush();
  expect(container.querySelector('[data-testid=kas-bank-cari-dialog]').getAttribute('open')).not.toBeNull();
  await click(byText('Batal'));
  await flush();
  expect(container.querySelector('[data-testid=kas-bank-cari-dialog]').getAttribute('open')).toBeNull();
});

test('double-click Cocok submits one pair', async () => {
  let resolveCreate;
  api.createCashBankPair.mockImplementation(() => new Promise((resolve) => { resolveCreate = resolve; }));
  await renderPage();
  const cocok = container.querySelector('[data-testid=kas-bank-pair-match]');
  await act(async () => { cocok.click(); cocok.click(); });
  expect(api.createCashBankPair).toHaveBeenCalledTimes(1);
  await act(async () => { resolveCreate(pair); await Promise.resolve(); });
  await flush();
});

test('BCA preview import controls still present', async () => {
  await renderPage();
  expect(container.querySelector('[data-testid=kas-bank-bca-file]')).toBeTruthy();
  expect(container.querySelector('[data-testid=kas-bank-pair-import]')).toBeTruthy();
});

test('in-flight active_claim bank line is hidden from Belum Cocok board', async () => {
  workspace.bank_lines = [{
    ...bank,
    id: 'bank-inflight',
    matchable: false,
    active_claim: 'inflight-key',
    claim_in_flight: true,
    claim_id: null,
    block_reason: 'Baris sedang diklaim pencocokan',
  }];
  workspace.book_lines = [{ ...book, matchable: true }];
  workspace.unmatched_bank_count = 0;
  workspace.bank_total = 1;
  await renderPage();
  expect(container.textContent).not.toContain('Transfer pelanggan');
  expect(container.querySelector('[data-testid=kas-bank-recommendation]')).toBeNull();
});

test('Hapus Belum Cocok hidden without purge permission', async () => {
  mockPermissions = ['cash_bank.view', 'cash_bank.reconcile'];
  await renderPage();
  expect(container.querySelector('[data-testid=kas-bank-purge-unmatched]')).toBeNull();
});

test('Hapus Belum Cocok visible when unmatched table is empty', async () => {
  mockPermissions = ['cash_bank.view', 'cash_bank.reconcile', 'cash_bank.reconcile.purge_unmatched'];
  workspace = {
    ...workspace,
    unmatched_bank_count: 0,
    unmatched_book_count: 0,
    visible_bank_count: 0,
    visible_book_count: 0,
    bank_total: 0,
    book_total: 0,
    bank_lines: [],
    book_lines: [],
  };
  api.previewCashBankUnmatchedPurge.mockResolvedValue({
    cash_bank_id: 'account-1',
    cash_bank_name: 'Rekening uji',
    eligible_count: 0,
    protected_count: 0,
    ambiguous_count: 0,
    fence_busy: false,
    zero_write: true,
  });
  await renderPage();
  const select = container.querySelector('[data-testid=kas-bank-pair-bank]');
  await act(async () => {
    select.value = 'account-1';
    select.dispatchEvent(new Event('change', { bubbles: true }));
  });
  await flush();
  await flush();
  const btn = container.querySelector('[data-testid=kas-bank-purge-unmatched]');
  expect(btn).toBeTruthy();
  expect(container.querySelector('[data-testid=kas-bank-unmatched-empty]')).toBeTruthy();
  expect(container.querySelector('[data-testid=kas-bank-pair-match]')).toBeNull();
  await click(btn);
  await flush();
  expect(api.previewCashBankUnmatchedPurge).toHaveBeenCalled();
  expect(container.querySelector('[data-testid=kas-bank-purge-confirm]').disabled).toBe(true);
});

test('Hapus Belum Cocok dialog shows counts and confirm disabled when zero', async () => {
  mockPermissions = ['cash_bank.view', 'cash_bank.reconcile', 'cash_bank.reconcile.purge_unmatched'];
  api.previewCashBankUnmatchedPurge.mockResolvedValue({
    cash_bank_id: 'account-1',
    cash_bank_name: 'Rekening uji',
    start_date: '2026-09-01',
    end_date: '2026-09-30',
    eligible_count: 0,
    protected_count: 2,
    ambiguous_count: 1,
    fence_busy: false,
    zero_write: true,
  });
  await renderPage();
  const select = container.querySelector('[data-testid=kas-bank-pair-bank]');
  await act(async () => {
    select.value = 'account-1';
    select.dispatchEvent(new Event('change', { bubbles: true }));
  });
  await flush();
  await flush();
  const btn = container.querySelector('[data-testid=kas-bank-purge-unmatched]');
  expect(btn).toBeTruthy();
  await click(btn);
  await flush();
  expect(api.previewCashBankUnmatchedPurge).toHaveBeenCalled();
  expect(container.querySelector('[data-testid=kas-bank-purge-dialog]')).toBeTruthy();
  expect(container.querySelector('[data-testid=kas-bank-purge-counts]').textContent).toContain('Eligible dihapus');
  expect(container.querySelector('[data-testid=kas-bank-purge-confirm]').disabled).toBe(true);
});

test('Hapus Belum Cocok execute calls API and refreshes workspace', async () => {
  mockPermissions = ['cash_bank.view', 'cash_bank.reconcile', 'cash_bank.reconcile.purge_unmatched'];
  api.previewCashBankUnmatchedPurge.mockResolvedValue({
    cash_bank_id: 'account-1',
    cash_bank_name: 'Rekening uji',
    eligible_count: 2,
    protected_count: 1,
    ambiguous_count: 0,
    fence_busy: false,
  });
  api.executeCashBankUnmatchedPurge.mockResolvedValue({
    status: 'completed', deleted_count: 2, message: '2 mutasi belum cocok dihapus.',
  });
  await renderPage();
  const select = container.querySelector('[data-testid=kas-bank-pair-bank]');
  await act(async () => {
    select.value = 'account-1';
    select.dispatchEvent(new Event('change', { bubbles: true }));
  });
  await flush();
  await flush();
  await click(container.querySelector('[data-testid=kas-bank-purge-unmatched]'));
  await flush();
  const confirm = container.querySelector('[data-testid=kas-bank-purge-confirm]');
  expect(confirm.disabled).toBe(false);
  const before = api.cashBankPairWorkspace.mock.calls.length;
  await click(confirm);
  await flush();
  await flush();
  expect(api.executeCashBankUnmatchedPurge).toHaveBeenCalled();
  expect(api.cashBankPairWorkspace.mock.calls.length).toBeGreaterThan(before);
});
