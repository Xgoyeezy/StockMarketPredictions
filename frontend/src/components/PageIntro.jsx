import ActionBar from './ActionBar'
import Chip from './Chip'

import { useId } from 'react'

export default function PageIntro({
  kicker,
  title,
  description,
  badge = '',
  helper = '',
  actions = null,
}) {
  const titleId = useId()
  const descriptionId = useId()
  const helperId = useId()

  return (
    <section
      className="ui-panel ui-panel--section ui-page-intro"
      aria-labelledby={titleId}
      aria-describedby={[description ? descriptionId : '', helper ? helperId : ''].filter(Boolean).join(' ') || undefined}
    >
      <div className="ui-page-intro__body">
        <div className="ui-page-intro__copy">
          {kicker ? <div className="ui-kicker">{kicker}</div> : null}
          <h1 className="ui-page-intro__title" id={titleId}>{title}</h1>
          {description ? <p className="ui-page-intro__description" id={descriptionId}>{description}</p> : null}
          {helper ? <p className="ui-page-intro__helper" id={helperId}>{helper}</p> : null}
        </div>
        <div className="ui-page-intro__rail">
          {badge ? <Chip tone="neutral" size="md">{badge}</Chip> : null}
          {actions ? <ActionBar compact className="ui-page-intro__actions">{actions}</ActionBar> : null}
        </div>
      </div>
    </section>
  )
}
