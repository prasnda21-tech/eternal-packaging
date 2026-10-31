import { act } from 'react';
import { createRoot } from 'react-dom/client';
import RoleWorkPanel from './RoleWorkPanel';
import api from '../lib/api';

const mockHasPerm = jest.fn(() => true);
jest.mock('../lib/auth', () => ({useAuth: () => ({user: {id:'u1', email:'operator@example.test', name:'Operator Uji'}, hasPerm: p => mockHasPerm(p)})}));
jest.mock('../lib/api', () => ({__esModule:true, default:{updateScreenerInfo:jest.fn(),updateOperatorProgress:jest.fn()}}));
jest.mock('react-router-dom', () => ({Link:({to,children,...p})=><a href={to} {...p}>{children}</a>}));
jest.mock('./SuratJalanPanel', () => () => null);
jest.mock('./LoadingPanel', () => () => null);
jest.mock('./DeliveryProofPanel', () => () => null);
jest.mock('./SuratJalanViewButton', () => () => null);
jest.mock('./AttachmentsPanel', () => () => null);
jest.mock('./OperatorSplitPanel', () => () => null);

global.IS_REACT_ACT_ENVIRONMENT = true;
const order={id:'po1',po_number:'PO-TEST',print_qty_total:1000,line_items:[{name:'Cup',quantity:1000}],workflow_ready:{screener:true},screener_info:{entries:[{qty:100,at:'2026-10-31T08:00:00Z',by_name:'Budi'}]}};
const division={id:'screener',label:'Screener',submit_perm:'screen.update'};
let root,host;
beforeEach(()=>{host=document.createElement('div');document.body.appendChild(host);root=createRoot(host);mockHasPerm.mockImplementation(()=>true);jest.clearAllMocks();});
afterEach(()=>{act(()=>root.unmount());host.remove();});
const render=async(props={})=>{await act(async()=>root.render(<RoleWorkPanel order={order} division={division} {...props}/>));};
const input=()=>host.querySelector('[data-testid="work-entry-po-input"]');
const change=async(value)=>{await act(async()=>{Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value').set.call(input(),value);input().dispatchEvent(new Event('input',{bubbles:true}));});};
test('desktop input precedes collapsed history; expanding history preserves draft',async()=>{
 await render({compactLayout:true});
 const form=host.querySelector('[data-testid="work-entry-po"]');
 const history=host.querySelector('details');
 expect(form.compareDocumentPosition(history)&Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
 expect(history.open).toBe(false);
 await change('25');
 history.open=true;history.open=false;
 expect(input().value).toBe('25');
 expect(host.querySelector('summary').textContent).toContain('1');
});
test('default shared panel retains visible history before form',async()=>{
 await render();
 expect(host.querySelector('details')).toBeNull();
 const history=host.querySelector('[data-testid="work-history-po"]');
 const form=host.querySelector('[data-testid="work-entry-po"]');
 expect(history.compareDocumentPosition(form)&Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
});
test('compact form keeps confirmation step and submit payload',async()=>{
 const updated=jest.fn();api.updateScreenerInfo.mockResolvedValue({order});
 await render({compactLayout:true,onUpdated:updated});await change('25');
 await act(async()=>host.querySelector('[data-testid="work-entry-po-confirm"]').click());
 expect(api.updateScreenerInfo).not.toHaveBeenCalled();
 expect(host.querySelector('[data-testid="confirm-qty"]').textContent).toBe('25');
 await act(async()=>host.querySelector('[data-testid="confirm-ok"]').click());
 expect(api.updateScreenerInfo).toHaveBeenCalledWith('po1',expect.objectContaining({qty:25,action_id:expect.any(String)}));
 expect(updated).toHaveBeenCalledWith(order);expect(input().value).toBe('');
});
test('compact layout does not create an input without submit permission',async()=>{
 mockHasPerm.mockImplementation(p=>p!=='screen.update');await render({compactLayout:true});
 expect(input()).toBeNull();expect(host.querySelector('details')).not.toBeNull();
});
test('personal operator keeps assignment and required work date through confirmation',async()=>{
 mockHasPerm.mockImplementation(p=>p!=='schedule.edit');
 const opOrder={...order,workflow_ready:{print_qty:true},operator_splits:[{id:'s1',line_idx:0,operator_user_id:'u1',operator_email:'operator@example.test',quantity:1000,qty_completed:0,progress_entries:[]}]};
 api.updateOperatorProgress.mockResolvedValue({order:opOrder});
 await render({compactLayout:true,order:opOrder,division:{id:'operator',submit_perm:'operator.update'}});
 const qty=host.querySelector('[data-testid^="work-entry-"][data-testid$="-input"]');
 const date=host.querySelector('input[type="date"]');
 const button=host.querySelector('[data-testid^="work-entry-"][data-testid$="-confirm"]');
 await act(async()=>{Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value').set.call(qty,'50');qty.dispatchEvent(new Event('input',{bubbles:true}));});
 await act(async()=>button.click());
 expect(host.querySelector('[data-testid="confirm-action-modal"]')).toBeNull();
 expect(api.updateOperatorProgress).not.toHaveBeenCalled();
 await act(async()=>{Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value').set.call(date,'2026-10-31');date.dispatchEvent(new Event('input',{bubbles:true}));});
 await act(async()=>button.click());
 await act(async()=>host.querySelector('[data-testid="confirm-ok"]').click());
 expect(api.updateOperatorProgress).toHaveBeenCalledWith('po1','s1',50,expect.objectContaining({qty:50,work_date:'2026-10-31',action_id:expect.any(String)}));
});
