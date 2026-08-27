export function nextSidebarMenu(current, clicked) {
  return current === clicked ? null : clicked;
}

const ICON_RAIL_MAX_WIDTH = 120;
const VIEW_MARGIN = 8;

export function placeSidebarFlyout(railRect, anchorRect, panelSize = {}, viewport = {}, gap = 0) {
  const vw = viewport.width ?? 1280;
  const vh = viewport.height ?? 800;
  const panelW = panelSize.width || 260;
  const panelH = panelSize.height || 0;
  const railRight = Math.round(railRect?.right || 0);
  const railBottom = Math.round(railRect?.bottom || 0);
  const isIconRail = (railRect?.width || 0) <= ICON_RAIL_MAX_WIDTH;

  let left;
  let top;
  let scrimLeft;
  let scrimTop;
  let placement;

  if (isIconRail) {
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
  if (isIconRail && left < railRight) left = railRight;

  return { left, top, scrimLeft, scrimTop, placement };
}
