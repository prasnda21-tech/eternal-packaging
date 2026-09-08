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

  test("canonical customer wins over outlet", () => {
    renderCard({
      task_id: "screener:po-c:po:-",
      order_id: "po-c",
      order_number: "PO-C",
      customer: "PT ABC",
      customer_name: "",
      outlet: "Yogyakarta",
      item: "Cup",
      target_qty: 10,
      completed_qty: 0,
      status: "belum",
    });
    expect(container.querySelector(".mop-card-title").textContent).toBe("PT ABC");
    expect(container.textContent).not.toMatch(/Customer[\s\S]*Yogyakarta/);
  });

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
    expect(container.textContent).toContain("Produk / Item");
    expect(container.textContent).toContain("Cup 16oz");
    expect(container.querySelectorAll(".mop-card-sub")).toHaveLength(0);
    expect(container.querySelector('[data-testid="mobile-task-qty"]').textContent).toContain("2.000 pcs");
    expect(container.textContent).not.toContain("500 / 2.000");
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
    expect(cards[0].querySelector('[data-testid="mobile-task-product"]').textContent).toContain("Cup 16oz");
    expect(cards[1].querySelector('[data-testid="mobile-task-product"]').textContent).toContain("Cup 16oz");
    expect(cards[0].querySelector('[data-testid="mobile-task-qty"]').textContent).toContain("10 pcs");
  });

  test("Cetak uses split target_qty, not PO total", () => {
    renderCard({
      task_id: "operator:po-5:0:s1",
      order_id: "po-5",
      order_number: "PO-1005",
      customer_name: "PT Example",
      division: "operator",
      required_input_type: "qty_cetak",
      item: "Standing Pouch 250g",
      line_idx: 0,
      line_id: "L1",
      operator_names: ["Budi"],
      work_date: "2026-09-08",
      delivery_deadline: "2026-09-10",
      target_qty: 1000,
      completed_qty: 200,
      status: "sebagian",
      status_label: "Sebagian",
    });
    expect(container.querySelector('[data-testid="mobile-task-qty"]').textContent).toContain("1.000 pcs");
    expect(container.textContent).not.toContain("1.200");
    expect(container.textContent).not.toContain("200 / 1.000");
  });

  test("long product names wrap inside the card", () => {
    renderCard({
      task_id: "packing:po-6:0:-",
      order_id: "po-6",
      order_number: "PO-1006",
      division: "packing",
      required_input_type: "qty_packing",
      item: "Standing Pouch Kraft 250g Window Matte Laminating Custom Print Long Name",
      line_idx: 0,
      line_id: "L9",
      operator_names: ["Budi"],
      work_date: "2026-09-08",
      target_qty: 12500,
      completed_qty: 0,
      status: "belum",
      status_label: "Belum Mulai",
    });
    const product = container.querySelector('[data-testid="mobile-task-product"] .mop-task-fact-value');
    expect(product.textContent).toContain("Standing Pouch Kraft 250g Window Matte Laminating Custom Print Long Name");
    expect(product.className).toContain("mop-task-fact-value");
    expect(container.querySelector('[data-testid="mobile-task-qty"]').textContent).toContain("12.500 pcs");
  });

  test("PO-level multi-item uses existing item summary, not an invented single name", () => {
    renderCard({
      task_id: "screener:po-7:po:-",
      order_id: "po-7",
      order_number: "PO-1007",
      division: "screener",
      required_input_type: "qty_screener",
      item: "3 item",
      operator_names: ["Budi"],
      work_date: "2026-09-08",
      target_qty: 2500,
      completed_qty: 0,
      status: "belum",
      status_label: "Belum Mulai",
    });
    expect(container.querySelector('[data-testid="mobile-task-product"]').textContent).toContain("3 item");
    expect(container.querySelector('[data-testid="mobile-task-qty"]').textContent).toContain("2.500 pcs");
  });

  test("Pengiriman does not display photo-count target as pcs", () => {
    renderCard({
      task_id: "terkirim:po-8:po:-",
      order_id: "po-8",
      order_number: "PO-1008",
      division: "terkirim",
      required_input_type: "upload_foto",
      item: "Cup 16oz",
      operator_names: ["Driver A"],
      work_date: "2026-09-08",
      target_qty: 2,
      completed_qty: 0,
      status: "belum",
      status_label: "Belum Mulai",
    });
    expect(container.querySelector('[data-testid="mobile-task-product"]').textContent).toContain("Cup 16oz");
    expect(container.querySelector('[data-testid="mobile-task-qty"]').textContent).toContain("—");
    expect(container.querySelector('[data-testid="mobile-task-qty"]').textContent).not.toContain("pcs");
    expect(container.textContent).toContain("Belum Mulai");
  });
});
