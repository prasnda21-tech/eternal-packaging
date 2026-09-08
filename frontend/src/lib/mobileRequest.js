export const MOBILE_LOAD_TIMEOUT_MS = 45000;

export function withTimeout(promise, ms = MOBILE_LOAD_TIMEOUT_MS) {
  let timer;
  const timeout = new Promise((_, reject) => {
    timer = setTimeout(() => {
      const err = new Error("timeout");
      err.code = "ETIMEDOUT";
      reject(err);
    }, ms);
  });
  return Promise.race([promise, timeout]).finally(() => clearTimeout(timer));
}
