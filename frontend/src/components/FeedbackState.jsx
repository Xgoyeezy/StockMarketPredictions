import Button from './Button'

function joinClasses(...values) {
  return values.filter(Boolean).join(' ')
}

export default function FeedbackState({
  tone = 'neutral',
  eyebrow = '',
  title,
  description = '',
  actions = [],
  loading = false,
  compact = false,
  className = '',
  role = undefined,
}) {
  return (
    <section
      className={joinClasses(
        'ui-state',
        `ui-state--${tone}`,
        compact && 'ui-state--compact',
        className,
      )}
      role={role}
    >
      <div className="ui-state__copy">
        {eyebrow ? <div className="ui-state__eyebrow">{eyebrow}</div> : null}
        <div className="ui-state__title-row">
          {loading ? <span className="ui-state__spinner" aria-hidden="true" /> : null}
          <strong className="ui-state__title">{title}</strong>
        </div>
        {description ? <p className="ui-state__description">{description}</p> : null}
      </div>

      {actions.length ? (
        <div className="ui-state__actions">
          {actions.map((action) => (
            <Button
              key={action.label}
              type="button"
              variant={action.variant || 'ghost'}
              size={compact ? 'sm' : 'md'}
              onClick={action.onAction}
              disabled={Boolean(action.disabled)}
            >
              {action.label}
            </Button>
          ))}
        </div>
      ) : null}
    </section>
  )
}
