import FeedbackState from './FeedbackState'

export default function ErrorState({
  title = 'Something needs attention.',
  description,
  eyebrow = 'Needs attention',
  actionLabel = 'Try again',
  onAction = null,
  secondaryActionLabel = '',
  onSecondaryAction = null,
  compact = false,
  className = '',
}) {
  const actions = []
  if (actionLabel && onAction) {
    actions.push({ label: actionLabel, onAction, variant: 'solid' })
  }
  if (secondaryActionLabel && onSecondaryAction) {
    actions.push({ label: secondaryActionLabel, onAction: onSecondaryAction, variant: 'ghost' })
  }

  return (
    <FeedbackState
      tone="negative"
      eyebrow={eyebrow}
      title={title}
      description={description}
      actions={actions}
      compact={compact}
      className={className}
      role="alert"
    />
  )
}
