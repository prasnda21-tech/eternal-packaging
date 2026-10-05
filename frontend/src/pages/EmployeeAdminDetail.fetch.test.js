import { act } from "react";
import { createRoot } from "react-dom/client";
import EmployeeAdminDetail from "./EmployeeAdminDetail";
import api from "../lib/api";

jest.mock("react-router-dom", () => ({
  Link: ({ children }) => <a>{children}</a>,
  useParams: () => ({ id: global.__eaRequestId }),
}), { virtual: true });

jest.mock("../lib/auth", () => ({
  useAuth: () => ({ hasPerm: () => false }),
}));

jest.mock("../components/InAppDocumentViewer", () => () => null);

jest.mock("../lib/api", () => ({
  __esModule: true,
  default: {
    employeeAdminGetRequest: jest.fn(),
    employeeAdminFileBlob: jest.fn(),
    employeeAdminDecide: jest.fn(),
  },
}));

global.IS_REACT_ACT_ENVIRONMENT = true;

const flush = () => new Promise((resolve) => setTimeout(resolve, 0));

test("detail loads the current request and drops a response after unmount", async () => {
  let resolveRequest;
  global.__eaRequestId = "req-1";
  api.employeeAdminGetRequest.mockImplementation(() => new Promise((resolve) => { resolveRequest = resolve; }));
  const host = document.createElement("div");
  document.body.appendChild(host);
  const root = createRoot(host);
  await act(async () => { root.render(<EmployeeAdminDetail />); });
  expect(api.employeeAdminGetRequest).toHaveBeenCalledTimes(1);
  expect(api.employeeAdminGetRequest).toHaveBeenCalledWith("req-1");
  await act(async () => { root.unmount(); });
  await act(async () => {
    resolveRequest({ request: { id: "req-1", status: "DIAJUKAN", form_snapshot: { fields: [] } } });
    await flush();
  });
  expect(api.employeeAdminGetRequest).toHaveBeenCalledTimes(1);
  host.remove();
});
