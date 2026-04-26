function joinClasses(...values) {
  return values.filter(Boolean).join(' ')
}

export default function ActionBar({ children, className = '', compact = false }) {
  return (
    <div className={joinClasses('ui-action-bar', compact && 'ui-action-bar--compact', className)}>
      {children}
    </div>
  )
}
