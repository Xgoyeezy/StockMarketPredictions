import FocusRailItem from './FocusRailItem'

export default function FocusRail({ items = [], onToggle }) {
  return (
    <aside className="focus-rails" aria-label="Peripheral focus instruments" data-testid="focus-rails">
      {items.map((item) => (
        <FocusRailItem key={item.key} item={item} onToggle={onToggle} />
      ))}
    </aside>
  )
}
