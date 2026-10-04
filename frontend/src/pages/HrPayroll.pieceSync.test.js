import { pieceInputSyncKey } from "./HrPayroll";

test("piece draft sync key changes only when server fields change", () => {
  const period = {
    id: "p1",
    updated_at: "2026-10-01T00:00:00Z",
    start_date: "2026-10-01",
    end_date: "2026-10-31",
    piece_bonus: {
      start_date: "2026-10-01",
      end_date: "2026-10-31",
      adjustments: { emp: { final_amount: 10, reason: "lembur" } },
    },
    piece_kasbon_inputs: [{ schedule_id: "s1", amount: 5 }],
  };
  const refreshed = {
    ...period,
    updated_at: "2026-10-04T00:00:00Z",
    piece_bonus: { ...period.piece_bonus, adjustments: { emp: { final_amount: 10, reason: "lembur" } } },
    piece_kasbon_inputs: [{ schedule_id: "s1", amount: 5 }],
  };
  expect(pieceInputSyncKey(refreshed)).toBe(pieceInputSyncKey(period));
  expect(pieceInputSyncKey({ ...period, start_date: "2026-10-02" })).not.toBe(pieceInputSyncKey(period));
  expect(pieceInputSyncKey({
    ...period,
    piece_bonus: { ...period.piece_bonus, adjustments: { emp: { final_amount: 12, reason: "lembur" } } },
  })).not.toBe(pieceInputSyncKey(period));
});
