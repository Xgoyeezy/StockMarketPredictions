import { useEffect, useMemo } from 'react'
import { NavLink } from 'react-router-dom'
import { appConfig } from '../config/appConfig'
import MarketingTopNav from '../components/MarketingTopNav'
import {
  getPublicSiteBranding,
  getPublicSiteContact,
  getPublicSitePage,
  getPublicSitePages,
} from '../utils/publicSiteModel'

export default function PublicInfoPage({ pathname }) {
  const page = useMemo(() => getPublicSitePage(pathname), [pathname])
  const branding = useMemo(() => getPublicSiteBranding(), [])
  const contact = useMemo(() => getPublicSiteContact(), [])
  const navItems = useMemo(() => getPublicSitePages(), [])

  useEffect(() => {
    if (!page || typeof document === 'undefined') return
    document.title = `${page.title} | ${branding.name}`
  }, [branding.name, page])

  if (!page) return null

  return (
    <div className="marketing-home marketing-home--public">
      <MarketingTopNav branding={branding} pages={navItems} mode="public" />
      <div className="public-site__shell">
        <header className="public-site__hero">
          <div className="public-site__brand">
            <div className="public-site__mark" aria-hidden="true">
              {branding.name
                .split(/\s+/)
                .filter(Boolean)
                .slice(0, 2)
                .map((part) => part[0]?.toUpperCase() || '')
                .join('')}
            </div>
            <div className="public-site__brand-copy">
              <div className="ui-kicker">{page.eyebrow}</div>
              <h1 className="public-site__title">{page.key === 'connect' ? branding.name : page.headline}</h1>
              <p className="public-site__subtitle">
                {page.key === 'connect' ? branding.tagline : page.subhead}
              </p>
            </div>
          </div>
        </header>

        <main className="public-site__content">
          {page.body?.length ? (
            <section className="public-site__panel public-site__panel--lead">
              <h2 className="public-site__panel-title">Overview</h2>
              <div className="public-site__copy">
                {page.body.map((paragraph) => (
                  <p key={paragraph}>{paragraph}</p>
                ))}
              </div>
            </section>
          ) : null}

          {page.sections?.length ? (
            <div className="public-site__section-grid">
              {page.sections.map((section) => (
                <section key={section.title} className="public-site__panel">
                  <h2 className="public-site__panel-title">{section.title}</h2>
                  <ul className="public-site__list">
                    {section.items.map((item) => (
                      <li key={item}>{item}</li>
                    ))}
                  </ul>
                </section>
              ))}
            </div>
          ) : null}

          <section className="public-site__panel public-site__panel--meta">
            <h2 className="public-site__panel-title">
              {page.key === 'connect' ? (appConfig.personalMode ? 'Local notes and policies' : 'Customer support and policies') : 'Contact'}
            </h2>
            <div className="public-site__copy">
              {page.key === 'connect' ? (
                <>
                  {appConfig.personalMode ? (
                    <>
                      <p>
                        This local workstation is for self-directed research and own-account execution control.
                      </p>
                      <p>
                        Keep live routing disabled until paper behavior, risk limits, and broker readiness are verified.
                      </p>
                    </>
                  ) : (
                    <>
                      <p>
                        This customer workspace connects authorized Alpaca accounts while keeping linked-account
                        routing behind readiness, risk, approval, and reconciliation controls.
                      </p>
                      <p>
                        Review the customer policies here before activating account setup and paper execution.
                      </p>
                    </>
                  )}
                </>
              ) : (
                <p>
                  Questions about this customer workspace, linked accounts, or account-routing behavior
                  should use the contact details below.
                </p>
              )}
            </div>
            <div className="public-site__meta-links">
              <NavLink to="/terms" className="public-site__text-link">
                Terms of Use
              </NavLink>
              <NavLink to="/privacy" className="public-site__text-link">
                Privacy Policy
              </NavLink>
              {contact.href ? (
                <a
                  className="public-site__text-link"
                  href={contact.href}
                  target={contact.type === 'url' ? '_blank' : undefined}
                  rel={contact.type === 'url' ? 'noreferrer' : undefined}
                >
                  {contact.description}: {contact.label}
                </a>
              ) : (
                <span className="public-site__contact-placeholder">{contact.label}</span>
              )}
            </div>
          </section>
        </main>

        <footer className="public-site__footer">
          <span>{branding.name}</span>
          <span>{appConfig.personalMode ? 'Private own-account workflow' : 'Customer control-plane workflow'}</span>
        </footer>
      </div>
    </div>
  )
}
