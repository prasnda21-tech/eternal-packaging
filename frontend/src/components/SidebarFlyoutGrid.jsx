import { ShoppingCart } from "lucide-react";
import { NavLink } from "react-router-dom";

export function SidebarFlyoutTile({ item, icon: Icon, onNavigate }) {
  const TileIcon = Icon || ShoppingCart;
  return (
    <NavLink
      to={item.to}
      end={!!item.end}
      className={({ isActive }) => "sales-menu-tile" + (isActive ? " is-active" : "")}
      data-tone={item.tone}
      data-testid={item.testid}
      aria-label={item.label}
      onClick={onNavigate}
    >
      <span className="sales-menu-tile-icon" aria-hidden="true">
        <TileIcon size={28} strokeWidth={1.6} />
      </span>
      <span className="sales-menu-tile-label">{item.label}</span>
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
      className="sidebar-flyout is-sales-grid"
      id={id}
      data-testid={testid}
      data-placement={placement}
      role="dialog"
      aria-label={title}
      style={style}
    >
      <header className="sidebar-flyout-head"><h2>{title}</h2></header>
      {blocks.map((block) => (
        <div key={block.heading || block.ariaLabel}>
          {block.heading ? (
            <div className="sidebar-flyout-head sidebar-flyout-subhead" data-testid={block.headingTestid}>
              <h3>{block.heading}</h3>
            </div>
          ) : null}
          <nav className="sales-menu-grid" aria-label={block.ariaLabel}>
            {block.items.map((item) => (
              <SidebarFlyoutTile
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
