import { joinClasses } from './ControlPrimitives'

export default function Kicker({
  children,
  className = '',
  as: Component = 'span',
  ...props
}) {
  return (
    <Component
      {...props}
      className={joinClasses('ui-kicker', className)}
    >
      {children}
    </Component>
  )
}
