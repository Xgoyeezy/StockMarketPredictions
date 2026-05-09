import { NavLink, useLocation, useNavigate } from 'react-router-dom'
import Button from './Button'

function joinClasses(...values) {
  return values.filter(Boolean).join(' ')
}

export default function MarketingTopNav({ branding, pages, mode = 'home' }) {
  const location = useLocation()
  const navigate = useNavigate()
  const navPages = Array.isArray(pages)
    ? pages.filter((page) => page.key !== 'connect' && String(page.path || '').trim() && String(page.path || '').trim() !== '/')
    : []
  const primaryPublicKeys = new Set(['tradingService', 'riskControls', 'auditReadyRecords', 'reviewProcess', 'integrations'])
  const publicPages = mode === 'home'
    ? navPages
    : navPages.filter((page) => primaryPublicKeys.has(page.key))
  const visiblePages = mode === 'home' ? publicPages.slice(0, 4) : publicPages
  const showHomeLink = location.pathname !== '/'

  const mergedPages = (() => {
    const list = []
    visiblePages.forEach((page) => list.push(page))
    list.push({ key: '__pricing__', path: '/pricing', navLabel: 'Pricing' })

    const seen = new Set()
    return list.filter((item) => {
      const rawPath = String(item?.path || '').trim()
      if (!rawPath) return false
      const path = rawPath.replace(/\/+$/, '') || '/'
      if (!path) return false
      if (seen.has(path)) return false
      seen.add(path)
      return true
    })
  })()

  function go(to) {
    if (typeof document !== 'undefined' && typeof document.startViewTransition === 'function') {
      document.startViewTransition(() => {
        navigate(to)
      })
      return
    }
    navigate(to)
  }

  return (
    <header className="marketing-home__nav">
      <NavLink
        to="/"
        className="marketing-home__brand"
        aria-label="Back to the main page"
        onClick={(event) => {
          if (!showHomeLink) return
          event.preventDefault()
          go('/')
        }}
      >
        <div className="marketing-home__mark" aria-hidden="true">
          {String(branding?.name || 'Desk')
            .split(/\s+/)
            .filter(Boolean)
            .slice(0, 2)
            .map((part) => part[0]?.toUpperCase() || '')
            .join('')}
        </div>
        <div className="marketing-home__brand-copy">
          <div className="ui-kicker">Trading control plane</div>
          <strong>{branding?.name || 'Trading desk'}</strong>
          <div className="ui-muted">{branding?.tagline || ''}</div>
        </div>
      </NavLink>

      <nav className="marketing-home__nav-links" aria-label="Marketing navigation">
        {mergedPages.map((page) => (
          <NavLink
            key={page.key || page.path}
            to={page.path}
            className={({ isActive }) => joinClasses('marketing-home__nav-link', isActive && 'marketing-home__nav-link--active')}
          >
            {page.navLabel}
          </NavLink>
        ))}
      </nav>

      <div className="marketing-home__nav-cta">
        <Button type="button" variant="ghost" size="sm" onClick={() => go('/login')}>
          Sign in
        </Button>
        <Button type="button" variant="solid" size="sm" onClick={() => go('/pricing')}>
          View plans
        </Button>
      </div>
    </header>
  )
}
