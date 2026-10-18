/** @jest-environment jsdom */
import React from "react";
import { act } from "react-dom/test-utils";
import { createRoot } from "react-dom/client";

const mockPermissions = new Set(["reports.sales.view"]);
jest.mock("react-router-dom", () => ({
  Link: ({to,children}) => <a href={to}>{children}</a>,
}), {virtual:true});
jest.mock("../lib/auth", () => ({
  useAuth: () => ({user:{id:"audit-user",email:"audit@example.test"},hasPerm:(key)=>mockPermissions.has(key)}),
}));
jest.mock("../lib/api", () => {
  const api={
    salesReportCatalog:jest.fn(),salesReportShow:jest.fn(),
    salesReportExport:jest.fn(),salesReportMemorize:jest.fn(),
    salesReportForget:jest.fn()
  };
  return {__esModule:true,api,default:api};
});
import {api} from "../lib/api";
import LaporanPenjualan from "./LaporanPenjualan";

const meta={
  branches:[{id:"semua",label:"Semua Cabang"}],
  memorized:[],
  reports:[{id:"rincian-penjualan-per-barang",name:"Rincian Penjualan per Barang",implemented:true,columns:[
    {key:"nomor",label:"Nomor",default:true,align:"left"},
    {key:"penjualan",label:"Penjualan",default:true,align:"right"}
  ]}]
};
const report={
  name:"Rincian Penjualan per Barang",company_name:"Eternal Packaging",
  date_from:"2026-10-01",date_to:"2026-10-19",period_label:"Oktober 2026",
  branch_label:"Semua Cabang",page:1,page_count:1,
  columns:[{key:"nomor",label:"Nomor",align:"left"},{key:"penjualan",label:"Penjualan",align:"right"}],
  rows:[
    {kind:"group",label:"Cup PET",level:0},
    {kind:"line",values:{nomor:"SI-001",penjualan:"1200"}},
    {kind:"subtotal",values:{nomor:"Cup PET",penjualan:"1200"}}
  ],
  totals:{penjualan:"1200"},gap:null
};

describe("Laporan Penjualan integration UI",()=>{
  let node,root;
  const flush=async()=>{await act(async()=>{await Promise.resolve();await Promise.resolve();});};
  beforeEach(()=>{
    globalThis.IS_REACT_ACT_ENVIRONMENT=true;
    window.localStorage.clear();mockPermissions.clear();mockPermissions.add("reports.sales.view");
    Object.values(api).forEach(fn=>fn.mockReset());
    api.salesReportCatalog.mockResolvedValue(meta);
    api.salesReportShow.mockResolvedValue(report);
    node=document.createElement("div");document.body.appendChild(node);root=createRoot(node);
  });
  afterEach(()=>{act(()=>root.unmount());node.remove();});
  async function start(){
    await act(async()=>{root.render(<LaporanPenjualan/>);});
    await flush();
  }
  async function run(){
    const runButton=[...node.querySelectorAll("button")].find(b=>b.textContent==="Tampilkan");
    expect(runButton).toBeTruthy();
    await act(async()=>{runButton.click();await Promise.resolve();});
    await flush();
  }
  test("catalog loads, report uses current API, and export requires separate permission",async()=>{
    await start();await run();
    expect(api.salesReportCatalog).toHaveBeenCalledTimes(1);
    expect(api.salesReportShow).toHaveBeenCalledWith(expect.objectContaining({
      report_id:"rincian-penjualan-per-barang",branch:"semua",page:1
    }));
    expect(node.textContent).toContain("SI-001");
    const exportButton=[...node.querySelectorAll("button")].find(b=>b.textContent==="Ekspor Excel");
    expect(exportButton.disabled).toBe(true);
    expect(api.salesReportExport).not.toHaveBeenCalled();
  });
  test("server-provided group collapses details while totals remain visible",async()=>{
    await start();await run();
    expect(node.textContent).toContain("SI-001");
    const close=node.querySelector('button[aria-label="Tutup Cup PET"]');
    expect(close).toBeTruthy();
    act(()=>close.click());
    expect(node.textContent).not.toContain("SI-001");
    expect(node.textContent).toContain("1.200"); // server total retained
    const open=node.querySelector('button[aria-label="Buka Cup PET"]');
    act(()=>open.click());
    expect(node.textContent).toContain("SI-001");
  });
  test("column choice persists under user/report key without leaking backend writes",async()=>{
    await start();await run();
    const settings=[...node.querySelectorAll("button")].find(b=>b.textContent==="Atur Tampilan");
    act(()=>settings.click());
    const checkbox=node.querySelector('.erw-col-row input[type="checkbox"]');
    expect(checkbox).toBeTruthy();
    act(()=>checkbox.click());
    await flush();
    const key="eternal-report-layout-v1:audit-user:rincian-penjualan-per-barang";
    const stored=JSON.parse(window.localStorage.getItem(key));
    expect(stored.hidden).toContain("nomor");
    expect(api.salesReportMemorize).not.toHaveBeenCalled();
  });
});
