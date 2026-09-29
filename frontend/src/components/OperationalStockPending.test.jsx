import React from "react";
import {render, screen, fireEvent, waitFor} from "@testing-library/react";
import "@testing-library/jest-dom";
import OperationalStockPending from "./OperationalStockPending";
import api from "../lib/api";
jest.mock("../lib/api", () => ({__esModule: true, default: {operationalStockPending: jest.fn(), retryOperationalStock: jest.fn()}}));
const row = {intent_id: "i1", order_id: "o1", source: "orders", po_number: "PO UJI", stage: "delivery", status: "pending", reason: "Saldo kurang"};
beforeEach(() => jest.clearAllMocks());
test("opening the queue is read-only; permission does not expose retry", async () => {
  api.operationalStockPending.mockResolvedValue({items: [row]});
  render(<OperationalStockPending canRetry={false} />);
  expect(api.operationalStockPending).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole("button", {name: "Pencatatan stok tertunda"}));
  expect(await screen.findByText("Saldo kurang")).toBeInTheDocument();
  expect(screen.queryByRole("button", {name: "Coba catat ulang"})).not.toBeInTheDocument();
  expect(api.retryOperationalStock).not.toHaveBeenCalled();
});
test("an unresolved retry remains pending and does not refresh stock as completed", async () => {
  api.operationalStockPending.mockResolvedValue({items: [row]});
  api.retryOperationalStock.mockResolvedValue({status: "pending"});
  const applied = jest.fn();
  render(<OperationalStockPending canRetry onApplied={applied} />);
  fireEvent.click(screen.getByRole("button", {name: "Pencatatan stok tertunda"}));
  fireEvent.click(await screen.findByRole("button", {name: "Coba catat ulang"}));
  await waitFor(() => expect(api.operationalStockPending).toHaveBeenCalledTimes(2));
  expect(api.retryOperationalStock).toHaveBeenCalledWith("o1", "i1", "orders");
  expect(applied).not.toHaveBeenCalled();
  expect(screen.getByText("Saldo kurang")).toBeInTheDocument();
});
test("403 is not displayed as an empty successful queue", async () => {
  api.operationalStockPending.mockRejectedValue({response: {status: 403}});
  render(<OperationalStockPending canRetry={false} />);
  fireEvent.click(screen.getByRole("button", {name: "Pencatatan stok tertunda"}));
  expect(await screen.findByRole("alert")).toHaveTextContent("Akses ditolak.");
  expect(screen.queryByText("Tidak ada pekerjaan tertunda pada antrean ini.")).not.toBeInTheDocument();
});
test("deleted source review cannot be retried from the panel", async () => {
  api.operationalStockPending.mockResolvedValue({items: [{...row, status: "review_required"}]});
  render(<OperationalStockPending canRetry />);
  fireEvent.click(screen.getByRole("button", {name: "Pencatatan stok tertunda"}));
  await screen.findByText("PO UJI");
  expect(screen.queryByRole("button", {name: "Coba catat ulang"})).not.toBeInTheDocument();
});
