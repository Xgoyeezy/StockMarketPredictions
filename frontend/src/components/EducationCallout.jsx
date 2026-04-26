import { Link, useLocation } from 'react-router-dom'
import Kicker from './Kicker'

export default function EducationCallout({
  topic = '',
  kicker = 'Trading guide',
  title,
  body,
  bullets = [],
  linkLabel = 'Open guide',
}) {
  const location = useLocation()
  const destination = {
    pathname: '/education',
    search: location.search,
    hash: topic ? `#${topic}` : '',
  }

  return (
    <div className="education-callout">
      <div className="education-callout__copy">
        <Kicker as="div" className="education-callout__kicker">{kicker}</Kicker>
        <strong>{title}</strong>
        <p>{body}</p>
        {bullets.length ? (
          <ul className="education-callout__list">
            {bullets.map((item) => (
              <li key={item}>{item}</li>
            ))}
          </ul>
        ) : null}
      </div>
      <Link className="education-callout__action" to={destination}>
        {linkLabel}
      </Link>
    </div>
  )
}
