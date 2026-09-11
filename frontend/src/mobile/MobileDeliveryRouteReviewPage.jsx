import DeliveryRouteReviewContent from "../components/DeliveryRouteReviewContent";
import { MOBILE_DELIVERY_SCHEDULE_PATH } from "../lib/mobileDeliverySchedule";

/** PWA review — MobileOperationalLayout + shared review content. */
export default function MobileDeliveryRouteReviewPage() {
  return (
    <DeliveryRouteReviewContent
      backPath={MOBILE_DELIVERY_SCHEDULE_PATH}
      shell="mobile"
    />
  );
}
