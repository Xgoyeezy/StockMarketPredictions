import { useEffect, useMemo, useState } from 'react'
import { matchPath, useLocation, useNavigate } from 'react-router-dom'
import { getAuthEntry } from '../api/client'
import Button from './Button'
import Chip from './Chip'
import { TextField } from './FormFields'
import Kicker from './Kicker'
import { useAuth } from '../context/useAuth'
import { appConfig } from '../config/appConfig'

const DEFAULT_FORM = {
  email: '',
  name: '',
  loginSecret: '',
  organizationName: '',
}

function normalizeOrganizationSlug(value) {
  return String(value || '').trim().toLowerCase()
}

function extractOrganizationSlugFromPath(pathname) {
  const loginMatch = matchPath('/login/:tenantSlug', pathname)
  return normalizeOrganizationSlug(loginMatch?.params?.tenantSlug)
}

function buildCleanSearch(search) {
  const params = new URLSearchParams(search || '')
  params.delete('auth_error')
  params.delete('auth_error_description')
  params.delete('invite')
  params.delete('invite_token')
  return params.toString()
}

function formatEntryModeLabel(entryMode, personalMode) {
  if (entryMode === 'tenant-sso') return 'SSO-ready'
  if (!entryMode) return personalMode ? 'desk' : 'organization'
  return personalMode ? entryMode.replace(/^tenant/i, 'desk') : entryMode.replace(/^tenant/i, 'organization')
}

export default function AuthScreen() {
  const { authConfig, busy, error, beginProviderLogin, signIn } = useAuth()
  const personalMode = appConfig.personalMode
  const location = useLocation()
  const navigate = useNavigate()
  const [form, setForm] = useState(DEFAULT_FORM)
  const [localError, setLocalError] = useState('')
  const [entryContext, setEntryContext] = useState(null)
  const [entryLoading, setEntryLoading] = useState(false)
  const provider = authConfig?.provider || 'customer-access'
  const usesExternalRedirect = (
    (provider === 'auth0' && authConfig?.auth0?.ready)
    || (provider === 'oidc' && authConfig?.oidc?.ready)
  )

  const searchParams = useMemo(() => new URLSearchParams(location.search), [location.search])
  const queryInviteToken = useMemo(() => String(searchParams.get('invite') || searchParams.get('invite_token') || '').trim(), [searchParams])
  const queryOrganizationSlug = useMemo(() => normalizeOrganizationSlug(searchParams.get('tenant') || searchParams.get('tenant_slug')), [searchParams])
  const routeOrganizationSlug = useMemo(() => extractOrganizationSlugFromPath(location.pathname), [location.pathname])
  const organizationSlug = queryOrganizationSlug || routeOrganizationSlug
  const normalizedFormEmail = useMemo(() => String(form.email || '').trim().toLowerCase(), [form.email])
  const cleanSearch = useMemo(() => buildCleanSearch(location.search), [location.search])
  const authRedirectPath = useMemo(() => {
    if (location.pathname.startsWith('/login')) {
      return ''
    }
    return `${location.pathname || '/'}${cleanSearch ? `?${cleanSearch}` : ''}`
  }, [cleanSearch, location.pathname])

  useEffect(() => {
    const authError = searchParams.get('auth_error')
    const authErrorDescription = searchParams.get('auth_error_description')
    if (authError) {
      setLocalError(authErrorDescription || authError.replace(/_/g, ' '))
    }
  }, [searchParams])

  useEffect(() => {
    let cancelled = false
    const timer = window.setTimeout(() => {
      setEntryLoading(true)
      getAuthEntry({
        organizationSlug,
        inviteToken: queryInviteToken,
        redirectPath: authRedirectPath,
        email: normalizedFormEmail || undefined,
      })
        .then((data) => {
          if (cancelled) return
          setEntryContext(data)
        })
        .catch((err) => {
          if (cancelled) return
          setLocalError((current) => current || err?.response?.data?.detail || err.message || (personalMode ? 'Unable to load desk sign-in routing.' : 'Unable to load organization access routing.'))
        })
        .finally(() => {
          if (!cancelled) {
            setEntryLoading(false)
          }
        })
    }, normalizedFormEmail ? 220 : 0)
    return () => {
      cancelled = true
      window.clearTimeout(timer)
    }
  }, [authRedirectPath, normalizedFormEmail, queryInviteToken, organizationSlug])

  const providerSelection = entryContext?.provider_selection || {}
  const localAvailable = providerSelection.local_login_available ?? !usesExternalRedirect
  const externalAvailable = providerSelection.external_login_available ?? usesExternalRedirect
  const blockLocalLogin = Boolean(providerSelection.block_local_login)
  const recommendedProvider = providerSelection.recommended_provider || (usesExternalRedirect ? 'auth0' : 'local-session')
  const redirectProviders = useMemo(
    () => (providerSelection.providers || []).filter((providerOption) => providerOption.mode === 'redirect'),
    [providerSelection.providers],
  )
  const recommendedRedirectProvider = useMemo(
    () => redirectProviders.find((providerOption) => providerOption.recommended) || redirectProviders.find((providerOption) => providerOption.key === recommendedProvider) || redirectProviders[0] || null,
    [recommendedProvider, redirectProviders],
  )

  const subtitle = useMemo(() => {
    if (blockLocalLogin && entryContext?.tenant?.name) {
      return personalMode
        ? `Local sign-in is disabled for ${entryContext.tenant.name}. Continue with the configured identity provider to open this own-account trading desk.`
        : `Local sign-in is disabled for ${entryContext.tenant.name}. Continue with the configured organization identity provider to open the trading desk.`
    }
    if (providerSelection.domain_match && entryContext?.tenant?.name) {
      return personalMode
        ? `We detected the ${providerSelection.email_domain} sign-in domain for ${entryContext.tenant.name}. SSO is recommended for this operator desk, but local fallback is still available if that profile allows it.`
        : `We detected the ${providerSelection.email_domain} organization domain for ${entryContext.tenant.name}. SSO is recommended for the trading desk, but local fallback is still available if your organization allows it.`
    }
    if (entryContext?.routing?.requires_sso && entryContext?.tenant?.name) {
      const emailDomain = entryContext?.routing?.email_domain_hint ? ` Use your ${entryContext.routing.email_domain_hint} identity.` : ''
      return personalMode
        ? `Continue to ${entryContext.tenant.name} using the configured sign-in flow for this own-account operator desk.${emailDomain}`
        : `Continue to ${entryContext.tenant.name} using the configured organization identity flow for the trading desk.${emailDomain}`
    }
    if (usesExternalRedirect) {
      return personalMode
        ? 'Continue with your sign-in provider. We will route you back into the active own-account desk after the callback completes.'
        : 'Continue with your organization identity provider. We will redeem organization invites and route you back into the trading desk after the callback completes.'
    }
    if (authConfig?.supports_signup) {
      return personalMode
        ? 'Sign in with your email. If an invite already exists, it will be claimed automatically. If not, we will create your first own-account desk profile during setup.'
        : 'Sign in with your work email. If an invite already exists, it will be claimed automatically. If not, we will create your first organization during onboarding.'
    }
    return personalMode
      ? 'Sign in with the invited email for this own-account desk. New desk creation is currently managed by an admin.'
      : 'Sign in with the invited email for your organization. New organization creation is currently managed by an admin.'
  }, [authConfig, blockLocalLogin, entryContext, personalMode, providerSelection.domain_match, providerSelection.email_domain, usesExternalRedirect])

  const providerCta = useMemo(() => {
    if ((blockLocalLogin || redirectProviders.some((providerOption) => providerOption.key === recommendedProvider)) && entryContext?.tenant?.name) {
      return `Continue to ${entryContext.tenant.name} SSO`
    }
    return `Continue with ${recommendedRedirectProvider?.label || authConfig?.provider_label || 'provider'}`
  }, [authConfig, blockLocalLogin, entryContext, recommendedProvider, recommendedRedirectProvider, redirectProviders])

  const postLoginPath = useMemo(() => {
    if (personalMode) {
      return entryContext?.routing?.post_login_path || '/'
    }
    return entryContext?.routing?.post_login_path || (organizationSlug ? `/?tenant=${organizationSlug}` : '/')
  }, [entryContext, personalMode, organizationSlug])
  const entryModeLabel = useMemo(
    () => formatEntryModeLabel(entryContext?.entry_mode, personalMode),
    [entryContext?.entry_mode, personalMode],
  )

  async function handleSubmit(event) {
    event.preventDefault()
    if (blockLocalLogin) {
      setLocalError(personalMode ? 'This desk requires SSO. Use the SSO button instead.' : 'This organization requires SSO. Use the SSO button instead.')
      return
    }
    if (!form.email.trim() || !form.name.trim()) {
      setLocalError('Email and display name are required.')
      return
    }
    try {
      setLocalError('')
      await signIn({
        email: form.email,
        name: form.name,
        login_secret: form.loginSecret || undefined,
        tenant_slug: organizationSlug || undefined,
        invite_token: queryInviteToken || undefined,
        organization_name: form.organizationName || undefined,
        create_organization_if_missing: Boolean(authConfig?.supports_signup),
      })
      navigate(postLoginPath, { replace: true })
    } catch (err) {
      setLocalError(err?.response?.data?.detail || err.message || 'Unable to sign in.')
    }
  }

  async function handleExternalSignIn(providerOption) {
    try {
      setLocalError('')
      await beginProviderLogin({
        provider: providerOption?.key || recommendedRedirectProvider?.key || recommendedProvider,
        providerRecordId: providerOption?.provider_record_id || recommendedRedirectProvider?.provider_record_id || undefined,
        organizationSlug: organizationSlug || undefined,
        inviteToken: queryInviteToken || undefined,
        redirectPath: entryContext?.routing?.redirect_path || postLoginPath,
        email: normalizedFormEmail || undefined,
      })
    } catch (err) {
      setLocalError(err?.response?.data?.detail || err.message || 'Unable to start provider login.')
    }
  }

  return (
    <div className="auth-screen">
      <div className="auth-screen__panel">
        <Kicker as="div" className="auth-screen__kicker">{personalMode ? 'Own-account desk access' : 'Trading workspace access'}</Kicker>
        <h1>{entryContext?.tenant?.name ? `Open ${entryContext.tenant.name}` : personalMode ? 'Sign in to your own-account trading desk' : 'Sign in to the trading desk'}</h1>
        <p>{subtitle}</p>

        {entryContext?.tenant || queryInviteToken ? (
          <div className="auth-screen__tenant-card">
            <div className="auth-screen__tenant-heading">
              <strong>{entryContext?.tenant?.name || organizationSlug || (personalMode ? 'Desk access' : 'Organization access')}</strong>
              <span>{entryModeLabel}</span>
            </div>
            <div className="auth-screen__tenant-grid">
              {entryContext?.tenant?.slug ? <span className="auth-screen__pill">{personalMode ? 'Desk' : 'Org'}: {entryContext.tenant.slug}</span> : null}
              {queryInviteToken ? <span className="auth-screen__pill">Invite flow</span> : null}
              {entryContext?.invite?.email_masked ? <span className="auth-screen__pill">Invitee: {entryContext.invite.email_masked}</span> : null}
              {entryContext?.routing?.email_domain_hint ? <span className="auth-screen__pill">Domain: {entryContext.routing.email_domain_hint}</span> : null}
              {entryContext?.routing?.connection_hint ? <span className="auth-screen__pill">Connection: {entryContext.routing.connection_hint}</span> : null}
              {entryContext?.routing?.organization_hint ? <span className="auth-screen__pill">{personalMode ? 'Desk hint' : 'Org hint'}: {entryContext.routing.organization_hint}</span> : null}
            </div>
          </div>
        ) : null}

        {externalAvailable && (!localAvailable || blockLocalLogin) ? (
          <div className="auth-screen__form">
            {(localError || error) ? <div className="auth-screen__error">{localError || error}</div> : null}
            <div className="auth-screen__provider-actions">
              {redirectProviders.map((providerOption) => (
                <Button
                  key={`${providerOption.key}-${providerOption.provider_record_id || 'default'}`}
                  type="button"
                  variant="solid"
                  onClick={() => handleExternalSignIn(providerOption)}
                  disabled={busy || entryLoading}
                >
                  {busy && `${providerOption.key}-${providerOption.provider_record_id || 'default'}` === `${recommendedRedirectProvider?.key || recommendedProvider}-${recommendedRedirectProvider?.provider_record_id || 'default'}`
                    ? 'Redirecting...'
                    : `Continue with ${providerOption.label}`}
                </Button>
              ))}
              {!redirectProviders.length ? (
                <Button type="button" variant="solid" onClick={() => handleExternalSignIn()} disabled={busy || entryLoading}>
                  {busy ? 'Redirecting...' : providerCta}
                </Button>
              ) : null}
            </div>
          </div>
        ) : (
          <form className="auth-screen__form" onSubmit={handleSubmit}>
            <TextField
              label={personalMode ? 'Sign-in email' : 'Work email'}
              type="email"
              value={form.email}
              onChange={(event) => setForm((current) => ({ ...current, email: event.target.value }))}
              placeholder={personalMode ? 'you@example.com' : 'name@company.com'}
              autoComplete="email"
              disabled={busy}
            />

            <TextField
              label="Display name"
              type="text"
              value={form.name}
              onChange={(event) => setForm((current) => ({ ...current, name: event.target.value }))}
              placeholder="Alex Trader"
              autoComplete="name"
              disabled={busy}
            />

            {authConfig?.local_session?.login_secret_required ? (
              <TextField
                label="Login secret"
                type="password"
                value={form.loginSecret}
                onChange={(event) => setForm((current) => ({ ...current, loginSecret: event.target.value }))}
                placeholder="Provided by the operator"
                autoComplete="current-password"
                disabled={busy}
              />
            ) : null}

            <TextField
              label={personalMode ? 'Desk label' : 'Organization name'}
              type="text"
              value={form.organizationName}
              onChange={(event) => setForm((current) => ({ ...current, organizationName: event.target.value }))}
              placeholder={personalMode ? 'Only used if we need to create your first own-account desk' : 'Only used when we need to create your first org'}
              disabled={busy || !authConfig?.supports_signup}
            />

            {(localError || error) ? <div className="auth-screen__error">{localError || error}</div> : null}

            {entryContext?.provider_selection?.providers?.length ? (
              <div className="auth-screen__provider-options">
                {entryContext.provider_selection.providers.map((providerOption) => (
                  <Chip
                    key={`${providerOption.key}-${providerOption.provider_record_id || 'default'}`}
                    tone={providerOption.recommended ? 'info' : 'neutral'}
                    size="sm"
                    className={`auth-screen__provider-pill ${providerOption.recommended ? 'is-recommended' : ''}`}
                  >
                    {providerOption.label}
                    {providerOption.recommended ? ' recommended' : ''}
                  </Chip>
                ))}
              </div>
            ) : null}

            <Button type="submit" variant="solid" disabled={busy || entryLoading}>
              {busy ? 'Signing in...' : personalMode ? 'Open own-account desk' : 'Open trading desk'}
            </Button>

            {externalAvailable ? (
              <div className="auth-screen__provider-actions">
                {redirectProviders.map((providerOption) => (
                  <Button
                    key={`${providerOption.key}-${providerOption.provider_record_id || 'default'}`}
                    type="button"
                    variant="ghost"
                    onClick={() => handleExternalSignIn(providerOption)}
                    disabled={busy || entryLoading}
                  >
                    {busy && `${providerOption.key}-${providerOption.provider_record_id || 'default'}` === `${recommendedRedirectProvider?.key || recommendedProvider}-${recommendedRedirectProvider?.provider_record_id || 'default'}`
                      ? 'Redirecting...'
                      : `Continue with ${providerOption.label}`}
                  </Button>
                ))}
                {!redirectProviders.length ? (
                  <Button type="button" variant="ghost" onClick={() => handleExternalSignIn()} disabled={busy || entryLoading}>
                    {busy ? 'Redirecting...' : providerCta}
                  </Button>
                ) : null}
              </div>
            ) : null}
          </form>
        )}

        <div className="auth-screen__meta">
          {!appConfig.customerReadyMode ? (
            <>
              <Chip tone="neutral" size="sm">Provider: {authConfig?.provider_label || authConfig?.provider || 'Auth'}</Chip>
              <Chip tone="neutral" size="sm">Environment: {authConfig?.environment || 'development'}</Chip>
            </>
          ) : null}
          <Chip tone="neutral" size="sm">{authConfig?.supports_signup ? (personalMode ? 'Self-serve desk setup enabled' : 'Self-serve onboarding enabled') : 'Invite-only access'}</Chip>
          {entryContext?.routing?.entry_path ? <Chip tone="neutral" size="sm">Entry: {entryContext.routing.entry_path}</Chip> : null}
        </div>
      </div>
    </div>
  )
}
