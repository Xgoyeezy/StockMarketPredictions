import { joinClasses } from './ControlPrimitives'

export default function SignalDot({
  accent = '',
  glow = '',
  size = 'md',
  className = '',
  style = {},
  ...props
}) {
  return (
    <span
      {...props}
      className={joinClasses('ui-signal-dot', `ui-signal-dot--${size}`, className)}
      style={{
        '--ui-signal-dot-color': accent || undefined,
        '--ui-signal-dot-glow': glow || accent || undefined,
        ...style,
      }}
    />
  )
}
