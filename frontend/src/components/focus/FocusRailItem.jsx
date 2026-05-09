import { NavLink } from 'react-router-dom'
import StatusBadge from '../StatusBadge'

function joinClasses(...values) {
  return values.filter(Boolean).join(' ')
}

export default function FocusRailItem({ item, onToggle }) {
  if (!item) return null
  const metaLabel = item.metaLabel || `${item.count || 0} tracked`
  const stateLabel = item.critical ? 'Blocking' : item.expanded ? 'Expanded' : item.pinned ? 'Pinned' : item.stateLabel || 'Quiet'

  return (
    <article
      className={joinClasses(
        'focus-rail-item',
        `focus-rail-item--${item.tone || 'neutral'}`,
        item.current && 'focus-rail-item--current',
        item.expanded && 'focus-rail-item--expanded',
        item.critical && 'focus-rail-item--critical',
      )}
      data-testid={`focus-rail-${item.key}`}
    >
      <button
        type="button"
        className="focus-rail-item__button"
        aria-expanded={item.expanded ? 'true' : 'false'}
        aria-controls={`focus-rail-panel-${item.key}`}
        aria-label={`${item.label}: ${item.next}. ${stateLabel}.`}
        onClick={() => onToggle(item.key)}
      >
        <span className="focus-rail-item__header">
          <span className="focus-rail-item__label">{item.label}</span>
          <span className="focus-rail-item__status">{stateLabel}</span>
        </span>
        <strong>{item.next}</strong>
        <span className="focus-rail-item__meta">{metaLabel}</span>
        {item.shortcutLabel ? <span className="focus-rail-item__shortcut">{item.shortcutLabel}</span> : null}
      </button>
      <div className="focus-rail-item__panel" id={`focus-rail-panel-${item.key}`} hidden={!item.expanded}>
        <p>{item.detail || item.emptyState}</p>
        <div className="focus-rail-item__actions">
          {item.critical ? <StatusBadge tone="negative">Blocking</StatusBadge> : item.pinned ? <StatusBadge tone="info">Pinned</StatusBadge> : <StatusBadge tone="neutral">{stateLabel}</StatusBadge>}
          <NavLink className="focus-rail-item__link" to={item.to}>
            {item.actionLabel || 'Open panel'}
          </NavLink>
        </div>
      </div>
    </article>
  )
}
