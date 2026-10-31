import {act} from 'react';
import {createRoot} from 'react-dom/client';
import DivisionTaskPage from './DivisionTaskPage';
import api from '../lib/api';
const mockLocation={pathname:'/tasks/operator',search:''};
const mockParams=new URLSearchParams();
jest.mock('react-router-dom',()=>({Link:({to,children,state,...p})=><a href={to} {...p}>{children}</a>,Navigate:()=>null,useLocation:()=>mockLocation,useNavigate:()=>jest.fn(),useSearchParams:()=>[mockParams,jest.fn()]}));
jest.mock('../lib/auth',()=>({useAuth:()=>({user:{id:'u1'},hasPerm:()=>true})}));
jest.mock('../lib/api',()=>({__esModule:true,default:{divisionTasks:jest.fn()}}));
global.IS_REACT_ACT_ENVIRONMENT=true;
let root,host;
const task={task_id:'t1',order_id:'po1',order_number:'PO-1',division:'operator',customer_name:'Kopi',item:'Cup',target_qty:2000,completed_qty:600,remaining_qty:1400,qty_order_current:2000,qty_produced:600,qty_production_remaining:1400,qty_scheduled:1200,qty_unscheduled:800,current_stock:null,required_input:'Isi qty',status:'belum',status_label:'Belum',eligible:true};
beforeEach(()=>{host=document.createElement('div');document.body.appendChild(host);root=createRoot(host);api.divisionTasks.mockResolvedValue({tasks:[task],can_view_all:true,can_view_po:true});});
afterEach(()=>{act(()=>root.unmount());host.remove();});
const render=async(slug)=>{mockLocation.pathname='/tasks/'+slug;await act(async()=>root.render(<DivisionTaskPage/>));};
test.each(['designer','kepala-produksi','screener','helper','operator','packing'])('%s preserves work action and row in compact layout',async(slug)=>{
 await render(slug);expect(host.querySelector('.production-compact')).not.toBeNull();
 expect(host.querySelectorAll('tbody tr')).toHaveLength(1);
 expect(host.textContent).toContain('PO-1');expect(host.querySelector('tbody a[href*="/po/po1"]')).not.toBeNull();
});
test('group headers preserve 12 columns and distinguish unknown stock from zero',async()=>{
 await render('kepala-produksi');
 const spans=[...host.querySelectorAll('.production-quantity-groups th')].reduce((n,c)=>n+c.colSpan,0);
 expect(spans).toBe(12);expect(host.querySelectorAll('tbody tr td')).toHaveLength(12);
 expect([...host.querySelectorAll('tbody .num')].map(n=>n.textContent)).toEqual(['2.000','600','1.400','1.200','800','—']);
});
test('shipping page does not opt into production styling',async()=>{
 await render('surat-jalan');expect(host.querySelector('.production-compact')).toBeNull();
});
