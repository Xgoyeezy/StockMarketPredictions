import FeedbackState from './FeedbackState'

export default function LoadingBlock({
  label = 'Loading desk',
  detail = 'Pulling the latest desk state so this surface opens with current context.',
  compact = false,
  className = '',
}) {
  return (
    <FeedbackState
      tone="info"
      eyebrow="Loading"
      title={label}
      description={detail}
      loading
      compact={compact}
      className={className}
      role="status"
    />
  )
}
