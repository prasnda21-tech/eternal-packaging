const React = require("react");

let params = new URLSearchParams();
const listeners = new Set();

function reset(search = "") {
  const raw = String(search || "");
  params = new URLSearchParams(raw.startsWith("?") ? raw.slice(1) : raw);
  listeners.forEach((notify) => notify());
}

function useSearchParams() {
  const [, bump] = React.useState(0);
  React.useEffect(() => {
    const notify = () => bump((value) => value + 1);
    listeners.add(notify);
    return () => listeners.delete(notify);
  }, []);
  const setSearchParams = (next) => {
    params = next instanceof URLSearchParams ? new URLSearchParams(next) : new URLSearchParams(next);
    listeners.forEach((notify) => notify());
  };
  return [params, setSearchParams];
}

function Link({ children, to, ...rest }) {
  return <a href={typeof to === "string" ? to : ""} {...rest}>{children}</a>;
}

function MemoryRouter({ children }) {
  return children;
}

module.exports = {
  __esModule: true,
  __reset: reset,
  useSearchParams,
  Link,
  MemoryRouter,
};
