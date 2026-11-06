// Lets a vertical mouse wheel scroll a horizontal strip: wheel down moves right, wheel up moves left.
// The event is only claimed while the strip can still move that way, so the page scrolls on at the ends.
const LINE_PX = 16;

export function wheelToScrollLeftDelta(event, element) {
  const dx = Number(event?.deltaX) || 0;
  const dy = Number(event?.deltaY) || 0;
  if (Math.abs(dx) >= Math.abs(dy)) return 0;
  const unit = event?.deltaMode === 1 ? LINE_PX : event?.deltaMode === 2 ? (element?.clientWidth || 0) : 1;
  return dy * unit;
}

export function handleWheelScrollX(event, element) {
  if (!element || event?.ctrlKey || event?.defaultPrevented) return false;
  const delta = wheelToScrollLeftDelta(event, element);
  if (!delta) return false;
  const max = Math.max(0, (element.scrollWidth || 0) - (element.clientWidth || 0));
  const before = Number(element.scrollLeft) || 0;
  const next = Math.min(max, Math.max(0, before + delta));
  if (next === before) return false;
  element.scrollLeft = next;
  event.preventDefault?.();
  return true;
}

export function bindWheelScrollX(element) {
  if (!element?.addEventListener) return () => {};
  const listener = (event) => { handleWheelScrollX(event, element); };
  element.addEventListener('wheel', listener, { passive: false });
  return () => element.removeEventListener('wheel', listener);
}
