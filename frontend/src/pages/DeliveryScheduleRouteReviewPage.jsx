import DeliveryRouteReviewContent from "../components/DeliveryRouteReviewContent";
import { DESKTOP_DELIVERY_SCHEDULE_PATH } from "../lib/mobileDeliverySchedule";

/** Desktop Jadwal Pengiriman review — AppShell + shared review content (never /m/...). */
export default function DeliveryScheduleRouteReviewPage() {
  return (
    <DeliveryRouteReviewContent
      backPath={DESKTOP_DELIVERY_SCHEDULE_PATH}
      shell="desktop"
    />
  );
}
