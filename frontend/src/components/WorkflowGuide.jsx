import { useId } from 'react'
import { usePreferences } from '../context/PreferencesContext'
import Button from './Button'
import Chip from './Chip'

const DEFAULT_WORKFLOW_LABELS = [
  'Find signal',
  'Qualify',
  'Act safely',
  'Review and repair',
]

export function buildWorkflowSteps(activeIndex = 0, labels = DEFAULT_WORKFLOW_LABELS) {
  return labels.map((label, index) => ({
    label,
    state: index < activeIndex ? 'completed' : index === activeIndex ? 'active' : 'upcoming',
  }))
}

function toneForStepState(state) {
  if (state === 'active') return 'positive'
  if (state === 'completed') return 'neutral'
  return 'neutral'
}

export default function WorkflowGuide({
  eyebrow = 'Page role',
  title,
  description = '',
  phaseLabel = '',
  phaseTone = 'neutral',
  steps = [],
  cards = [],
  compact = false,
  showSteps = true,
}) {
  const { preferences } = usePreferences()
  const titleId = useId()

  if (!preferences?.showWorkflowGuides) return null

  return (
    <section
      className={`ui-panel ui-panel--section workflow-guide ${compact ? 'workflow-guide--compact' : ''} ${
        showSteps ? '' : 'workflow-guide--no-steps'
      }`}
      aria-labelledby={titleId}
    >
      <div className="workflow-guide__header">
        <div className="workflow-guide__copy">
          {eyebrow ? <div className="ui-kicker">{eyebrow}</div> : null}
          <h2 className="workflow-guide__title" id={titleId}>{title}</h2>
          {description ? <p className="workflow-guide__description">{description}</p> : null}
        </div>
        {phaseLabel ? (
          <div className="workflow-guide__rail">
            <Chip tone={phaseTone} size={compact ? 'sm' : 'md'} active>
              {phaseLabel}
            </Chip>
          </div>
        ) : null}
      </div>

      {showSteps && steps.length ? (
        <ol className="workflow-guide__steps" aria-label="Workflow steps">
          {steps.map((step, index) => (
            <li
              key={`${step.label}-${index}`}
              className={`workflow-guide__step workflow-guide__step--${step.state || 'upcoming'}`}
              aria-current={step.state === 'active' ? 'step' : undefined}
            >
              <span className="workflow-guide__step-index">{index + 1}</span>
              <div className="workflow-guide__step-copy">
                <strong>{step.label}</strong>
                <Chip tone={toneForStepState(step.state)} size="sm" className="workflow-guide__step-chip">
                  {step.state === 'active' ? 'You are here' : step.state === 'completed' ? 'Covered' : 'Next'}
                </Chip>
              </div>
            </li>
          ))}
        </ol>
      ) : null}

      {cards.length ? (
        <div className="workflow-guide__cards">
          {cards.map((card) => (
            <article
              key={card.label}
              className={`workflow-guide__card workflow-guide__card--${card.tone || 'neutral'}`}
            >
              <span className="workflow-guide__card-label">{card.label}</span>
              <strong className="workflow-guide__card-value">{card.value}</strong>
              {card.detail ? <p className="workflow-guide__card-detail">{card.detail}</p> : null}
              {card.actionLabel ? (
                <Button
                  type="button"
                  variant={card.actionVariant || 'ghost'}
                  size="sm"
                  className="workflow-guide__card-action"
                  onClick={card.onAction}
                  disabled={Boolean(card.actionDisabled)}
                >
                  {card.actionLabel}
                </Button>
              ) : null}
            </article>
          ))}
        </div>
      ) : null}
    </section>
  )
}
