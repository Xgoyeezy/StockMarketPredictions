import FeedbackState from './FeedbackState'

export default function EmptyState({
  title,
  description,
  eyebrow = 'Nothing here yet',
  tone = 'neutral',
  actionLabel = '',
  onAction = null,
  secondaryActionLabel = '',
  onSecondaryAction = null,
  compact = false,
  className = '',
}) {
  const actions = []
  if (actionLabel && onAction) {
    actions.push({ label: actionLabel, onAction })
  }
  if (secondaryActionLabel && onSecondaryAction) {
    actions.push({ label: secondaryActionLabel, onAction: onSecondaryAction, variant: 'subtle' })
  }

  return (
    <FeedbackState
      tone={tone}
      eyebrow={eyebrow}
      title={title}
      description={description}
      actions={actions}
      compact={compact}
      className={className}
    />
  )
}
