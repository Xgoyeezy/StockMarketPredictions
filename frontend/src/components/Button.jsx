import { NativeButton, joinClasses } from './ControlPrimitives'

export default function Button({
  children,
  variant = 'ghost',
  size = 'md',
  className = '',
  ...props
}) {
  return (
    <NativeButton
      {...props}
      className={joinClasses(
        'ui-button',
        `ui-button--${variant}`,
        `ui-button--${size}`,
        className,
      )}
    >
      {children}
    </NativeButton>
  )
}
