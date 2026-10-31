import { startWebPresence } from "./webPresence";

function browser() {
  const handlers = {};
  return {
    handlers,
    addEventListener: jest.fn((name, fn) => { handlers[name] = fn; }),
    removeEventListener: jest.fn((name) => { delete handlers[name]; }),
    setInterval, clearInterval,
  };
}
beforeEach(() => jest.useFakeTimers());
afterEach(() => jest.useRealTimers());
const flush = async () => { await Promise.resolve(); await Promise.resolve(); };

test("opening web sends heartbeat; idle does not manufacture interaction; cleanup stops", async () => {
  const win = browser();
  const send = jest.fn().mockResolvedValue({});
  const stop = startWebPresence(send, { win, clock: () => 0 });
  await flush();
  expect(send).toHaveBeenCalledWith({});
  jest.advanceTimersByTime(30000); await flush();
  expect(send).toHaveBeenLastCalledWith({});
  stop(); jest.advanceTimersByTime(90000);
  expect(send).toHaveBeenCalledTimes(2);
  expect(Object.keys(win.handlers)).toHaveLength(0);
});

test("trusted input is sent once with elapsed time; synthetic input ignored", async () => {
  let now = 100;
  const win = browser(); const send = jest.fn().mockResolvedValue({});
  const stop = startWebPresence(send, { win, clock: () => now }); await flush();
  win.handlers.keydown({ isTrusted: false });
  jest.advanceTimersByTime(30000); await flush();
  expect(send).toHaveBeenLastCalledWith({});
  win.handlers.pointerdown({ isTrusted: true }); now = 400;
  jest.advanceTimersByTime(30000); await flush();
  expect(send).toHaveBeenLastCalledWith({ activity_age_ms: 300 });
  jest.advanceTimersByTime(30000); await flush();
  expect(send).toHaveBeenLastCalledWith({}); stop();
});

test("failed requests retry and in-flight heartbeats never overlap", async () => {
  const win = browser(); let reject;
  const send = jest.fn().mockImplementationOnce(() => new Promise((_, no) => { reject = no; })).mockResolvedValue({});
  const stop = startWebPresence(send, { win });
  jest.advanceTimersByTime(60000); expect(send).toHaveBeenCalledTimes(1);
  reject(new Error("offline")); await flush();
  win.handlers.online(); await flush(); expect(send).toHaveBeenCalledTimes(2); stop();
});
