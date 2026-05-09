import { useId } from 'react'

export default function SectionCard({ title, subtitle, children, actions, eyebrow = '', id }) {
  const headingId = useId()
  const subtitleId = useId()

  return (
    <section
      className="ui-panel ui-panel--section"
      id={id}
      aria-labelledby={headingId}
      aria-describedby={subtitle ? subtitleId : undefined}
    >
      <div className="ui-panel__header">
        <div className="ui-panel__copy">
          {eyebrow ? <div className="ui-panel__eyebrow">{eyebrow}</div> : null}
          <h2 className="ui-panel__title" id={headingId}>{title}</h2>
          {subtitle ? <p className="ui-panel__subtitle" id={subtitleId}>{subtitle}</p> : null}
        </div>
        {actions ? <div className="ui-panel__actions">{actions}</div> : null}
      </div>
      <div className="ui-panel__body">{children}</div>
    </section>
  )
}
