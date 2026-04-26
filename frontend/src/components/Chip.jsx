function joinClasses(...values) {
  return values.filter(Boolean).join(' ')
}

export default function Chip({
  children,
  tone = 'neutral',
  active = false,
  size = 'sm',
  className = '',
  as: Component = 'span',
  ...props
}) {
  return (
    <Component
      {...props}
      className={joinClasses(
        'ui-chip',
        `ui-chip--${tone}`,
        `ui-chip--${size}`,
        active ? 'ui-chip--active' : '',
        className,
      )}
    >
      {children}
    </Component>
  )
}
