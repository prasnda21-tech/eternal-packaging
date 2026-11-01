/** One tracker per authenticated document. No event contents are collected. */
export function startWebPresence(send, { win = window, clock = () => performance.now() } = {}) {
  let stopped = false;
  let inFlight = false;
  let lastInteraction = null;
  let sentInteraction = null;
  const interact = (event) => {
    if (event.isTrusted) lastInteraction = clock();
  };
  const beat = async () => {
    if (stopped || inFlight) return;
    inFlight = true;
    const interaction = lastInteraction;
    const age = interaction == null ? null : Math.max(0, Math.floor(clock() - interaction));
    const payload = interaction !== sentInteraction && age != null && age <= 90000
      ? { activity_age_ms: age } : {};
    try {
      await send(payload);
      if (payload.activity_age_ms != null) sentInteraction = interaction;
    } catch { /* Presence must never interrupt business actions or sign the user out. */ }
    finally { inFlight = false; }
  };
  const events = ["pointerdown", "keydown", "scroll", "touchstart"];
  events.forEach(event => win.addEventListener(event, interact, { passive: true, capture: true }));
  win.addEventListener("online", beat);
  win.addEventListener("pageshow", beat);
  const timer = win.setInterval(beat, 30000);
  beat();
  return () => {
    stopped = true;
    win.clearInterval(timer);
    events.forEach(event => win.removeEventListener(event, interact, true));
    win.removeEventListener("online", beat);
    win.removeEventListener("pageshow", beat);
  };
}
