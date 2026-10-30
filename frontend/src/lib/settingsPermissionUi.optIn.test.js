import { bulkPermissionKeys, addedOptInPermissions } from "./settingsPermissionUi";

test("select all excludes opt-in while retaining ordinary view grants", () => {
  expect(bulkPermissionKeys([{key:"po.view"}, {key:"paint.view",opt_in:true}])).toEqual(["po.view"]);
});
test("confirmation names only newly added special permissions", () => {
  const catalog={modules:{PO:[{key:"po.view",label:"PO"}],Cat:[{key:"paint.view",label:"Cat",opt_in:true}]}};
  expect(addedOptInPermissions(catalog,new Set(["po.view","paint.view"]),new Set(["po.view"])).map(p=>p.key)).toEqual(["paint.view"]);
  expect(addedOptInPermissions(catalog,new Set(["paint.view"]),new Set(["paint.view"]))).toEqual([]);
});
