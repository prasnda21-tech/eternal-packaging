jest.mock("react-router-dom", () => ({
  useParams: () => ({ id: "sj-1" }),
  useNavigate: () => () => {},
}), { virtual: true });

jest.mock("../components/DeliveryProofPanel", () => () => null);

import { latestTimelineEvent } from "./SuratJalan";

test("latest timeline event follows the order and ignores earlier stages", () => {
  const order = {
    timeline: [
      { stage: 11, at: "first" },
      { stage: 13, at: "load" },
      { stage: 11, at: "packed-again" },
    ],
  };
  expect(latestTimelineEvent(order, 11)).toEqual({ stage: 11, at: "packed-again" });
  expect(latestTimelineEvent(order, 13)).toEqual({ stage: 13, at: "load" });
  expect(latestTimelineEvent(order, 15)).toBeNull();
  expect(latestTimelineEvent(null, 11)).toBeNull();
});
