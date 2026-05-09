import FeedbackState from './FeedbackState'

export default function ErrorState({
  title = 'Something needs attention.',
  description,
  error = null,
  eyebrow = 'Needs attention',
  actionLabel = 'Try again',
  onAction = null,
  secondaryActionLabel = '',
  onSecondaryAction = null,
  compact = false,
  className = '',
}) {
  const resolvedTitle = error?.display_title || title
  const resolvedDescription = description || error?.display_detail || ''
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
      title={resolvedTitle}
      description={resolvedDescription}
      actions={actions}
      compact={compact}
      className={className}
      role="alert"
    />
  )
}
