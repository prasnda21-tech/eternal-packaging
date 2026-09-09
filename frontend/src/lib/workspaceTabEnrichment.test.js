import { enrichWorkspaceTab, registerWorkspaceTabEnricher } from "./workspaceTabEnrichment";

describe("workspaceTabEnrichment", () => {
  beforeEach(() => {
    const drain = registerWorkspaceTabEnricher(() => {});
    drain();
  });

  test("registered handler receives pathname + meta; unregister stops delivery", () => {
    const calls = [];
    const stop = registerWorkspaceTabEnricher((pathname, meta) => {
      calls.push({ pathname, meta });
    });
    enrichWorkspaceTab("/po/ord-a", { customer: "WELCOME TO PLEASURE" });
    expect(calls).toEqual([{ pathname: "/po/ord-a", meta: { customer: "WELCOME TO PLEASURE" } }]);
    stop();
    enrichWorkspaceTab("/po/ord-b", { customer: "PT ABC" });
    expect(calls).toHaveLength(1);
  });

  test("PODetail enrich before hook register is queued and flushed (child effects run first)", () => {
    const calls = [];
    enrichWorkspaceTab("/po/b8aecfed-3a03-401d-9197-08d75cef1bfd", {
      po_number: "PO/09/09/26/001",
      customer: "GIMBO 2.0",
    });
    expect(calls).toHaveLength(0);
    const stop = registerWorkspaceTabEnricher((pathname, meta) => {
      calls.push({ pathname, meta });
    });
    expect(calls).toEqual([{
      pathname: "/po/b8aecfed-3a03-401d-9197-08d75cef1bfd",
      meta: { po_number: "PO/09/09/26/001", customer: "GIMBO 2.0" },
    }]);
    stop();
  });
});
