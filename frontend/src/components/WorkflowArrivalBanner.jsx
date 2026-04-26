import { useId } from 'react'
import { usePreferences } from '../context/PreferencesContext'
import Button from './Button'

export default function WorkflowArrivalBanner({
  title,
  detail = '',
  tone = 'info',
  actions = [],
  onDismiss = null,
  dismissLabel = 'Dismiss',
}) {
  const { preferences } = usePreferences()
  const titleId = useId()
  const detailId = useId()
  const isUrgent = tone === 'warning' || tone === 'negative'

  if (!title || !preferences?.showArrivalBanners) return null

  return (
    <section
      className={`workflow-arrival-banner workflow-arrival-banner--${tone}`}
      role={isUrgent ? 'alert' : 'status'}
      aria-live={isUrgent ? 'assertive' : 'polite'}
      aria-labelledby={titleId}
      aria-describedby={detail ? detailId : undefined}
    >
      <div className="workflow-arrival-banner__copy">
        <strong id={titleId}>{title}</strong>
        {detail ? <p id={detailId}>{detail}</p> : null}
      </div>
      {actions.length ? (
        <div className="workflow-arrival-banner__actions">
          {actions.map((action) => (
            <Button
              key={action.label}
              type="button"
              variant={action.variant || 'ghost'}
              size={action.size || 'sm'}
              onClick={action.onClick}
              disabled={Boolean(action.disabled)}
            >
              {action.label}
            </Button>
          ))}
          {onDismiss ? (
            <Button type="button" variant="ghost" size="sm" onClick={onDismiss} aria-label={dismissLabel}>
              {dismissLabel}
            </Button>
          ) : null}
        </div>
      ) : onDismiss ? (
        <div className="workflow-arrival-banner__actions">
          <Button type="button" variant="ghost" size="sm" onClick={onDismiss} aria-label={dismissLabel}>
            {dismissLabel}
          </Button>
        </div>
      ) : null}
    </section>
  )
}
