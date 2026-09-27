const React = require("react");

let currentPath = "/dashboard/penjualan";
const listeners = new Set();

function notify() {
  listeners.forEach((listener) => listener());
}

function reset(path = "/dashboard/penjualan") {
  currentPath = path;
  notify();
}

function path() {
  return currentPath;
}

function subscribe(listener) {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

function Link({ to, children, ...props }) {
  return (
    <a
      href={typeof to === "string" ? to : "#"}
      {...props}
      onClick={(event) => {
        event.preventDefault();
        if (typeof to === "string") currentPath = to;
        notify();
      }}
    >
      {children}
    </a>
  );
}

function Navigate({ to }) {
  return <div data-testid="access-denied" data-to={to} />;
}

function useLocation() {
  return { hash: "", pathname: "/dashboard/penjualan", search: "" };
}

function useSearchParams() {
  const [, bump] = React.useState(0);
  React.useEffect(() => subscribe(() => bump((value) => value + 1)), []);
  const search = currentPath.includes("?") ? currentPath.split("?")[1] : "";
  return [new URLSearchParams(search), () => {}];
}

module.exports = {
  __esModule: true,
  Link,
  Navigate,
  useLocation,
  useSearchParams,
  __reset: reset,
  __path: path,
  __subscribe: subscribe,
};
