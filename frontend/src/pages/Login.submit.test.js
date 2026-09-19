import { act } from "react";
import { createRoot } from "react-dom/client";
import Login from "./Login";

const mockLogin = jest.fn();

jest.mock("../lib/auth", () => ({
  useAuth: () => ({ login: mockLogin }),
}));

jest.mock("../components/BrandMark", () => () => <div data-testid="login-brand" />);

global.IS_REACT_ACT_ENVIRONMENT = true;

const flush = async () => {
  await act(async () => {
    await new Promise((resolve) => setTimeout(resolve, 0));
  });
};

const setNativeValue = (element, value) => {
  const setter = Object.getOwnPropertyDescriptor(Object.getPrototypeOf(element), "value").set;
  setter.call(element, value);
  element.dispatchEvent(new Event("input", { bubbles: true }));
  element.dispatchEvent(new Event("change", { bubbles: true }));
};

const fillForm = (container) => {
  setNativeValue(container.querySelector('[data-testid="login-email-input"]'), "admin@eternalpackaging.com");
  setNativeValue(container.querySelector('[data-testid="login-password-input"]'), "eternal123");
};

describe("Login submit mutations", () => {
  let container;
  let root;

  beforeEach(() => {
    container = document.createElement("div");
    document.body.appendChild(container);
    root = createRoot(container);
    mockLogin.mockReset();
    mockLogin.mockResolvedValue({ email: "admin@eternalpackaging.com", permissions: ["po.view"] });
  });

  afterEach(() => {
    act(() => root.unmount());
    container.remove();
  });

  test("click submit fires one login mutation", async () => {
    await act(async () => root.render(<Login />));
    fillForm(container);
    const button = container.querySelector('[data-testid="login-submit-button"]');
    await act(async () => {
      button.dispatchEvent(new MouseEvent("click", { bubbles: true, cancelable: true }));
    });
    await flush();
    expect(mockLogin).toHaveBeenCalledTimes(1);
    expect(mockLogin).toHaveBeenCalledWith("admin@eternalpackaging.com", "eternal123");
  });

  test("Enter on the form fires one login mutation", async () => {
    await act(async () => root.render(<Login />));
    fillForm(container);
    const form = container.querySelector('[data-testid="login-form"]');
    await act(async () => {
      form.dispatchEvent(new Event("submit", { bubbles: true, cancelable: true }));
    });
    await flush();
    expect(mockLogin).toHaveBeenCalledTimes(1);
  });

  test("rapid double click does not duplicate the login mutation", async () => {
    let release;
    mockLogin.mockImplementation(() => new Promise((resolve) => {
      release = resolve;
    }));
    await act(async () => root.render(<Login />));
    fillForm(container);
    const button = container.querySelector('[data-testid="login-submit-button"]');
    await act(async () => {
      button.dispatchEvent(new MouseEvent("click", { bubbles: true, cancelable: true }));
      button.dispatchEvent(new MouseEvent("click", { bubbles: true, cancelable: true }));
      container.querySelector('[data-testid="login-form"]')
        .dispatchEvent(new Event("submit", { bubbles: true, cancelable: true }));
    });
    expect(mockLogin).toHaveBeenCalledTimes(1);
    await act(async () => {
      release({ email: "admin@eternalpackaging.com" });
    });
    await flush();
    expect(mockLogin).toHaveBeenCalledTimes(1);
  });

  test("pending login plus another click does not start a second mutation", async () => {
    let release;
    mockLogin.mockImplementation(() => new Promise((resolve) => {
      release = resolve;
    }));
    await act(async () => root.render(<Login />));
    fillForm(container);
    const button = container.querySelector('[data-testid="login-submit-button"]');
    await act(async () => {
      button.dispatchEvent(new MouseEvent("click", { bubbles: true, cancelable: true }));
    });
    expect(mockLogin).toHaveBeenCalledTimes(1);
    await act(async () => {
      button.dispatchEvent(new MouseEvent("click", { bubbles: true, cancelable: true }));
    });
    expect(mockLogin).toHaveBeenCalledTimes(1);
    await act(async () => {
      release({ email: "admin@eternalpackaging.com" });
    });
    await flush();
    expect(mockLogin).toHaveBeenCalledTimes(1);
  });

  test("successful login keeps a single mutation and no error", async () => {
    await act(async () => root.render(<Login />));
    fillForm(container);
    await act(async () => {
      container.querySelector('[data-testid="login-submit-button"]')
        .dispatchEvent(new MouseEvent("click", { bubbles: true, cancelable: true }));
    });
    await flush();
    expect(mockLogin).toHaveBeenCalledTimes(1);
    expect(container.querySelector('[data-testid="login-error"]')).toBeNull();
    expect(container.querySelector('[data-testid="login-form"]')).toBeTruthy();
  });

  test("login error stays visible and a later submit can retry once", async () => {
    mockLogin.mockRejectedValueOnce({ response: { data: { detail: "Email atau kata sandi salah" } } });
    await act(async () => root.render(<Login />));
    fillForm(container);
    const button = container.querySelector('[data-testid="login-submit-button"]');
    await act(async () => {
      button.dispatchEvent(new MouseEvent("click", { bubbles: true, cancelable: true }));
    });
    await flush();
    expect(container.querySelector('[data-testid="login-error"]').textContent).toBe("Email atau kata sandi salah");
    expect(mockLogin).toHaveBeenCalledTimes(1);

    mockLogin.mockResolvedValueOnce({ email: "admin@eternalpackaging.com" });
    await act(async () => {
      button.dispatchEvent(new MouseEvent("click", { bubbles: true, cancelable: true }));
    });
    await flush();
    expect(mockLogin).toHaveBeenCalledTimes(2);
    expect(container.querySelector('[data-testid="login-error"]')).toBeNull();
  });
});
