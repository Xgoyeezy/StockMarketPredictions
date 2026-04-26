function joinClasses(...values) {
  return values.filter(Boolean).join(' ')
}

export default function MetricCard({ label, value, tone = 'default', helper }) {
  return (
    <div className={joinClasses('ui-panel', 'ui-panel--metric', tone !== 'default' ? `ui-panel--${tone}` : '')}>
      <div className="ui-metric-card__header">
        <div className="ui-kicker">{label}</div>
      </div>
      <div className="ui-value ui-metric-card__value">{value}</div>
      {helper ? <div className="ui-note ui-metric-card__helper">{helper}</div> : null}
    </div>
  )
}
