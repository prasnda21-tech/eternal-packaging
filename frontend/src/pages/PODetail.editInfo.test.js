import fs from "fs";
import path from "path";
import { act } from "react";
import { createRoot } from "react-dom/client";
import EditPoInfoModal, {
  buildPoInfoUpdatePayload,
  toDateInputValue,
} from "../components/EditPoInfoModal";

global.IS_REACT_ACT_ENVIRONMENT = true;

const detail = fs.readFileSync(path.join(__dirname, "PODetail.jsx"), "utf8");
const apiSrc = fs.readFileSync(path.join(__dirname, "../lib/api.js"), "utf8");
const modalSrc = fs.readFileSync(
  path.join(__dirname, "../components/EditPoInfoModal.jsx"),
  "utf8"
);
const outletSrc = fs.readFileSync(
  path.join(__dirname, "../components/OutletLocationModal.jsx"),
  "utf8"
);

const mockUpdateOrderInfo = jest.fn();

jest.mock("../lib/api", () => ({
  __esModule: true,
  default: {
    updateOrderInfo: (...args) => mockUpdateOrderInfo(...args),
  },
}));

function setInputValue(el, value) {
  const proto = el.tagName === "TEXTAREA" ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
  const setter = Object.getOwnPropertyDescriptor(proto, "value").set;
  setter.call(el, value);
  el.dispatchEvent(new Event("input", { bubbles: true }));
  el.dispatchEvent(new Event("change", { bubbles: true }));
}

describe("Edit Informasi PO — Informasi PO card", () => {
  const commandBar = fs.readFileSync(
    path.join(__dirname, "../components/poDetail/PoGlobalCommandBar.jsx"),
    "utf8"
  );
  test("card action Edit Informasi uses po.edit and does not replace Edit Lokasi", () => {
    expect(commandBar).toMatch(/Edit Informasi/);
    expect(commandBar).toMatch(/po-edit-info/);
    expect(detail).toMatch(/canEditPoInfo/);
    expect(detail).toMatch(/hasPerm\("po\.edit"\)/);
    expect(detail).toMatch(/EditPoInfoModal/);
    expect(commandBar).toMatch(/Atur Lokasi|Edit Lokasi/);
    expect(commandBar).toMatch(/po-atur-lokasi/);
    expect(detail).toMatch(/OutletLocationModal/);
    expect(detail).toMatch(/delivery\.location\.manage/);
    expect(detail).toMatch(/data-testid="detail-address"/);
    expect(detail).toMatch(/data-testid="detail-estimated-delivery"/);
    expect(detail).toMatch(/data-testid="detail-tanggal-pengiriman"/);
    expect(detail).not.toMatch(/DeliveryDeadlinePanel/);
    expect(detail).not.toMatch(/data-testid="detail-deadline"/);
  });

  test("API client patches canonical /orders/{id}/info only", () => {
    expect(apiSrc).toMatch(/updateOrderInfo:\s*\(id,\s*payload\)\s*=>\s*client\.patch\(`\/orders\/\$\{id\}\/info`/);
    expect(modalSrc).toMatch(/api\.updateOrderInfo\(order\.id,\s*payload\)/);
    expect(modalSrc).toMatch(/buildPoInfoUpdatePayload/);
    expect(modalSrc).not.toMatch(/saveDeliveryOutlet/);
    expect(outletSrc).toMatch(/saveDeliveryOutlet/);
  });

  test("modal form only exposes Alamat / Tanggal Pengiriman / Estimasi Kirim + Batal/Simpan", () => {
    expect(modalSrc).toMatch(/edit-po-info-address/);
    expect(modalSrc).toMatch(/edit-po-info-tanggal-pengiriman/);
    expect(modalSrc).toMatch(/edit-po-info-estimated-delivery/);
    expect(modalSrc).toMatch(/edit-po-info-cancel/);
    expect(modalSrc).toMatch(/edit-po-info-save/);
    expect(modalSrc).toMatch(/>Alamat</);
    expect(modalSrc).toMatch(/>Tanggal Pengiriman</);
    expect(modalSrc).toMatch(/>Estimasi Kirim</);
    expect(modalSrc).not.toMatch(/>Deadline</);
    expect(modalSrc).not.toMatch(/edit-po-info-deadline/);
    expect(modalSrc).not.toMatch(/Customer/);
    expect(modalSrc).not.toMatch(/Ekspedisi/);
    expect(modalSrc).not.toMatch(/current_stage/);
    expect(modalSrc).not.toMatch(/delivery_location/);
  });
});

describe("Edit Informasi PO — helpers", () => {
  test("toDateInputValue uses persisted ISO calendar day", () => {
    expect(toDateInputValue("2026-09-10T00:00:00+00:00")).toBe("2026-09-10");
    expect(toDateInputValue("2026-09-10")).toBe("2026-09-10");
    expect(toDateInputValue("")).toBe("");
    expect(toDateInputValue(null)).toBe("");
  });

  test("buildPoInfoUpdatePayload is field-scoped to canonical keys", () => {
    const payload = buildPoInfoUpdatePayload({
      address: "Jl. Baru 1",
      tanggal_pengiriman: "2026-09-20",
      estimated_delivery: "2026-09-18",
      customer: "SHOULD_NOT_APPEAR",
      current_stage: 9,
    });
    expect(payload).toEqual({
      address: "Jl. Baru 1",
      tanggal_pengiriman: "2026-09-20",
      estimated_delivery: "2026-09-18",
    });
    expect(Object.keys(payload).sort()).toEqual([
      "address",
      "estimated_delivery",
      "tanggal_pengiriman",
    ]);
  });
});

describe("Edit Informasi PO — modal behavior", () => {
  let container;
  let root;

  beforeEach(() => {
    mockUpdateOrderInfo.mockReset();
    container = document.createElement("div");
    document.body.appendChild(container);
    root = createRoot(container);
  });

  afterEach(async () => {
    await act(async () => {
      root.unmount();
    });
    container.remove();
  });

  const order = {
    id: "ord-info-1",
    address: "Jl. Lama No. 1",
    delivery_deadline_manual: "2026-09-10T00:00:00+07:00",
    delivery_deadline_source: "manual",
    tanggal_pengiriman: "2026-09-10",
    tanggal_pengiriman_source: "manual",
    scheduled_delivery_date: "2026-09-10",
    estimated_delivery: "2026-09-12T00:00:00+00:00",
    customer: "PT Fixture",
    current_stage: 3,
  };

  test("CASE 1 — open editor shows persisted alamat/tanggal pengiriman/estimasi", async () => {
    await act(async () => {
      root.render(<EditPoInfoModal open order={order} onClose={() => {}} onSaved={() => {}} />);
    });
    expect(container.querySelector('[data-testid="edit-po-info-address"]').value).toBe(
      "Jl. Lama No. 1"
    );
    expect(container.querySelector('[data-testid="edit-po-info-tanggal-pengiriman"]').value).toBe(
      "2026-09-10"
    );
    expect(
      container.querySelector('[data-testid="edit-po-info-estimated-delivery"]').value
    ).toBe("2026-09-12");
  });

  test("CASE 2 — Cancel discards draft and does not call update", async () => {
    const onClose = jest.fn();
    await act(async () => {
      root.render(<EditPoInfoModal open order={order} onClose={onClose} onSaved={() => {}} />);
    });
    await act(async () => {
      setInputValue(container.querySelector('[data-testid="edit-po-info-address"]'), "DRAFT ONLY");
    });
    await act(async () => {
      container.querySelector('[data-testid="edit-po-info-cancel"]').click();
    });
    expect(onClose).toHaveBeenCalled();
    expect(mockUpdateOrderInfo).not.toHaveBeenCalled();
  });

  test("CASE 3–6 — Save sends only canonical fields (combined)", async () => {
    mockUpdateOrderInfo.mockResolvedValue({
      order: {
        ...order,
        address: "Jl. Baru No. 9",
        tanggal_pengiriman: "2026-09-20",
        estimated_delivery: "2026-09-19T00:00:00+00:00",
      },
    });
    const onSaved = jest.fn();
    const onClose = jest.fn();
    await act(async () => {
      root.render(<EditPoInfoModal open order={order} onClose={onClose} onSaved={onSaved} />);
    });

    await act(async () => {
      setInputValue(container.querySelector('[data-testid="edit-po-info-address"]'), "Jl. Baru No. 9");
      setInputValue(container.querySelector('[data-testid="edit-po-info-tanggal-pengiriman"]'), "2026-09-20");
      setInputValue(
        container.querySelector('[data-testid="edit-po-info-estimated-delivery"]'),
        "2026-09-19"
      );
    });

    await act(async () => {
      container.querySelector('[data-testid="edit-po-info-save"]').click();
    });

    expect(mockUpdateOrderInfo).toHaveBeenCalledTimes(1);
    expect(mockUpdateOrderInfo).toHaveBeenCalledWith("ord-info-1", {
      address: "Jl. Baru No. 9",
      tanggal_pengiriman: "2026-09-20",
      estimated_delivery: "2026-09-19",
    });
    const sent = mockUpdateOrderInfo.mock.calls[0][1];
    expect(sent.customer).toBeUndefined();
    expect(sent.current_stage).toBeUndefined();
    expect(sent.delivery_location).toBeUndefined();
    expect(sent.deadline).toBeUndefined();
    expect(onSaved).toHaveBeenCalled();
    expect(onClose).toHaveBeenCalled();
  });

  test("CASE 9 — Edit Lokasi remains a separate canonical path", () => {
    expect(outletSrc).toMatch(/ATUR LOKASI PENGIRIMAN/);
    expect(outletSrc).toMatch(/saveDeliveryOutlet/);
    expect(outletSrc).toMatch(/lat/);
    expect(outletSrc).toMatch(/lng/);
    expect(modalSrc).not.toMatch(/saveDeliveryOutlet/);
    expect(modalSrc).not.toMatch(/place_id/);
    expect(modalSrc).not.toMatch(/\blat\b/);
    expect(modalSrc).not.toMatch(/\blng\b/);
  });
});
