import { act } from "react";
import { createRoot } from "react-dom/client";
import MobileTaskCard from "./MobileTaskCard";

jest.mock("react-router-dom", () => ({
  Link: ({ to, children, ...props }) => <a href={to} {...props}>{children}</a>,
}), { virtual: true });

global.IS_REACT_ACT_ENVIRONMENT = true;

describe("MobileTaskCard schedule fields", () => {
  let container;
  let root;

  beforeEach(() => {
    container = document.createElement("div");
    document.body.appendChild(container);
    root = createRoot(container);
  });

  afterEach(() => {
    act(() => root.unmount());
    container.remove();
  });

  const renderCard = (task) => {
    act(() => {
      root.render(<MobileTaskCard task={task} workspaceId="print" />);
    });
  };

  test("renders operator, work date, and deadline in dd/mm/yy", () => {
    renderCard({
      task_id: "operator:po-1:0:s1",
      order_id: "po-1",
      order_number: "PO-1001",
      customer_name: "KALJA COFFEE",
      item: "Cup 16oz",
      line_idx: 0,
      line_id: "L1",
      operator_names: ["Fauzan Maaruf"],
      work_date: "2026-09-08",
      delivery_deadline: "2026-09-09",
      target_qty: 2000,
      completed_qty: 500,
      status: "sebagian",
      status_label: "Sebagian",
    });
    expect(container.textContent).toContain("Operator");
    expect(container.textContent).toContain("Fauzan Maaruf");
    expect(container.textContent).toContain("Tgl Pengerjaan");
    expect(container.textContent).toContain("08/09/26");
    expect(container.textContent).toContain("Deadline");
    expect(container.textContent).toContain("09/09/26");
    expect(container.textContent).toContain("Sebagian");
    expect(container.querySelector("a").getAttribute("href")).toBe("/m/orders/po-1?ws=print");
  });

  test("missing operator and work date use canonical empty wording", () => {
    renderCard({
      task_id: "screener:po-2:po:-",
      order_id: "po-2",
      order_number: "PO-1002",
      customer_name: "Kopi Rumah",
      item: "Cup 12oz",
      operator_names: [],
      work_date: null,
      delivery_deadline: null,
      target_qty: 100,
      completed_qty: 0,
      status: "belum",
      status_label: "Belum Mulai",
    });
    expect(container.textContent).toContain("Belum ditentukan");
    expect(container.textContent).toContain("Belum Dijadwalkan");
    expect(container.textContent).toContain("Menunggu approval");
    expect(container.textContent).not.toContain("Invalid Date");
    expect(container.textContent).not.toContain("undefined");
  });

  test("multiple operators stay on the same task identity", () => {
    renderCard({
      task_id: "helper:po-3:0:-",
      order_id: "po-3",
      order_number: "PO-1003",
      item: "Cup 16oz",
      line_idx: 0,
      line_id: "L1",
      operator_names: ["Fauzan Maaruf", "Indra Maulana"],
      work_date: "2026-09-08",
      target_qty: 10,
      completed_qty: 0,
      status: "belum",
    });
    expect(container.textContent).toContain("Fauzan Maaruf");
    expect(container.textContent).toContain("Indra Maulana");
    expect(container.querySelector("[data-line-idx]").getAttribute("data-line-idx")).toBe("0");
  });

  test("same-name lines remain separate cards", () => {
    const line0 = {
      task_id: "helper:po-4:0:-",
      order_id: "po-4",
      order_number: "PO-1004",
      item: "Cup 16oz",
      line_idx: 0,
      line_id: "L0",
      operator_names: ["Budi"],
      work_date: "2026-09-08",
      target_qty: 10,
      completed_qty: 0,
      status: "belum",
    };
    const line2 = {
      ...line0,
      task_id: "helper:po-4:2:-",
      line_idx: 2,
      line_id: "L2",
      operator_names: ["Sari"],
      work_date: "2026-09-10",
    };
    act(() => {
      root.render(
        <>
          <MobileTaskCard task={line0} workspaceId="prep" />
          <MobileTaskCard task={line2} workspaceId="prep" />
        </>,
      );
    });
    const cards = container.querySelectorAll("a.mop-card");
    expect(cards).toHaveLength(2);
    expect(cards[0].getAttribute("data-line-idx")).toBe("0");
    expect(cards[1].getAttribute("data-line-idx")).toBe("2");
    expect(cards[0].textContent).toContain("Budi");
    expect(cards[1].textContent).toContain("Sari");
    expect(cards[0].textContent).not.toContain("Sari");
  });
});
