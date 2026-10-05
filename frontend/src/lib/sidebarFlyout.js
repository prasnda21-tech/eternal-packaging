export function nextSidebarMenu(current, clicked) {
  return current === clicked ? null : clicked;
}

const VIEW_MARGIN = 8;
const HORIZONTAL_RAIL_MAX_HEIGHT = 120;

export function placeSidebarFlyout(railRect, anchorRect, panelSize = {}, viewport = {}, gap = 0) {
  const vw = viewport.width ?? 1280;
  const vh = viewport.height ?? 800;
  const panelW = panelSize.width || 280;
  const panelH = panelSize.height || 0;
  const railRight = Math.round(railRect?.right || 0);
  const railBottom = Math.round(railRect?.bottom || 0);
  const railW = railRect?.width || 0;
  const railH = railRect?.height || 0;
  // Short horizontal shell (narrow viewport) drops below.
  // The permanent compact vertical rail always opens to the right.
  const isHorizontalRail = railH > 0 && railH <= HORIZONTAL_RAIL_MAX_HEIGHT && railW > railH;

  let left;
  let top;
  let scrimLeft;
  let scrimTop;
  let placement;

  if (!isHorizontalRail) {
    left = railRight + gap;
    top = Math.round(anchorRect?.top ?? railRect?.top ?? VIEW_MARGIN);
    scrimLeft = left;
    scrimTop = 0;
    placement = "right";
  } else {
    const maxLeft = Math.max(VIEW_MARGIN, vw - panelW - VIEW_MARGIN);
    left = Math.round(Math.min(Math.max(VIEW_MARGIN, anchorRect?.left ?? VIEW_MARGIN), maxLeft));
    top = railBottom + gap;
    scrimLeft = 0;
    scrimTop = railBottom;
    placement = "below";
  }

  if (panelH > 0) {
    const maxTop = vh - VIEW_MARGIN - panelH;
    if (top > maxTop) top = Math.max(VIEW_MARGIN, maxTop);
  }
  if (top < VIEW_MARGIN) top = VIEW_MARGIN;
  if (!isHorizontalRail && left < railRight) left = railRight;
  if (!isHorizontalRail && left + panelW > vw - VIEW_MARGIN) {
    left = Math.max(railRight, vw - VIEW_MARGIN - panelW);
  }
  if (!isHorizontalRail) scrimLeft = left;

  return { left, top, scrimLeft, scrimTop, placement };
}
