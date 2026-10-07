import { ShoppingCart } from "lucide-react";
import { NavLink } from "react-router-dom";

export function SidebarFlyoutRow({ item, icon: Icon, onNavigate }) {
  const RowIcon = Icon || ShoppingCart;
  return (
    <NavLink
      to={item.to}
      end={!!item.end}
      className={({ isActive }) => "sidebar-menu-link" + (isActive ? " is-active" : "")}
      data-testid={item.testid}
      aria-label={item.label}
      onClick={onNavigate}
    >
      <span className="sidebar-menu-link-icon" aria-hidden="true">
        <RowIcon size={18} strokeWidth={1.75} />
      </span>
      <span className="sidebar-menu-link-label">{item.label}</span>
    </NavLink>
  );
}

export default function SidebarFlyoutGrid({
  innerRef,
  id,
  testid,
  title,
  ariaLabel,
  placement,
  style,
  items,
  sections,
  icons = {},
  onNavigate,
}) {
  const blocks = (sections || [{ ariaLabel: ariaLabel || `Menu ${title}`, items: items || [] }])
    .filter((block) => block && (block.items || []).length > 0);

  if (!blocks.length) return null;

  return (
    <div
      ref={innerRef}
      className="sidebar-flyout is-menu-list"
      id={id}
      data-testid={testid}
      data-placement={placement}
      role="dialog"
      aria-label={title}
      style={style}
    >
      <header className="sidebar-flyout-head"><h2>{title}</h2></header>
      {blocks.map((block) => (
        <div
          key={block.heading || block.ariaLabel || block.headingTestid || block.marker || "leaves"}
          id={block.marker || undefined}
          className={block.marker || undefined}
        >
          {block.heading ? (
            <div className="sidebar-menu-section" data-testid={block.headingTestid}>
              {block.heading}
            </div>
          ) : block.headingTestid ? (
            <div className="sidebar-menu-section is-unlabeled" data-testid={block.headingTestid} aria-hidden="true" />
          ) : null}
          <nav className="sidebar-menu-list" aria-label={block.ariaLabel || block.heading || title}>
            {block.items.map((item) => (
              <SidebarFlyoutRow
                key={item.key}
                item={item}
                icon={icons[item.key]}
                onNavigate={onNavigate}
              />
            ))}
          </nav>
        </div>
      ))}
    </div>
  );
}
