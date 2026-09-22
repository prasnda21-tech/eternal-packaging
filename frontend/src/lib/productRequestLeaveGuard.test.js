import { armProductRequestBeforeUnload } from "./productRequestLeaveGuard";

describe("product request beforeunload", () => {
  test("arms the browser event without a custom message", () => {
    const event = {
      defaultPrevented: false,
      returnValue: undefined,
      preventDefault() { this.defaultPrevented = true; },
    };
    armProductRequestBeforeUnload(event);
    expect(event.defaultPrevented).toBe(true);
    expect(event.returnValue).toBe("");
  });
});
