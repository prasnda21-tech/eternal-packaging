import { useRef } from "react";

/** Arrow/Home/End navigation with a single keyboard entry point. */
export default function DashboardTabs({ id, label, items, value, onChange }) {
  const buttons = useRef({});
  function onKeyDown(event, index) {
    let next;
    if (event.key === "ArrowRight") next = (index + 1) % items.length;
    else if (event.key === "ArrowLeft") next = (index + items.length - 1) % items.length;
    else if (event.key === "Home") next = 0;
    else if (event.key === "End") next = items.length - 1;
    else return;
    event.preventDefault();
    onChange(items[next].id);
    buttons.current[items[next].id]?.focus();
  }
  return <div className="sd-tabs" role="tablist" aria-label={label}>
    {items.map((item, index) => <button key={item.id} type="button" role="tab"
      id={`${id}-tab-${item.id}`} aria-controls={`${id}-panel-${item.id}`}
      aria-selected={value === item.id} tabIndex={value === item.id ? 0 : -1}
      ref={(node) => { buttons.current[item.id] = node; }}
      onKeyDown={(event) => onKeyDown(event, index)} onClick={() => onChange(item.id)}>{item.label}</button>)}
  </div>;
}
