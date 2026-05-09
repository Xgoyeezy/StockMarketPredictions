import Button from '../Button'

function joinClasses(...values) {
  return values.filter(Boolean).join(' ')
}

export default function DecisionRibbon({
  model,
  focusMode = 'decision_focus',
  onToggleFocusMode,
}) {
  if (!model) return null

  return (
    <section className="focus-ribbon" aria-label="Decision focus ribbon" data-testid="decision-ribbon">
      <div className="focus-ribbon__lead">
        <span className="focus-ribbon__kicker">Decision aperture</span>
        <strong>{model.pageLabel}</strong>
        <small>{model.focusModeLabel || 'Decision Focus'}</small>
      </div>
      <div className="focus-ribbon__items">
        {model.items.map((item) => (
          <div key={item.key} className={joinClasses('focus-ribbon__item', `focus-ribbon__item--${item.tone || 'neutral'}`)}>
            <span>{item.label}</span>
            <strong>{item.value}</strong>
          </div>
        ))}
      </div>
      <div
        className={joinClasses('focus-ribbon__system', `focus-ribbon__system--${model.system?.tone || 'neutral'}`)}
        aria-live="polite"
      >
        <span>{model.system?.label || 'System healthy'}</span>
        <strong>{model.system?.detail || 'Quiet background checks'}</strong>
        <small className="focus-ribbon__blockers">{model.safeStateSummary || `${model.blockerCount || 0} blockers`}</small>
      </div>
      <div className="focus-ribbon__controls">
        <span className="focus-ribbon__mode">
          {focusMode === 'decision_focus' ? model.focusModeDetail : 'Full Console keeps every rail expanded for review.'}
        </span>
        <span className="focus-ribbon__hint">{model.keyboardHint || 'Tab to rails. Enter expands.'}</span>
        <Button type="button" variant="ghost" size="sm" onClick={onToggleFocusMode}>
          {focusMode === 'decision_focus' ? 'Full Console' : 'Decision Focus'}
        </Button>
      </div>
    </section>
  )
}
