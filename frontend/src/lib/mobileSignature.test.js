import { applyLocalSignature, INITIAL_SIGNATURE_MODAL, signatureModalReducer, signaturePreviewState } from "./mobileSignature";

describe("signature modal state", () => {
  test("open / clear / cancel / save", () => {
    let state = INITIAL_SIGNATURE_MODAL;
    state = signatureModalReducer(state, { type: "open", role: "recipient" });
    expect(state).toEqual({ open: true, role: "recipient", draft: null });
    state = signatureModalReducer(state, { type: "stroke", dataUrl: "data:image/png;base64,aa" });
    expect(state.draft).toMatch(/^data:image/);
    state = signatureModalReducer(state, { type: "clear" });
    expect(state.draft).toBe(null);
    expect(state.open).toBe(true);
    state = signatureModalReducer(state, { type: "cancel" });
    expect(state).toEqual(INITIAL_SIGNATURE_MODAL);
    state = signatureModalReducer(state, { type: "open", role: "driver" });
    state = signatureModalReducer(state, { type: "stroke", dataUrl: "data:image/png;base64,bb" });
    const saved = applyLocalSignature({}, state.role, state.draft);
    state = signatureModalReducer(state, { type: "save" });
    expect(state.open).toBe(false);
    expect(saved.driver).toMatch(/^data:image/);
  });

  test("read-only when delivered", () => {
    const frozen = signatureModalReducer(INITIAL_SIGNATURE_MODAL, { type: "open", role: "recipient", readOnly: true });
    expect(frozen.open).toBe(false);
    const preview = signaturePreviewState(null, { id: "s1", signature_storage_path: "sig.png" }, true);
    expect(preview.saved).toBe(true);
    expect(preview.readOnly).toBe(true);
  });
});
