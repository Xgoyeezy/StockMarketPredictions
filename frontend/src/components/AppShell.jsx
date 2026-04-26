import { useEffect, useMemo, useRef, useState } from 'react'
import { NavLink, useLocation, useNavigate } from 'react-router-dom'
import { usePreferences } from '../context/PreferencesContext'
import { useAuth } from '../context/useAuth'
import { appConfig } from '../config/appConfig'
import { recordRecentTicker } from '../api/client'
import {
  getSurfaceLabel,
  getTradingStyleProfile,
  isWorkflowSurfacePath,
  normalizeTradingStyle,
  resolveReviewSurface,
  resolveStartupSurface,
} from '../utils/operatorCustomization'
import { getIntradayPresetProfile } from '../utils/intradayPresetModel'
import {
  getAccountProfileDefinition,
  getAccountProfileOptions,
  normalizeAccountProfile,
} from '../utils/accountProfileModel'
import ActionBar from './ActionBar'
import Button from './Button'
import Chip from './Chip'
import TickerInput from './TickerInput'

const QUICK_OPEN_INPUT_ID = 'shell-quick-open-input'

function getShellNavItems(personalMode, activeAccountProfile) {
  const normalizedAccountProfile = normalizeAccountProfile(activeAccountProfile)
  const settingsItem =
    normalizedAccountProfile === 'brokerage'
      ? { to: '/settings', label: 'Brokerage', kicker: 'Acct' }
      : { to: '/settings', label: personalMode ? 'Desk setup' : 'Account', kicker: personalMode ? 'Desk' : 'Acct' }
  if (personalMode) {
    return [
      { to: '/', label: 'Desk', kicker: 'Live' },
      { to: '/watchlist', label: 'Watchlist', kicker: 'Scan' },
      { to: '/compare', label: 'Compare', kicker: 'Rank' },
      { to: '/trades', label: 'Trades', kicker: 'Route' },
      { to: '/portfolio', label: 'Portfolio', kicker: 'Risk' },
      { to: '/strategy-desks', label: 'Desks', kicker: 'Quant' },
      { to: '/strategy-desks/systematic-equities', label: 'Systematic', kicker: 'Desk' },
      { to: '/journal', label: 'Journal', kicker: 'Review' },
      { to: '/alerts', label: 'Alerts', kicker: 'Watch' },
      { to: '/notes', label: 'Notes', kicker: 'Plan' },
      { to: '/education', label: 'Playbook', kicker: 'Run' },
      settingsItem,
    ]
  }

  return [
    { to: '/', label: 'Operations', kicker: 'Ops' },
    { to: '/watchlist', label: 'Market watch', kicker: 'Scan' },
    { to: '/compare', label: 'Analysis', kicker: 'Rank' },
    { to: '/trades', label: 'Execution ops', kicker: 'Route' },
    { to: '/portfolio', label: 'Exposure', kicker: 'Risk' },
    { to: '/strategy-desks', label: 'Strategy desks', kicker: 'Quant' },
    { to: '/strategy-desks/systematic-equities', label: 'Systematic', kicker: 'Desk' },
    { to: '/journal', label: 'Audit log', kicker: 'Review' },
    { to: '/alerts', label: 'Alerts', kicker: 'Watch' },
    { to: '/notes', label: 'Runbook', kicker: 'Plan' },
    { to: '/education', label: 'Operator guide', kicker: 'Guide' },
    settingsItem,
    { to: '/workspaces', label: 'Organizations', kicker: 'Org' },
  ]
}

function getShellNavShortcuts(personalMode, activeAccountProfile) {
  return getShellNavItems(personalMode, activeAccountProfile).map((item) => {
    const keyMap = {
      '/': 'D',
      '/watchlist': 'W',
      '/compare': 'C',
      '/trades': 'T',
      '/portfolio': 'P',
      '/strategy-desks': 'Q',
      '/strategy-desks/systematic-equities': 'Y',
      '/journal': 'J',
      '/alerts': 'A',
      '/notes': 'N',
      '/education': 'G',
      '/settings': 'S',
      '/workspaces': 'O',
    }
    return {
      to: item.to,
      label: item.label,
      keys: ['Alt', 'Shift', keyMap[item.to] || 'D'],
    }
  })
}

function getShellShortcutGroups(personalMode, shellNavShortcuts) {
  return [
    {
      title: 'Global',
      items: [
        { label: 'Focus quick open', keys: ['Ctrl/Cmd', 'K'] },
        { label: 'Open shortcut help', keys: ['Shift', '?'] },
        { label: 'Close shortcut help or blur quick open', keys: ['Esc'] },
      ],
    },
    {
      title: 'Page jumps',
      items: shellNavShortcuts,
    },
    {
      title: 'Page actions',
      items: [
        { label: 'Focus page input or filter', keys: ['/'], detail: 'Works on compare, alerts, journal, and notes. When a page has a primary input, this takes you there first.' },
        { label: 'Jump to the main result or action', keys: ['Shift', 'J'], detail: 'Moves focus into the top queue, replay row, or saved-note action on the current page.' },
      ],
    },
    {
      title: personalMode ? 'Desk only' : 'Operations only',
      items: [
        { label: 'Toggle plan, position, and radar drawers', keys: ['1 / 2 / 3'] },
        { label: 'Toggle tape', keys: ['T'] },
        { label: 'Reset overlays or close panels', keys: ['Esc'] },
        { label: 'Switch chart tools', keys: ['V', 'X', 'H', 'L', 'G', 'R', 'N', 'M'] },
        { label: 'Undo or redo guide changes', keys: ['Ctrl/Cmd', 'Z / Shift+Z / Y'] },
      ],
    },
  ]
}

function joinClasses(...values) {
  return values.filter(Boolean).join(' ')
}

function isNavActive(pathname, to) {
  if (to === '/') return pathname === '/'
  return pathname === to || pathname.startsWith(`${to}/`)
}

function resolveActiveNavItem(pathname, items) {
  const matches = items.filter((item) => isNavActive(pathname, item.to))
  if (!matches.length) return items[0]
  return [...matches].sort((left, right) => right.to.length - left.to.length)[0]
}

function isEditableTarget(target) {
  if (!(target instanceof HTMLElement)) return false
  if (target.isContentEditable) return true
  return ['INPUT', 'TEXTAREA', 'SELECT'].includes(target.tagName)
}

export default function AppShell({ appName, appTagline, children }) {
  const { preferences, setPreference } = usePreferences()
  const { session } = useAuth()
  const location = useLocation()
  const navigate = useNavigate()
  const personalMode = appConfig.personalMode
  const isAuthenticated = Boolean(session?.authenticated)
  const [quickTicker, setQuickTicker] = useState('')
  const [shortcutsOpen, setShortcutsOpen] = useState(false)
  const lastFocusedElementRef = useRef(null)
  const brandName = personalMode
    ? appName
    : session?.active_tenant?.brand_settings?.app_name || appName
  const brandTagline = personalMode
    ? appTagline
    : session?.active_tenant?.brand_settings?.app_tagline || appTagline
  const activeAccountProfile = normalizeAccountProfile(preferences?.activeAccountProfile)
  const activeAccountProfileDefinition = getAccountProfileDefinition(activeAccountProfile)
  const accountProfileOptions = useMemo(() => getAccountProfileOptions(), [])
  const shellNavItems = useMemo(
    () => getShellNavItems(personalMode, activeAccountProfile),
    [activeAccountProfile, personalMode],
  )
  const shellNavShortcuts = useMemo(
    () => getShellNavShortcuts(personalMode, activeAccountProfile),
    [activeAccountProfile, personalMode],
  )
  const shellShortcutGroups = useMemo(
    () => getShellShortcutGroups(personalMode, shellNavShortcuts),
    [personalMode, shellNavShortcuts],
  )
  const currentPage = resolveActiveNavItem(location.pathname, shellNavItems)
  const tradingStyle = normalizeTradingStyle(preferences?.tradingStyle, 'intraday')
  const tradingStyleProfile = getTradingStyleProfile(tradingStyle)
  const intradayPresetProfile = getIntradayPresetProfile(preferences?.intradayPreset)
  const startupSurface = resolveStartupSurface(tradingStyle, preferences?.startupSurface)
  const reviewSurface = resolveReviewSurface(tradingStyle, preferences?.defaultReviewSurface)
  const startupLabel = getSurfaceLabel(startupSurface)
  const reviewLabel = getSurfaceLabel(reviewSurface)
  const navShortcutMap = useMemo(
    () => new Map(shellNavShortcuts.map((item) => [String(item.keys[item.keys.length - 1] || '').toLowerCase(), item.to])),
    [shellNavShortcuts],
  )
  const brandMark = brandName
    .split(/\s+/)
    .filter(Boolean)
    .slice(0, 2)
    .map((part) => part[0]?.toUpperCase() || '')
    .join('')

  function navigateShell(to) {
    const currentParams = new URLSearchParams(location.search)
    const nextParams = new URLSearchParams()
    if (!personalMode) {
      const tenant = currentParams.get('tenant')
      if (tenant) nextParams.set('tenant', tenant)
    }
    navigate({
      pathname: to,
      search: nextParams.toString() ? `?${nextParams.toString()}` : '',
    })
  }

  function handleAccountProfileChange(event) {
    const nextProfile = normalizeAccountProfile(event.target.value)
    setPreference('activeAccountProfile', nextProfile)
    if (nextProfile === 'brokerage' || location.pathname === '/settings') {
      navigateShell('/settings')
    }
  }

  function getStoredWorkflowSurface() {
    if (typeof window === 'undefined') return ''
    const stored = String(window.localStorage.getItem('sos-last-workflow-surface') || '').trim()
    return isWorkflowSurfacePath(stored) ? stored : ''
  }

  function resolveHomeSurface() {
    if (preferences?.rememberLastWorkflowSurface) {
      const stored = getStoredWorkflowSurface()
      if (stored) return stored
    }
    return startupSurface
  }

  function focusQuickOpen() {
    const input = document.getElementById(QUICK_OPEN_INPUT_ID)
    if (input && typeof input.focus === 'function') {
      input.focus()
      if (typeof input.select === 'function') input.select()
    }
  }

  function openShortcuts() {
    lastFocusedElementRef.current = document.activeElement instanceof HTMLElement ? document.activeElement : null
    setShortcutsOpen(true)
  }

  function closeShortcuts() {
    setShortcutsOpen(false)
    window.requestAnimationFrame(() => {
      if (lastFocusedElementRef.current && typeof lastFocusedElementRef.current.focus === 'function') {
        lastFocusedElementRef.current.focus()
      }
    })
  }

  async function handleQuickOpen(event) {
    event.preventDefault()
    if (!isAuthenticated) return
    const normalizedTicker = String(quickTicker || '').trim().toUpperCase()
    if (!normalizedTicker || normalizedTicker.length > 8) return
    const params = new URLSearchParams(location.search)
    const nextParams = new URLSearchParams()
    if (!appConfig.personalMode) {
      const tenant = params.get('tenant')
      if (tenant) nextParams.set('tenant', tenant)
    }
    nextParams.set('ticker', normalizedTicker)
    navigate({
      pathname: '/',
      search: nextParams.toString() ? `?${nextParams.toString()}` : '',
    })
    try {
      await recordRecentTicker(normalizedTicker)
    } catch {
      // keep quick-open responsive even if the ticker hub does not persist
    }
  }

  useEffect(() => {
    if (!shortcutsOpen) return undefined
    window.requestAnimationFrame(() => {
      const closeButton = document.getElementById('shell-shortcuts-close')
      if (closeButton && typeof closeButton.focus === 'function') {
        closeButton.focus()
      }
    })
    return undefined
  }, [shortcutsOpen])

  useEffect(() => {
    if (!isAuthenticated) return undefined

    function handleShellKeydown(event) {
      if (event.defaultPrevented) return
      const key = String(event.key || '').toLowerCase()
      const commandModifier = event.metaKey || event.ctrlKey
      const shellNavigationModifier = event.altKey && event.shiftKey
      const editableTarget = isEditableTarget(event.target)

      if (commandModifier && key === 'k') {
        event.preventDefault()
        if (shortcutsOpen) setShortcutsOpen(false)
        focusQuickOpen()
        return
      }

      if (!commandModifier && !event.altKey && (event.key === '?' || (event.shiftKey && event.key === '/'))) {
        if (editableTarget) return
        event.preventDefault()
        if (shortcutsOpen) {
          closeShortcuts()
        } else {
          openShortcuts()
        }
        return
      }

      if (event.key === 'Escape') {
        if (shortcutsOpen) {
          event.preventDefault()
          closeShortcuts()
          return
        }
        const activeElement = document.activeElement
        if (activeElement instanceof HTMLElement && activeElement.id === QUICK_OPEN_INPUT_ID) {
          activeElement.blur()
        }
        return
      }

      if (shellNavigationModifier && navShortcutMap.has(key)) {
        event.preventDefault()
        if (shortcutsOpen) setShortcutsOpen(false)
        navigateShell(navShortcutMap.get(key))
      }
    }

    window.addEventListener('keydown', handleShellKeydown)
    return () => {
      window.removeEventListener('keydown', handleShellKeydown)
    }
  }, [isAuthenticated, location.search, navShortcutMap, navigate, shortcutsOpen])

  return (
    <div className={`app-shell app-shell--chart-mode app-shell--${tradingStyle}-mode ${preferences?.compactTables ? 'app-shell--compact' : ''}`}>
      <a className="ui-skip-link" href="#primary-content">
        Skip to primary content
      </a>
      <div className="ui-shell">
        <header className="ui-panel ui-panel--section ui-shell__masthead" id="desk-masthead">
          <div className="ui-shell__brand-row">
              <div className="ui-shell__brand">
              <div className="ui-shell__brand-mark" aria-hidden="true">{brandMark}</div>
              <div className="ui-shell__brand-copy">
                <div className="ui-kicker">{personalMode ? 'Own-account operator desk' : 'Platform operations'}</div>
                <h1 className="ui-shell__brand-name">{brandName}</h1>
                <p className="ui-shell__brand-tagline">{brandTagline}</p>
              </div>
            </div>
            <div className="ui-shell__status">
              <div className="ui-shell__profile-picker">
                <label className="ui-shell__nav-label" htmlFor="shell-account-profile">
                  Profile
                </label>
                <select
                  id="shell-account-profile"
                  className="ui-input ui-input--select ui-shell__profile-select"
                  value={activeAccountProfile}
                  onChange={handleAccountProfileChange}
                  aria-label="Select account profile"
                >
                  {accountProfileOptions.map((profile) => (
                    <option key={profile.key} value={profile.key}>
                      {profile.label}
                    </option>
                  ))}
                </select>
              </div>
              <Chip tone={isAuthenticated ? 'positive' : 'warning'} size="sm">
                {isAuthenticated ? (personalMode ? 'Desk live' : 'Ops live') : 'Local demo'}
              </Chip>
              <Chip
                tone={
                  activeAccountProfile === 'brokerage'
                    ? 'info'
                    : activeAccountProfile === 'personal_live'
                      ? 'negative'
                      : 'warning'
                }
                size="sm"
              >
                {activeAccountProfileDefinition.badgeLabel}
              </Chip>
              <Chip tone={tradingStyleProfile.tone} size="sm">
                {tradingStyleProfile.shellLabel}
              </Chip>
              {tradingStyle === 'intraday' ? (
                <Chip tone="neutral" size="sm">
                  {intradayPresetProfile.shellLabel}
                </Chip>
              ) : null}
              <Chip tone="neutral" size="sm">
                {preferences?.compactTables ? (personalMode ? 'Dense desk' : 'Dense ops') : (personalMode ? 'Comfort desk' : 'Comfort ops')}
              </Chip>
              <Chip tone="neutral" size="sm">
                {preferences?.rememberLastWorkflowSurface ? `Resume ${getSurfaceLabel(resolveHomeSurface())}` : `Home ${startupLabel}`}
              </Chip>
              <Chip tone="neutral" size="sm">
                {currentPage.label}
              </Chip>
              {!personalMode && session?.active_tenant?.name ? (
                <Chip tone="neutral" size="sm">
                  {session.active_tenant.name}
                </Chip>
              ) : null}
            </div>
          </div>
          {isAuthenticated ? (
            <div className="ui-shell__nav-wrap">
              <div className="ui-shell__topline">
                <form className="ui-shell__quick-open" onSubmit={handleQuickOpen}>
                  <div className="ui-shell__quick-open-field">
                    <label className="ui-shell__nav-label" htmlFor="shell-quick-open-input">
                      Quick open
                    </label>
                    <TickerInput
                      id="shell-ticker-suggestions"
                      inputId={QUICK_OPEN_INPUT_ID}
                      value={quickTicker}
                      onChange={setQuickTicker}
                      placeholder={personalMode ? 'Load ticker into desk' : 'Load ticker into operations'}
                      ariaLabel="Quick open ticker"
                    />
                  </div>
                  <ActionBar compact className="ui-shell__quick-actions">
                    <Button type="submit" variant="solid" size="sm" disabled={!String(quickTicker || '').trim()}>
                      Open ticker
                    </Button>
                    <Button type="button" variant="ghost" size="sm" onClick={() => navigateShell(resolveHomeSurface())}>
                      {preferences?.rememberLastWorkflowSurface ? 'Resume flow' : `Open ${startupLabel.toLowerCase()}`}
                    </Button>
                    <Button type="button" variant="subtle" size="sm" onClick={() => navigateShell(reviewSurface)}>
                      {`Review ${reviewLabel.toLowerCase()}`}
                    </Button>
                    <Button
                      type="button"
                      variant="ghost"
                      size="sm"
                      onClick={() => {
                        if (shortcutsOpen) {
                          closeShortcuts()
                        } else {
                          openShortcuts()
                        }
                      }}
                      aria-haspopup="dialog"
                      aria-expanded={shortcutsOpen ? 'true' : 'false'}
                    >
                      Shortcuts ?
                    </Button>
                  </ActionBar>
                </form>
              </div>
              <div className="ui-shell__nav-label">Navigation</div>
              <nav className="ui-shell__nav" aria-label={personalMode ? 'Desk navigation' : 'Platform operations navigation'} aria-describedby="desk-masthead">
                {shellNavItems.map((item) => (
                  <NavLink
                    key={item.to}
                    to={item.to}
                    title={
                      shellNavShortcuts.find((shortcut) => shortcut.to === item.to)
                        ? `${item.label} (${shellNavShortcuts.find((shortcut) => shortcut.to === item.to).keys.join('+')})`
                        : item.label
                    }
                    className={joinClasses(
                      'ui-shell__nav-link',
                      currentPage?.to === item.to && 'ui-shell__nav-link--active',
                    )}
                  >
                    <span className="ui-shell__nav-kicker">{item.kicker}</span>
                    <span className="ui-shell__nav-text">{item.label}</span>
                  </NavLink>
                ))}
              </nav>
            </div>
          ) : null}
        </header>
        {isAuthenticated && shortcutsOpen ? (
          <div className="ui-shell-shortcuts" role="presentation">
            <button type="button" className="ui-shell-shortcuts__backdrop" aria-label="Close keyboard shortcuts" onClick={closeShortcuts} />
            <div
              className="ui-panel ui-panel--section ui-shell-shortcuts__dialog"
              role="dialog"
              aria-modal="true"
              aria-labelledby="shell-shortcuts-title"
            >
              <div className="ui-shell-shortcuts__header">
                <div>
                  <div className="ui-kicker">Keyboard shortcuts</div>
                  <h2 className="ui-shell-shortcuts__title" id="shell-shortcuts-title">Move faster without leaving the workstation flow.</h2>
                  <p className="ui-shell-shortcuts__subtitle">
                    Shell shortcuts handle navigation and quick open. {personalMode ? 'Desk-only shortcuts' : 'Operations-only shortcuts'} stay local to the active surface once you are inside the control loop.
                  </p>
                </div>
                <Button id="shell-shortcuts-close" type="button" variant="ghost" size="sm" onClick={closeShortcuts}>
                  Close
                </Button>
              </div>
              <div className="ui-shell-shortcuts__grid">
                {shellShortcutGroups.map((group) => (
                  <section key={group.title} className="ui-shell-shortcuts__section">
                    <h3 className="ui-shell-shortcuts__section-title">{group.title}</h3>
                    <div className="ui-shell-shortcuts__list">
                      {group.items.map((item) => (
                        <div key={`${group.title}-${item.label}`} className="ui-shell-shortcuts__row">
                          <span className="ui-shell-shortcuts__label">{item.label}</span>
                          <span className="ui-shell-shortcuts__keys" aria-label={`${item.label} shortcut`}>
                            {item.keys.map((part) => (
                              <kbd key={`${item.label}-${part}`} className="ui-kbd">{part}</kbd>
                            ))}
                          </span>
                        </div>
                      ))}
                    </div>
                  </section>
                ))}
              </div>
              <div className="ui-shell-shortcuts__footnote">
                Start with <kbd className="ui-kbd">Ctrl/Cmd</kbd><kbd className="ui-kbd">K</kbd> to load a ticker quickly, or use
                <kbd className="ui-kbd">Alt</kbd><kbd className="ui-kbd">Shift</kbd><kbd className="ui-kbd">W</kbd> and
                <kbd className="ui-kbd">Alt</kbd><kbd className="ui-kbd">Shift</kbd><kbd className="ui-kbd">C</kbd> to move between market watch and analysis without touching the mouse.
              </div>
            </div>
          </div>
        ) : null}
        <main className="ui-shell__body" id="primary-content" tabIndex={-1} aria-label="Primary content">
          <div
            className="ui-shell__page"
            data-app-name={brandName}
            data-app-tagline={brandTagline}
            data-tenant-name={personalMode ? '' : session?.active_tenant?.name || ''}
          >
            {children}
          </div>
        </main>
      </div>
    </div>
  )
}
