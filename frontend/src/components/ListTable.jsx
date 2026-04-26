function joinClasses(...values) {
  return values.filter(Boolean).join(' ')
}

export default function ListTable({ children, className = '' }) {
  return (
    <div className={joinClasses('ui-list-shell', className)}>
      {children}
    </div>
  )
}

export function ListCell({ kicker = '', title = '', meta = '', stack = null, badges = null, className = '' }) {
  const stackItems = Array.isArray(stack) ? stack.filter(Boolean) : stack
  const badgeItems = Array.isArray(badges) ? badges.filter(Boolean) : badges

  return (
    <div className={joinClasses('ui-list-cell', className)}>
      {kicker ? <div className="ui-list-cell__kicker">{kicker}</div> : null}
      {title ? <div className="ui-list-cell__title">{title}</div> : null}
      {meta ? <div className="ui-list-cell__meta">{meta}</div> : null}
      {stackItems ? (
        <div className="ui-list-cell__stack">
          {Array.isArray(stackItems) ? stackItems.map((item, index) => <span key={index}>{item}</span>) : stackItems}
        </div>
      ) : null}
      {badgeItems ? (
        <div className="ui-list-cell__badges">
          {Array.isArray(badgeItems) ? badgeItems.map((item, index) => <span key={index}>{item}</span>) : badgeItems}
        </div>
      ) : null}
    </div>
  )
}
