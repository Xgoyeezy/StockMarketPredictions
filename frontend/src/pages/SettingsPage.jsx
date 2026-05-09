import { useEffect, useState } from 'react'
import {
  activateOrganization,
  createOrganizationWebhook,
  createOrganizationApiToken,
  clearRecentTickers,
  createBillingCheckoutSession,
  createOrganization,
  getBillingPlans,
  getBillingSummary,
  getOrganizationAnalytics,
  getOrganizationTradeAutomation,
  getOrganizationTradeAutomationAlpacaPaperReadiness,
  getOrganizationTradeAutomationMarketSession,
  getOrganizationTradeAutomationProductionTrust,
  getOrganizationTradeAutomationSafetyState,
  getOrganizationDelivery,
  getOrganizationFeatureFlags,
  getOrganizationOnboarding,
  getOrganizationOnboardingTemplates,
  getOrganizationApiTokens,
  getOrganizationApiUsage,
  getOrganizationSecurity,
  inviteOrganizationMember,
  getOrganizationSupportSnapshot,
  getOrganizations,
  getOrganizationWebhooks,
  openBillingPortal,
  applyOrganizationOnboardingTemplate,
  runBillingRecovery,
  removeOrganizationMember,
  revokeOrganizationApiToken,
  runOrganizationInvitationAction,
  runOrganizationDeliveryAction,
  runOrganizationWebhookAction,
  seedOrganizationWorkspace,
  updateOrganizationMember,
  updateOrganizationOnboardingStep,
  updateOrganizationBranding,
  updateOrganizationDelivery,
  updateOrganizationFeatureFlag,
  updateOrganizationStatus,
} from '../api/client'
import { useToast } from '../context/ToastContext'
import { usePreferences } from '../context/PreferencesContext'
import { useAuth } from '../context/useAuth'
import { appConfig } from '../config/appConfig'
import ActionBar from '../components/ActionBar'
import Button from '../components/Button'
import EmptyState from '../components/EmptyState'
import ErrorState from '../components/ErrorState'
import FeedbackState from '../components/FeedbackState'
import { SelectField, TextAreaField, TextField, ToggleField } from '../components/FormFields'
import { formatInlineMeta } from '../components/InlineMeta'
import Kicker from '../components/Kicker'
import LinkedBrokerageAccountsSection from '../components/LinkedBrokerageAccountsSection'
import ExecutionProviderDiagnosticsSection from '../components/ExecutionProviderDiagnosticsSection'
import PageIntro from '../components/PageIntro'
import PricingComparisonTable from '../components/pricing/PricingComparisonTable'
import PricingTierCard from '../components/pricing/PricingTierCard'
import SectionCard from '../components/SectionCard'
import MetricCard from '../components/MetricCard'
import StatusBadge from '../components/StatusBadge'
import TickerHub from '../components/TickerHub'
import TradeAutomationSection from '../components/TradeAutomationSection'
import {
  buildTradingStylePreset,
  buildSurfaceSummary,
  getTradingStyleLabel,
  REVIEW_SURFACE_OPTIONS,
  STARTUP_SURFACE_OPTIONS,
  TRADING_STYLE_OPTIONS,
} from '../utils/operatorCustomization'
import {
  buildIntradayPresetGuide,
  DEFAULT_INTRADAY_PRESET,
  getIntradayPresetProfile,
  INTRADAY_PRESET_OPTIONS,
  normalizeIntradayPreset,
} from '../utils/intradayPresetModel'
import {
  buildIntradayModelSummary,
  buildIntervalModel,
  formatMinuteWindow,
  getStyleIntervalOptions,
} from '../utils/intradayModel'
import { getAccountProfileDefinition, normalizeAccountProfile } from '../utils/accountProfileModel'
import { normalizePricingPlans } from '../utils/pricingModel'

const DEFAULT_ORGANIZATION_FORM = {
  name: '',
  planKey: 'starter',
  billingEmail: '',
}

const DEFAULT_BRANDING_FORM = {
  name: '',
  billingEmail: '',
  logoUrl: '',
  appName: '',
  appTagline: '',
  accentPrimary: '#565656',
  accentSecondary: '#2F2F2F',
  backgroundColor: '#000000',
  surfaceColor: '#111111',
  textColor: '#F5F5F5',
  supportEmail: '',
  supportUrl: '',
}

const DEFAULT_DELIVERY_FORM = {
  primaryDomain: '',
  secondaryDomains: '',
  domainStatus: 'draft',
  emailProvider: 'none',
  providerStatus: 'draft',
  templateSetName: '',
  releaseChannel: 'stable',
  senderName: '',
  senderEmail: '',
  replyToEmail: '',
  mailFromSubdomain: '',
  emailSignature: '',
  auth0Organization: '',
  auth0Connection: '',
  ssoEmailDomain: '',
  enabledProviders: ['local-session', 'auth0', 'oidc'],
  authPolicy: 'default',
  preferredProvider: 'default',
  authProviderRecords: [],
}

const DEFAULT_AUTH_PROVIDER_DRAFT = {
  providerId: '',
  providerKey: 'auth0',
  label: '',
  emailDomains: '',
  organizationHint: '',
  connectionHint: '',
  auth0Domain: '',
  issuer: '',
  authorizeUrl: '',
  tokenUrl: '',
  userinfoUrl: '',
  logoutUrl: '',
  clientId: '',
  clientSecret: '',
  audience: '',
  scope: '',
  allowSignup: true,
  enabled: true,
  isDefault: false,
  hasClientSecret: false,
  hasPendingClientSecret: false,
  ready: false,
}

const API_TOKEN_SCOPE_OPTIONS = [
  { key: 'tenant.read', label: 'Organization read' },
  { key: 'market.read', label: 'Market read' },
  { key: 'workspace.write', label: 'Operations write' },
  { key: 'tenant.admin', label: 'Organization admin' },
]

const DEFAULT_API_TOKEN_FORM = {
  name: '',
  expiresInDays: '90',
  scopes: ['tenant.read', 'market.read'],
}

const DEFAULT_WEBHOOK_FORM = {
  name: '',
  url: '',
  events: ['tenant.launch_ready', 'market.signal_ready'],
}

const DEFAULT_MEMBER_INVITE_FORM = {
  email: '',
  name: '',
  role: 'viewer',
  message: '',
}

const EMAIL_PATTERN = /^[^\s@]+@[^\s@]+\.[^\s@]+$/
const DOMAIN_PATTERN = /^(?=.{1,253}$)(?!-)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)(?:\.(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?))+$/i
const HEX_COLOR_PATTERN = /^#[0-9A-F]{6}$/i

function buildBrandingForm(organization) {
  const brand = organization?.brand_settings || {}
  return {
    name: organization?.name || '',
    billingEmail: organization?.billing_email || '',
    logoUrl: organization?.logo_url || '',
    appName: brand.app_name || '',
    appTagline: brand.app_tagline || '',
    accentPrimary: brand.accent_primary || DEFAULT_BRANDING_FORM.accentPrimary,
    accentSecondary: brand.accent_secondary || DEFAULT_BRANDING_FORM.accentSecondary,
    backgroundColor: brand.background_color || DEFAULT_BRANDING_FORM.backgroundColor,
    surfaceColor: brand.surface_color || DEFAULT_BRANDING_FORM.surfaceColor,
    textColor: brand.text_color || DEFAULT_BRANDING_FORM.textColor,
    supportEmail: brand.support_email || '',
    supportUrl: brand.support_url || '',
  }
}

function buildDeliveryForm(snapshot) {
  const delivery = snapshot?.delivery || snapshot?.delivery_settings || {}
  const customDomains = delivery.custom_domains || {}
  const brandedEmail = delivery.branded_email || {}
  const authRouting = delivery.auth_routing || {}
  return {
    primaryDomain: customDomains.primary_domain || '',
    secondaryDomains: (customDomains.secondary_domains || []).join(', '),
    domainStatus: customDomains.domain_status || 'draft',
    emailProvider: brandedEmail.provider_key || 'none',
    providerStatus: brandedEmail.provider_status || 'draft',
    templateSetName: brandedEmail.template_set_name || '',
    releaseChannel: brandedEmail.release_channel || 'stable',
    senderName: brandedEmail.sender_name || '',
    senderEmail: brandedEmail.sender_email || '',
    replyToEmail: brandedEmail.reply_to_email || '',
    mailFromSubdomain: brandedEmail.mail_from_subdomain || '',
    emailSignature: brandedEmail.email_signature || '',
    auth0Organization: authRouting.organization_hint || '',
    auth0Connection: authRouting.connection_hint || '',
    ssoEmailDomain: authRouting.email_domain_hint || '',
    enabledProviders: (authRouting.enabled_providers || []).length
      ? authRouting.enabled_providers
      : DEFAULT_DELIVERY_FORM.enabledProviders,
    authPolicy: authRouting.auth_policy || 'default',
    preferredProvider: authRouting.preferred_provider || 'default',
    authProviderRecords: authRouting.provider_records || [],
  }
}

function formatMoney(amount) {
  if (typeof amount !== 'number') return 'Custom'
  return `$${amount.toLocaleString()}`
}

function formatRemaining(value) {
  if (value === null || value === undefined) return 'Unlimited'
  return String(value)
}

function formatDateTime(value) {
  if (!value) return 'Unknown'
  const parsed = new Date(value)
  if (Number.isNaN(parsed.getTime())) return value
  return parsed.toLocaleString()
}

function omitKeys(record, fields) {
  const next = { ...record }
  fields.forEach((field) => {
    delete next[field]
  })
  return next
}

function isValidEmail(value) {
  const normalized = String(value || '').trim()
  return !normalized || EMAIL_PATTERN.test(normalized)
}

function isValidHttpUrl(value) {
  const normalized = String(value || '').trim()
  if (!normalized) return true
  try {
    const parsed = new URL(normalized)
    return parsed.protocol === 'http:' || parsed.protocol === 'https:'
  } catch {
    return false
  }
}

function isValidDomain(value) {
  const normalized = String(value || '').trim().toLowerCase()
  return !normalized || DOMAIN_PATTERN.test(normalized)
}

function isValidHexColor(value) {
  const normalized = String(value || '').trim().toUpperCase()
  return HEX_COLOR_PATTERN.test(normalized)
}

function buildOrganizationFormErrors(form) {
  const errors = {}
  if (!String(form.name || '').trim()) {
    errors.name = 'Enter the organization name shown in the control plane and shell.'
  }
  if (form.billingEmail && !isValidEmail(form.billingEmail)) {
    errors.billingEmail = 'Enter a valid billing email or leave it blank.'
  }
  return errors
}

function buildBrandingFormErrors(form) {
  const errors = {}
  if (!String(form.name || '').trim()) {
    errors.name = 'Enter the organization display name used across the branded shell.'
  }
  if (form.billingEmail && !isValidEmail(form.billingEmail)) {
    errors.billingEmail = 'Enter a valid billing email or leave it blank.'
  }
  if (form.logoUrl && !isValidHttpUrl(form.logoUrl)) {
    errors.logoUrl = 'Enter a full logo URL starting with http:// or https://.'
  }
  if (form.supportEmail && !isValidEmail(form.supportEmail)) {
    errors.supportEmail = 'Enter a valid support email or leave it blank.'
  }
  if (form.supportUrl && !isValidHttpUrl(form.supportUrl)) {
    errors.supportUrl = 'Enter a full support URL starting with http:// or https://.'
  }
  ;[
    ['accentPrimary', 'Primary accent'],
    ['accentSecondary', 'Secondary accent'],
    ['backgroundColor', 'Background'],
    ['surfaceColor', 'Surface'],
    ['textColor', 'Text'],
  ].forEach(([field, label]) => {
    if (!isValidHexColor(form[field])) {
      errors[field] = `${label} must be a 6-digit hex color like #565656.`
    }
  })
  return errors
}

function buildAuthProviderDraftErrors(draft) {
  const errors = {}
  if (!String(draft.label || '').trim()) {
    errors.label = 'Enter the provider label shown to operators.'
  }
  if (draft.emailDomains) {
    const invalidDomain = String(draft.emailDomains)
      .split(/[\n,]+/)
      .map((value) => value.trim().toLowerCase())
      .filter(Boolean)
      .find((value) => !isValidDomain(value))
    if (invalidDomain) {
        errors.emailDomains = 'Use comma-separated email domains like organization.com.'
    }
  }
  if (draft.providerKey === 'auth0') {
    if (!String(draft.auth0Domain || '').trim()) {
      errors.auth0Domain = 'Enter the Auth0 domain for this route.'
    } else if (!isValidDomain(draft.auth0Domain)) {
      errors.auth0Domain = 'Use a valid Auth0 domain like org.us.auth0.com.'
    }
  }
  if (draft.providerKey === 'oidc') {
    if (!String(draft.issuer || '').trim()) {
      errors.issuer = 'Enter the OIDC issuer URL for this provider.'
    } else if (!isValidHttpUrl(draft.issuer)) {
      errors.issuer = 'Enter a full issuer URL starting with http:// or https://.'
    }
    ;[
      ['authorizeUrl', 'Authorize URL'],
      ['tokenUrl', 'Token URL'],
      ['userinfoUrl', 'Userinfo URL'],
      ['logoutUrl', 'Logout URL'],
    ].forEach(([field, label]) => {
      if (draft[field] && !isValidHttpUrl(draft[field])) {
        errors[field] = `${label} must start with http:// or https://.`
      }
    })
  }
  return errors
}

function buildDeliveryFormErrors(form, { customDomainsEnabled = false, brandedEmailEnabled = false } = {}) {
  const errors = {}
  if (customDomainsEnabled && form.primaryDomain && !isValidDomain(form.primaryDomain)) {
    errors.primaryDomain = 'Use a valid fully qualified domain like signals.example.com.'
  }
  if (customDomainsEnabled && form.secondaryDomains) {
    const invalidDomain = String(form.secondaryDomains)
      .split(/[\n,]+/)
      .map((value) => value.trim().toLowerCase())
      .filter(Boolean)
      .find((value) => !isValidDomain(value))
    if (invalidDomain) {
      errors.secondaryDomains = 'Use comma-separated domains like alerts.example.com.'
    }
  }
  if (brandedEmailEnabled && form.senderEmail && !isValidEmail(form.senderEmail)) {
    errors.senderEmail = 'Enter a valid sender email or leave it blank.'
  }
  if (brandedEmailEnabled && form.replyToEmail && !isValidEmail(form.replyToEmail)) {
    errors.replyToEmail = 'Enter a valid reply-to email or leave it blank.'
  }
  if (form.ssoEmailDomain && !isValidDomain(form.ssoEmailDomain)) {
    errors.ssoEmailDomain = 'Use a valid email domain like organization.com.'
  }
  return errors
}

function buildApiTokenFormErrors(form) {
  const errors = {}
  if (!String(form.name || '').trim()) {
    errors.name = 'Enter a token name so the service purpose is clear later.'
  }
  if (!Array.isArray(form.scopes) || !form.scopes.length) {
    errors.scopes = 'Pick at least one scope before issuing a token.'
  }
  if (String(form.expiresInDays || '').trim()) {
    const expiresInDays = Number(form.expiresInDays)
    if (!Number.isInteger(expiresInDays) || expiresInDays < 1 || expiresInDays > 3650) {
      errors.expiresInDays = 'Expires in days must be a whole number between 1 and 3650.'
    }
  }
  return errors
}

function buildWebhookFormErrors(form) {
  const errors = {}
  if (!String(form.name || '').trim()) {
    errors.name = 'Enter a webhook name that explains the partner endpoint.'
  }
  if (!String(form.url || '').trim()) {
    errors.url = 'Enter the partner callback URL.'
  } else if (!isValidHttpUrl(form.url)) {
    errors.url = 'Webhook URL must start with http:// or https://.'
  }
  if (!Array.isArray(form.events) || !form.events.length) {
    errors.events = 'Pick at least one delivery event before creating the webhook.'
  }
  return errors
}

function buildMemberInviteErrors(form) {
  const errors = {}
  if (!String(form.email || '').trim()) {
    errors.email = 'Enter the email address that should receive the invite.'
  } else if (!isValidEmail(form.email)) {
    errors.email = 'Enter a valid invite email.'
  }
  return errors
}

function formatAuthOperationLabel(event) {
  const normalized = String(event || 'validation').replace(/_/g, ' ').trim()
  if (!normalized) return 'Validation'
  return normalized.charAt(0).toUpperCase() + normalized.slice(1)
}

function formatBillingEventLabel(eventKey) {
  const normalized = String(eventKey || 'billing.event').replace(/[._]/g, ' ').trim()
  if (!normalized) return 'Billing event'
  return normalized
    .split(/\s+/)
    .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
    .join(' ')
}

function formatSecuritySeverity(value) {
  const normalized = String(value || 'warning').trim().toLowerCase()
  if (normalized === 'critical') return 'Critical'
  if (normalized === 'healthy') return 'Healthy'
  return 'Warning'
}

function getSecurityBadgeClass(value) {
  const normalized = String(value || 'warning').trim().toLowerCase()
  if (normalized === 'critical') return 'negative'
  if (normalized === 'healthy') return 'positive'
  return 'neutral'
}

function formatSecurityEventLabel(eventType) {
  const normalized = String(eventType || 'security event').replace(/[._]/g, ' ').trim()
  if (!normalized) return 'Security event'
  return normalized
    .split(/\s+/)
    .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
    .join(' ')
}

function getBillingSyncBadgeClass(status) {
  if (status === 'healthy') return 'positive'
  if (status === 'attention' || status === 'stale') return 'negative'
  return 'neutral'
}

export default function SettingsPage() {
  const { preferences, setPreference, applyPreferences, resetPreferences } = usePreferences()
  const { pushToast } = useToast()
  const { session, refreshSession } = useAuth()
  const activeAccountProfile = normalizeAccountProfile(preferences?.activeAccountProfile)
  const activeAccountProfileDefinition = getAccountProfileDefinition(activeAccountProfile)
  const [organizations, setOrganizations] = useState({ items: [], count: 0 })
  const [billingSummary, setBillingSummary] = useState(null)
  const [plans, setPlans] = useState({ items: [], count: 0 })
  const [launchChecklistSnapshot, setLaunchChecklistSnapshot] = useState({
    automation: null,
    marketSession: null,
    safety: null,
    alpaca: null,
  })
  const [billingBusyKey, setBillingBusyKey] = useState('')
  const [billingRecoveryBusyKey, setBillingRecoveryBusyKey] = useState('')
  const [billingCycle, setBillingCycle] = useState('monthly')
  const [organizationBusyKey, setOrganizationBusyKey] = useState('')
  const [organizationForm, setOrganizationForm] = useState(DEFAULT_ORGANIZATION_FORM)
  const [organizationFormErrors, setOrganizationFormErrors] = useState({})
  const [brandingBusy, setBrandingBusy] = useState(false)
  const [brandingForm, setBrandingForm] = useState(DEFAULT_BRANDING_FORM)
  const [brandingFormErrors, setBrandingFormErrors] = useState({})
  const [deliverySnapshot, setDeliverySnapshot] = useState(null)
  const [deliveryBusy, setDeliveryBusy] = useState(false)
  const [deliveryActionBusyKey, setDeliveryActionBusyKey] = useState('')
  const [deliveryForm, setDeliveryForm] = useState(DEFAULT_DELIVERY_FORM)
  const [deliveryFormErrors, setDeliveryFormErrors] = useState({})
  const [authProviderDraft, setAuthProviderDraft] = useState(DEFAULT_AUTH_PROVIDER_DRAFT)
  const [authProviderDraftErrors, setAuthProviderDraftErrors] = useState({})
  const [analyticsSnapshot, setAnalyticsSnapshot] = useState(null)
  const [apiTokenSnapshot, setApiTokenSnapshot] = useState(null)
  const [apiUsageSnapshot, setApiUsageSnapshot] = useState(null)
  const [webhookSnapshot, setWebhookSnapshot] = useState(null)
  const [securitySnapshot, setSecuritySnapshot] = useState(null)
  const [featureFlags, setFeatureFlags] = useState({ items: [], count: 0, enabled_count: 0, override_count: 0, custom_count: 0 })
  const [onboarding, setOnboarding] = useState(null)
  const [templateSnapshot, setTemplateSnapshot] = useState(null)
  const [supportSnapshot, setSupportSnapshot] = useState(null)
  const [onboardingBusyKey, setOnboardingBusyKey] = useState('')
  const [templateBusyKey, setTemplateBusyKey] = useState('')
  const [supportBusyKey, setSupportBusyKey] = useState('')
  const [memberBusyKey, setMemberBusyKey] = useState('')
  const [featureFlagBusyKey, setFeatureFlagBusyKey] = useState('')
  const [apiTokenBusyKey, setApiTokenBusyKey] = useState('')
  const [apiTokenForm, setApiTokenForm] = useState(DEFAULT_API_TOKEN_FORM)
  const [apiTokenFormErrors, setApiTokenFormErrors] = useState({})
  const [createdToken, setCreatedToken] = useState(null)
  const [webhookBusyKey, setWebhookBusyKey] = useState('')
  const [webhookForm, setWebhookForm] = useState(DEFAULT_WEBHOOK_FORM)
  const [webhookFormErrors, setWebhookFormErrors] = useState({})
  const [webhookSecret, setWebhookSecret] = useState(null)
  const [memberInviteForm, setMemberInviteForm] = useState(DEFAULT_MEMBER_INVITE_FORM)
  const [memberInviteErrors, setMemberInviteErrors] = useState({})
  const [saasSurfaceIssue, setSaasSurfaceIssue] = useState(null)
  const operatorSurfaceSummary = buildSurfaceSummary({
    tradingStyle: preferences.tradingStyle,
    startupSurface: preferences.startupSurface,
    rememberLastWorkflowSurface: preferences.rememberLastWorkflowSurface,
    reviewSurface: preferences.defaultReviewSurface,
    showWorkflowStatusStrip: preferences.showWorkflowStatusStrip,
    showWorkflowGuides: preferences.showWorkflowGuides,
    showArrivalBanners: preferences.showArrivalBanners,
  })
  const intradayPreset = normalizeIntradayPreset(preferences.intradayPreset, DEFAULT_INTRADAY_PRESET)
  const intradayPresetProfile = getIntradayPresetProfile(intradayPreset)
  const intradayWatchlistGuide = buildIntradayPresetGuide({ preset: intradayPreset, page: 'watchlist' })
  const orderedIntervalOptions = getStyleIntervalOptions(preferences.tradingStyle)
  const marketModelSummary = buildIntradayModelSummary({
    tradingStyle: preferences.tradingStyle,
    preferences,
  })
  const defaultIntervalModel = buildIntervalModel({
    tradingStyle: preferences.tradingStyle,
    interval: preferences.defaultInterval,
    horizon: preferences.defaultHorizon,
  })

  function applyTradingStylePreset(tradingStyle, presetOverride = intradayPreset) {
    const preset = buildTradingStylePreset(tradingStyle, presetOverride)
    applyPreferences(preset)
    pushToast(
      tradingStyle === 'intraday'
        ? `${intradayPresetProfile.label} defaults applied.`
        : `${getTradingStyleLabel(tradingStyle)} defaults applied.`,
      'success',
    )
  }

  function renderIntradayMarketModelSection() {
    return (
      <div id="market-model-start" tabIndex={-1}>
      <SectionCard
        title="Intraday market model"
        subtitle="Tune how the workstation reads the session clock, opening range, catalyst buffers, and same-day close discipline."
      >
        <section className="metrics-grid">
          <MetricCard
            label="Session clock"
            value={marketModelSummary.sessionModel.label}
            helper={marketModelSummary.sessionModel.timeLabel}
            tone={marketModelSummary.sessionModel.tone}
          />
          <MetricCard
            label="Opening range"
            value={marketModelSummary.openingRangeLabel}
            helper={preferences.tradingStyle === 'intraday' ? 'Used for same-day breakout framing.' : 'Kept available for session context.'}
          />
          <MetricCard
            label="Event guard"
            value={marketModelSummary.eventGuardLabel}
            helper="No-initiation buffer around catalysts in intraday mode."
          />
          <MetricCard
            label="Close buffer"
            value={marketModelSummary.flattenLabel}
            helper="How early the desk starts warning about flattening same-day risk."
          />
        </section>

        <FeedbackState
          tone={marketModelSummary.sessionModel.tone}
          title={marketModelSummary.sessionModel.label}
          description={`${marketModelSummary.sessionModel.detail} ${defaultIntervalModel.recommendedDetail}`}
        />

        <div className="ui-field-grid ui-field-grid--settings">
          <TextField
            label="Opening range minutes"
            hint="How long the desk treats the first regular-session block as the opening range."
            type="number"
            min="5"
            max="60"
            step="1"
            value={preferences.openingRangeMinutes}
            onChange={(e) => setPreference('openingRangeMinutes', Number(e.target.value))}
          />
          <TextField
            label="Event guard minutes"
            hint="Intraday buffer around same-session catalysts before new setups should slow down."
            type="number"
            min="0"
            max="180"
            step="5"
            value={preferences.intradayEventGuardMinutes}
            onChange={(e) => setPreference('intradayEventGuardMinutes', Number(e.target.value))}
          />
          <TextField
            label="Flatten-before-close minutes"
            hint="How early the desk should start treating the close as cleanup time."
            type="number"
            min="1"
            max="60"
            step="1"
            value={preferences.flattenBeforeCloseMinutes}
            onChange={(e) => setPreference('flattenBeforeCloseMinutes', Number(e.target.value))}
          />
        </div>
      </SectionCard>
      </div>
    )
  }
  async function loadSaasSurface() {
    if (appConfig.customerReadyMode) {
      const results = await Promise.allSettled([
        getBillingSummary(),
        getBillingPlans(),
      ])
      const [billingResult, plansResult] = results
      if (billingResult.status === 'fulfilled') setBillingSummary(billingResult.value)
      if (plansResult.status === 'fulfilled') setPlans(plansResult.value)
      const failures = results.filter((result) => result.status === 'rejected')
      if (failures.length) {
        const leadFailure =
          failures[0]?.reason?.response?.data?.detail ||
          failures[0]?.reason?.message ||
          'Some account settings failed to refresh.'
        setSaasSurfaceIssue({
          tone: failures.length === results.length ? 'negative' : 'info',
          title: failures.length === results.length ? 'Account setup unavailable' : 'Account setup refresh incomplete',
          description: leadFailure,
        })
        return
      }
      setSaasSurfaceIssue(null)
      return
    }

    const results = await Promise.allSettled([
      getOrganizations(),
      getBillingSummary(),
      getBillingPlans(),
      getOrganizationDelivery(),
      getOrganizationAnalytics(),
      getOrganizationApiTokens(),
      getOrganizationApiUsage(),
      getOrganizationWebhooks(),
      getOrganizationSecurity(),
      getOrganizationFeatureFlags(),
      getOrganizationOnboarding(),
      getOrganizationOnboardingTemplates(),
      getOrganizationSupportSnapshot(),
    ])

    const [
      organizationsResult,
      billingResult,
      plansResult,
      deliveryResult,
      analyticsResult,
      apiTokensResult,
      apiUsageResult,
      webhooksResult,
      securityResult,
      featureFlagsResult,
      onboardingResult,
      templatesResult,
      supportResult,
    ] = results
    if (organizationsResult.status === 'fulfilled') setOrganizations(organizationsResult.value)
    if (billingResult.status === 'fulfilled') setBillingSummary(billingResult.value)
    if (plansResult.status === 'fulfilled') setPlans(plansResult.value)
    if (deliveryResult.status === 'fulfilled') setDeliverySnapshot(deliveryResult.value)
    if (analyticsResult.status === 'fulfilled') setAnalyticsSnapshot(analyticsResult.value)
    if (apiTokensResult.status === 'fulfilled') setApiTokenSnapshot(apiTokensResult.value)
    if (apiUsageResult.status === 'fulfilled') setApiUsageSnapshot(apiUsageResult.value)
    if (webhooksResult.status === 'fulfilled') setWebhookSnapshot(webhooksResult.value)
    if (securityResult.status === 'fulfilled') setSecuritySnapshot(securityResult.value)
    if (featureFlagsResult.status === 'fulfilled') setFeatureFlags(featureFlagsResult.value)
    if (onboardingResult.status === 'fulfilled') setOnboarding(onboardingResult.value)
    if (templatesResult.status === 'fulfilled') setTemplateSnapshot(templatesResult.value)
    if (supportResult.status === 'fulfilled') setSupportSnapshot(supportResult.value)

    const failures = results.filter((result) => result.status === 'rejected')
    if (failures.length) {
      const totalSources = results.length
      const leadFailure =
        failures[0]?.reason?.response?.data?.detail ||
        failures[0]?.reason?.message ||
        'Some control-plane sections failed to refresh.'
      setSaasSurfaceIssue({
        tone: failures.length === totalSources ? 'negative' : 'info',
        title:
          failures.length === totalSources
            ? 'Control plane unavailable'
            : 'Control plane refresh incomplete',
        description:
          failures.length === totalSources
            ? leadFailure
            : `${failures.length} of ${totalSources} control-plane sources failed to refresh. ${leadFailure}`,
      })
      return
    }

    setSaasSurfaceIssue(null)
  }

  async function loadLaunchChecklistSurface() {
    const results = await Promise.allSettled([
      getOrganizationTradeAutomation(),
      getOrganizationTradeAutomationMarketSession(),
      getOrganizationTradeAutomationSafetyState({ force: true }),
      getOrganizationTradeAutomationAlpacaPaperReadiness(),
      getOrganizationTradeAutomationProductionTrust(),
    ])
    const [automationResult, marketSessionResult, safetyResult, alpacaResult, productionTrustResult] = results
    setLaunchChecklistSnapshot({
      automation: automationResult.status === 'fulfilled' ? automationResult.value : null,
      marketSession: marketSessionResult.status === 'fulfilled' ? marketSessionResult.value : null,
      safety: safetyResult.status === 'fulfilled' ? safetyResult.value : null,
      alpaca: alpacaResult.status === 'fulfilled' ? alpacaResult.value : null,
      productionTrust: productionTrustResult.status === 'fulfilled' ? productionTrustResult.value : null,
    })
  }

  useEffect(() => {
    loadSaasSurface().catch((error) => {
      setSaasSurfaceIssue({
        tone: 'negative',
        title: 'Control plane unavailable',
        description: error?.response?.data?.detail || error?.message || 'Failed to load SaaS settings.',
      })
    })
    loadLaunchChecklistSurface().catch(() => {
      setLaunchChecklistSnapshot((current) => current)
    })
  }, [])

  function scrollToSettingsForm(id) {
    if (typeof document === 'undefined') return
    const element = document.getElementById(id)
    if (!element) return
    element.scrollIntoView({ behavior: 'smooth', block: 'center' })
    if (typeof element.focus === 'function') {
      element.focus({ preventScroll: true })
    }
  }

  function handleRefreshSaasSurface() {
    loadSaasSurface().catch((error) => {
      setSaasSurfaceIssue({
        tone: 'negative',
        title: 'Control plane unavailable',
        description: error?.response?.data?.detail || error?.message || 'Failed to load SaaS settings.',
      })
    })
    loadLaunchChecklistSurface().catch(() => {
      setLaunchChecklistSnapshot((current) => current)
    })
  }

  const activeOrganization = billingSummary?.tenant || session?.active_tenant || null
  const activePermissionMap = session?.active_tenant?.permission_map || {}
  const launchAutomation = launchChecklistSnapshot.automation || {}
  const launchMarketSession = launchChecklistSnapshot.marketSession || {}
  const launchSafety = launchChecklistSnapshot.safety || {}
  const launchAlpaca = launchChecklistSnapshot.alpaca || {}
  const launchProductionTrust = launchChecklistSnapshot.productionTrust || launchMarketSession.production_trust || {}
  const launchProductionOnboarding = launchProductionTrust.onboarding || launchMarketSession.onboarding_checklist || {}
  const launchEvidenceQuality = launchProductionTrust.evidence_quality || launchMarketSession.evidence_quality || {}
  const launchSettingsProof = launchMarketSession.expected_settings_proof || {}
  const launchEntryWindow = launchMarketSession.entry_window_explainer || {}
  const launchKillSwitchOff =
    !Boolean(launchAutomation?.settings?.kill_switch) &&
    !['killed'].includes(String(launchSafety.status || '').toLowerCase())
  const launchDesks = launchMarketSession.desks || {}
  const launchDeskCount = launchDesks.active_armed_count ?? launchDesks.count ?? null
  const entitlements = billingSummary?.entitlements?.items || []
  const usage = billingSummary?.usage || {}
  const checkout = billingSummary?.checkout || {}
  const publicPricingPlans = normalizePricingPlans(plans)
  const billingSync = billingSummary?.sync || { status: 'demo', message: 'Billing state unavailable.' }
  const billingEvents = billingSummary?.events || { items: [], count: 0, status_counts: {} }
  const billingRecovery = billingSummary?.recovery || {
    available_actions: ['reconcile', 'sync_entitlements'],
    jobs: { summary: {} },
    recent_jobs: [],
    failed_events: [],
  }
  const brandingEnabled = entitlements.find((item) => item.key === 'tenant_branding')?.enabled ?? false
  const analyticsSummary = analyticsSnapshot?.summary || {}
  const apiTokensSummary = apiTokenSnapshot?.tokens || { items: [], active_count: 0, remaining: null, enabled: false, scope_catalog: [] }
  const apiUsageSummary = apiUsageSnapshot?.summary || {}
  const webhookSummary = webhookSnapshot?.webhooks || { items: [], deliveries: [], enabled: false, event_catalog: [], active_count: 0, remaining: null }
  const webhookJobSummary = webhookSummary.jobs?.summary || { queued: 0, retrying: 0, running: 0, dead_letter: 0, pending: 0, last_finished_at: null }
  const securitySummary = securitySnapshot?.summary || {
    status: 'healthy',
    critical_count: 0,
    warning_count: 0,
    active_admin_tokens: 0,
    stale_tokens: 0,
    expiring_tokens: 0,
    failed_webhooks: 0,
    dead_letter_jobs: 0,
    auth_launch_blockers: 0,
    last_security_event_at: null,
  }
  const securityTokens = securitySnapshot?.tokens || { risk_items: [] }
  const securityWebhooks = securitySnapshot?.webhooks || { risk_items: [] }
  const securityAuth = securitySnapshot?.auth || { provider_health: {}, launch_blockers: [], risk_items: [] }
  const securityRateLimits = securitySnapshot?.rate_limits || { risk_items: [], recent_events: [], recent_abuse: [], blocked_actors: [] }
  const securityAudit = securitySnapshot?.audit || { items: [], event_type_counts: [], count: 0, last_event_at: null }
  const rolloutFlags = featureFlags?.items || []
  const templatesSummary = templateSnapshot?.templates || { items: [], applied_count: 0, remaining: 0, enabled: false }
  const deliverySettings = deliverySnapshot?.delivery || {}
  const customDomainDelivery = deliverySettings.custom_domains || {}
  const brandedEmailDelivery = deliverySettings.branded_email || {}
  const authRoutingDelivery = deliverySettings.auth_routing || {}
  const customDomainsEnabled =
    rolloutFlags.find((item) => item.key === 'custom_domains')?.effective_enabled ??
    entitlements.find((item) => item.key === 'custom_domains')?.enabled ??
    false
  const brandedEmailEnabled =
    rolloutFlags.find((item) => item.key === 'branded_email')?.effective_enabled ??
    entitlements.find((item) => item.key === 'branded_email')?.enabled ??
    false
  const releaseChannelsEnabled =
    rolloutFlags.find((item) => item.key === 'release_channels')?.effective_enabled ??
    entitlements.find((item) => item.key === 'release_channels')?.enabled ??
    false
  const apiAccessEnabled =
    rolloutFlags.find((item) => item.key === 'api_access')?.effective_enabled ??
    entitlements.find((item) => item.key === 'api_access')?.enabled ??
    false
  const partnerWebhooksEnabled =
    rolloutFlags.find((item) => item.key === 'partner_webhooks')?.effective_enabled ??
    entitlements.find((item) => item.key === 'partner_webhooks')?.enabled ??
    false
  const canCreateOrganization = Boolean(activePermissionMap['tenant.create'])
  const canManageBilling = Boolean(activePermissionMap['tenant.manage_billing'])
  const canManageBranding = Boolean(activePermissionMap['tenant.manage_branding'])
  const canManageDelivery = Boolean(activePermissionMap['tenant.manage_delivery'])
  const canManageOnboarding = Boolean(activePermissionMap['tenant.manage_onboarding'])
  const canManageFeatureFlags = Boolean(activePermissionMap['tenant.manage_flags'])
  const canManageApiTokens = Boolean(activePermissionMap['tenant.manage_api_tokens'])
  const canManageWebhooks = Boolean(activePermissionMap['tenant.manage_webhooks'])
  const canManageSupport = Boolean(activePermissionMap['tenant.manage_support'])
  const canManageMembers = Boolean(activePermissionMap['tenant.manage_members'])
  const canChangeOrganizationStatus = Boolean(activePermissionMap['tenant.change_status'])
  const launchTimeline = (supportSnapshot?.timeline?.items || [])
    .filter((event) => String(event.event_type || '').startsWith('tenant.launch_'))
    .slice(0, 4)

  useEffect(() => {
    const currentOrganization = organizations.items.find((item) => item.slug === activeOrganization?.slug)
    const sourceOrganization = currentOrganization || activeOrganization
    setBrandingForm(buildBrandingForm(sourceOrganization))
    setBrandingFormErrors({})
    setOrganizationFormErrors({})
  }, [organizations, activeOrganization?.slug, billingSummary?.tenant?.logo_url, billingSummary?.tenant?.brand_settings, session?.active_tenant?.logo_url, session?.active_tenant?.brand_settings])

  useEffect(() => {
    const source = deliverySnapshot?.delivery
      ? deliverySnapshot
      : billingSummary?.tenant?.delivery_settings
        ? { delivery_settings: billingSummary.tenant.delivery_settings }
        : session?.active_tenant?.delivery_settings
          ? { delivery_settings: session.active_tenant.delivery_settings }
          : null
    setDeliveryForm(buildDeliveryForm(source))
    setAuthProviderDraft(DEFAULT_AUTH_PROVIDER_DRAFT)
    setDeliveryFormErrors({})
    setAuthProviderDraftErrors({})
  }, [deliverySnapshot, billingSummary?.tenant?.delivery_settings, session?.active_tenant?.delivery_settings, activeOrganization?.slug])

  function saveNotice() {
    pushToast('Preferences saved locally in this browser.', 'success')
  }

  async function handleBillingPortal() {
    if (!canManageBilling) {
      pushToast('Your current role cannot manage billing for this organization.', 'error')
      return
    }
    try {
      const portal = await openBillingPortal()
      if (portal.url) {
        window.location.href = portal.url
        return
      }
      pushToast(portal.message || 'Billing portal opened.', portal.available ? 'success' : 'info')
    } catch (error) {
      pushToast(error?.response?.data?.detail || error.message || 'Billing portal is unavailable.', 'error')
    }
  }

  async function handlePlanChange(planKey) {
    if (!canManageBilling) {
      pushToast('Your current role cannot change plans for this organization.', 'error')
      return
    }
    try {
      setBillingBusyKey(planKey)
      const checkout = await createBillingCheckoutSession({ plan_key: planKey, billing_cycle: billingCycle })
      if (checkout.summary) {
        setBillingSummary(checkout.summary)
      }
      await refreshSession()
      await loadSaasSurface()
      if (checkout.url) {
        window.location.href = checkout.url
        return
      }
      pushToast(checkout.message || `Checkout started for the ${planKey.toUpperCase()} plan.`, checkout.mode === 'sales' ? 'info' : 'success')
    } catch (error) {
      pushToast(error?.response?.data?.detail || error.message || 'Failed to start checkout.', 'error')
    } finally {
      setBillingBusyKey('')
    }
  }

  async function handleBillingRecovery(action) {
    if (!canManageBilling) {
      pushToast('Your current role cannot run billing sync actions.', 'error')
      return
    }
    try {
      setBillingRecoveryBusyKey(action)
      const payload = await runBillingRecovery(action)
      if (payload.summary) {
        setBillingSummary(payload.summary)
      }
      await loadSaasSurface()
      pushToast(payload.message || 'Billing sync queued.', 'info')
    } catch (error) {
      pushToast(error?.response?.data?.detail || error.message || 'Failed to queue billing sync.', 'error')
    } finally {
      setBillingRecoveryBusyKey('')
    }
  }

  async function handleActivateOrganization(organizationSlug) {
    try {
      setOrganizationBusyKey(organizationSlug)
      await activateOrganization(organizationSlug)
      await refreshSession()
      await loadSaasSurface()
      pushToast(`Switched to ${organizationSlug}.`, 'success')
    } catch (error) {
      pushToast(error?.response?.data?.detail || error.message || 'Failed to switch organization.', 'error')
    } finally {
      setOrganizationBusyKey('')
    }
  }

  async function handleCreateOrganization(event) {
    event.preventDefault()
    if (!canCreateOrganization) {
      pushToast('Your current role cannot create new organizations.', 'error')
      return
    }
    const errors = buildOrganizationFormErrors(organizationForm)
    if (Object.keys(errors).length) {
      setOrganizationFormErrors(errors)
      pushToast('Fix the highlighted organization fields and try again.', 'error')
      return
    }

    try {
      setOrganizationBusyKey('create')
      setOrganizationFormErrors({})
      await createOrganization({
        name: organizationForm.name,
        plan_key: organizationForm.planKey,
        billing_email: organizationForm.billingEmail || undefined,
      })
      setOrganizationForm(DEFAULT_ORGANIZATION_FORM)
      setOrganizationFormErrors({})
      await loadSaasSurface()
      pushToast('New organization created for your paid alpha sandbox.', 'success')
    } catch (error) {
      pushToast(error?.response?.data?.detail || error.message || 'Failed to create organization.', 'error')
    } finally {
      setOrganizationBusyKey('')
    }
  }

  async function handleBrandingSave(event) {
    event.preventDefault()
    if (!canManageBranding) {
      pushToast('Your current role cannot update organization branding.', 'error')
      return
    }
    const errors = buildBrandingFormErrors(brandingForm)
    if (Object.keys(errors).length) {
      setBrandingFormErrors(errors)
      pushToast('Fix the highlighted branding fields and try again.', 'error')
      return
    }
    try {
      setBrandingBusy(true)
      setBrandingFormErrors({})
      await updateOrganizationBranding({
        name: brandingForm.name,
        billing_email: brandingForm.billingEmail || null,
        logo_url: brandingForm.logoUrl || null,
        app_name: brandingForm.appName || null,
        app_tagline: brandingForm.appTagline || null,
        accent_primary: brandingForm.accentPrimary || null,
        accent_secondary: brandingForm.accentSecondary || null,
        background_color: brandingForm.backgroundColor || null,
        surface_color: brandingForm.surfaceColor || null,
        text_color: brandingForm.textColor || null,
        support_email: brandingForm.supportEmail || null,
        support_url: brandingForm.supportUrl || null,
      })
      await refreshSession()
      await loadSaasSurface()
      pushToast('Organization branding updated.', 'success')
    } catch (error) {
      pushToast(error?.response?.data?.detail || error.message || 'Failed to update organization branding.', 'error')
    } finally {
      setBrandingBusy(false)
    }
  }

  function handleEditAuthProviderRecord(record) {
    setAuthProviderDraft({
      providerId: record.id || '',
      providerKey: record.provider_key || 'auth0',
      label: record.label || '',
      emailDomains: (record.email_domains || []).join(', '),
      organizationHint: record.organization_hint || '',
      connectionHint: record.connection_hint || '',
      auth0Domain: record.auth0_domain || '',
      issuer: record.issuer || '',
      authorizeUrl: record.authorize_url || '',
      tokenUrl: record.token_url || '',
      userinfoUrl: record.userinfo_url || '',
      logoutUrl: record.logout_url || '',
      clientId: record.client_id || '',
      clientSecret: '',
      audience: record.audience || '',
      scope: record.scope || '',
      allowSignup: record.allow_signup ?? true,
      enabled: record.enabled !== false,
      isDefault: Boolean(record.is_default),
      hasClientSecret: Boolean(record.has_client_secret),
      hasPendingClientSecret: Boolean(record.has_pending_client_secret),
      ready: Boolean(record.ready),
    })
    setAuthProviderDraftErrors({})
  }

  function resetAuthProviderDraft() {
    setAuthProviderDraft(DEFAULT_AUTH_PROVIDER_DRAFT)
    setAuthProviderDraftErrors({})
  }

  function handleSaveAuthProviderRecord() {
    const errors = buildAuthProviderDraftErrors(authProviderDraft)
    if (Object.keys(errors).length) {
      setAuthProviderDraftErrors(errors)
      pushToast('Fix the highlighted auth-provider fields and try again.', 'error')
      return
    }
    setAuthProviderDraftErrors({})
    const nextRecord = {
      provider_id: authProviderDraft.providerId || undefined,
      provider_key: authProviderDraft.providerKey,
      label: authProviderDraft.label.trim(),
      enabled: authProviderDraft.enabled,
      email_domains: authProviderDraft.emailDomains
        .split(/[\n,]+/)
        .map((value) => value.trim().toLowerCase())
        .filter(Boolean),
      organization_hint: authProviderDraft.organizationHint || null,
      connection_hint: authProviderDraft.connectionHint || null,
      auth0_domain: authProviderDraft.providerKey === 'auth0' ? authProviderDraft.auth0Domain || null : null,
      issuer: authProviderDraft.providerKey === 'oidc' ? authProviderDraft.issuer || null : null,
      authorize_url: authProviderDraft.providerKey === 'oidc' ? authProviderDraft.authorizeUrl || null : null,
      token_url: authProviderDraft.providerKey === 'oidc' ? authProviderDraft.tokenUrl || null : null,
      userinfo_url: authProviderDraft.providerKey === 'oidc' ? authProviderDraft.userinfoUrl || null : null,
      logout_url: authProviderDraft.providerKey === 'oidc' ? authProviderDraft.logoutUrl || null : null,
      client_id: authProviderDraft.clientId || null,
      client_secret: authProviderDraft.clientSecret || undefined,
      audience: authProviderDraft.audience || null,
      scope: authProviderDraft.scope || null,
      allow_signup: authProviderDraft.allowSignup,
      is_default: authProviderDraft.isDefault,
    }
    setDeliveryForm((current) => {
      const existingRecords = [...(current.authProviderRecords || [])]
      let nextRecords = authProviderDraft.providerId
        ? existingRecords.map((record) => (record.id === authProviderDraft.providerId ? { ...record, ...nextRecord, id: authProviderDraft.providerId } : record))
        : [...existingRecords, nextRecord]
      if (nextRecord.is_default) {
        nextRecords = nextRecords.map((record, index) => {
          const recordId = record.id || record.provider_id
          const targetId = authProviderDraft.providerId || nextRecords[nextRecords.length - 1]?.id || nextRecords[nextRecords.length - 1]?.provider_id
          return {
            ...record,
            is_default: recordId === targetId || (!targetId && index === nextRecords.length - 1),
          }
        })
      }
      return {
        ...current,
        authProviderRecords: nextRecords.map((record) => ({ ...record, is_default: Boolean(record.is_default) })),
      }
    })
    resetAuthProviderDraft()
    pushToast('Organization auth provider staged. Save delivery to persist it.', 'success')
  }

  function handleRemoveAuthProviderRecord(recordId) {
    setDeliveryForm((current) => ({
      ...current,
      authProviderRecords: (current.authProviderRecords || []).filter((record) => record.id !== recordId && record.provider_id !== recordId),
    }))
    if (authProviderDraft.providerId === recordId) {
      resetAuthProviderDraft()
    }
    pushToast('Organization auth provider removed from the staged delivery config.', 'info')
  }

  async function handleDeliverySave(event) {
    event.preventDefault()
    if (!canManageDelivery) {
      pushToast('Your current role cannot manage delivery settings.', 'error')
      return
    }
    const deliveryErrors = buildDeliveryFormErrors(deliveryForm, {
      customDomainsEnabled,
      brandedEmailEnabled,
    })
    if (Object.keys(deliveryErrors).length) {
      setDeliveryFormErrors(deliveryErrors)
      pushToast('Fix the highlighted delivery fields and try again.', 'error')
      return
    }
    try {
      setDeliveryBusy(true)
      setDeliveryFormErrors({})
      const payload = {}
      if (customDomainsEnabled) {
        payload.primary_domain = deliveryForm.primaryDomain || null
        payload.secondary_domains = deliveryForm.secondaryDomains
          .split(/[\n,]+/)
          .map((value) => value.trim().toLowerCase())
          .filter(Boolean)
        payload.domain_status = deliveryForm.domainStatus || 'draft'
      }
      if (brandedEmailEnabled) {
        payload.email_provider = deliveryForm.emailProvider || 'none'
        payload.provider_status = deliveryForm.providerStatus || 'draft'
        payload.template_set_name = deliveryForm.templateSetName || null
        payload.release_channel = deliveryForm.releaseChannel || 'stable'
        payload.sender_name = deliveryForm.senderName || null
        payload.sender_email = deliveryForm.senderEmail || null
        payload.reply_to_email = deliveryForm.replyToEmail || null
        payload.mail_from_subdomain = deliveryForm.mailFromSubdomain || null
        payload.email_signature = deliveryForm.emailSignature || null
      }
      payload.auth0_organization = deliveryForm.auth0Organization || null
      payload.auth0_connection = deliveryForm.auth0Connection || null
      payload.sso_email_domain = deliveryForm.ssoEmailDomain || null
      payload.enabled_providers = deliveryForm.enabledProviders
      payload.auth_policy = deliveryForm.authPolicy || 'default'
      payload.preferred_provider = deliveryForm.preferredProvider || 'default'
      payload.auth_provider_records = (deliveryForm.authProviderRecords || []).map((record) => ({
        provider_id: record.provider_id || record.id || null,
        provider_key: record.provider_key || record.providerKey,
        label: record.label,
        enabled: record.enabled !== false,
        email_domains: record.email_domains || [],
        organization_hint: record.organization_hint || null,
        connection_hint: record.connection_hint || null,
        auth0_domain: record.auth0_domain || null,
        issuer: record.issuer || null,
        authorize_url: record.authorize_url || null,
        token_url: record.token_url || null,
        userinfo_url: record.userinfo_url || null,
        logout_url: record.logout_url || null,
        client_id: record.client_id || null,
        client_secret: record.client_secret,
        audience: record.audience || null,
        scope: record.scope || null,
        allow_signup: record.allow_signup,
        is_default: Boolean(record.is_default),
      }))
      const next = await updateOrganizationDelivery(payload)
      setDeliverySnapshot(next)
      await refreshSession()
      await loadSaasSurface()
      pushToast('Domain and sender delivery settings updated.', 'success')
    } catch (error) {
      pushToast(error?.response?.data?.detail || error.message || 'Failed to update delivery settings.', 'error')
    } finally {
      setDeliveryBusy(false)
    }
  }

  async function handleDeliveryAction(actionOrPayload, successMessage) {
    if (!canManageDelivery) {
      pushToast('Your current role cannot run delivery actions for this organization.', 'error')
      return
    }
    const actionPayload = typeof actionOrPayload === 'string' ? { action: actionOrPayload } : actionOrPayload
    const busyKey = [actionPayload.action, actionPayload.provider_id].filter(Boolean).join(':') || actionPayload.action
    try {
      setDeliveryActionBusyKey(busyKey)
      const next = await runOrganizationDeliveryAction(actionPayload)
      setDeliverySnapshot(next)
      await refreshSession()
      await loadSaasSurface()
      pushToast(successMessage, 'success')
    } catch (error) {
      pushToast(error?.response?.data?.detail || error.message || 'Failed to run delivery action.', 'error')
    } finally {
      setDeliveryActionBusyKey('')
    }
  }

  async function handleOnboardingToggle(step) {
    if (!canManageOnboarding) {
      pushToast('Your current role cannot manage organization onboarding.', 'error')
      return
    }
    const nextCompleted = !step.completed
    try {
      setOnboardingBusyKey(step.key)
      const next = await updateOrganizationOnboardingStep({ step_key: step.key, completed: nextCompleted })
      setOnboarding(next)
      await loadSaasSurface()
      pushToast(`${step.title} ${nextCompleted ? 'completed' : 're-opened'}.`, 'success')
    } catch (error) {
      pushToast(error?.response?.data?.detail || error.message || 'Failed to update onboarding state.', 'error')
    } finally {
      setOnboardingBusyKey('')
    }
  }

  async function handleSeedWorkspace() {
    if (!canManageOnboarding) {
      pushToast('Your current role cannot seed organization presets.', 'error')
      return
    }
    try {
      setOnboardingBusyKey('seed-workspace')
      await seedOrganizationWorkspace()
      await loadSaasSurface()
      pushToast('Starter preset seeded for this organization.', 'success')
    } catch (error) {
      pushToast(error?.response?.data?.detail || error.message || 'Failed to seed starter preset.', 'error')
    } finally {
      setOnboardingBusyKey('')
    }
  }

  async function handleApplyTemplate(template) {
    if (!canManageOnboarding) {
      pushToast('Your current role cannot apply onboarding templates.', 'error')
      return
    }
    try {
      setTemplateBusyKey(template.key)
      await applyOrganizationOnboardingTemplate(template.key)
      await loadSaasSurface()
      pushToast(`${template.name} applied to the active organization.`, 'success')
    } catch (error) {
      pushToast(error?.response?.data?.detail || error.message || 'Failed to apply onboarding template.', 'error')
    } finally {
      setTemplateBusyKey('')
    }
  }

  function toggleApiTokenScope(scopeKey) {
    setApiTokenForm((current) => {
      const exists = current.scopes.includes(scopeKey)
      return {
        ...current,
        scopes: exists
          ? current.scopes.filter((scope) => scope !== scopeKey)
          : [...current.scopes, scopeKey],
      }
    })
    setApiTokenFormErrors((current) => omitKeys(current, ['scopes']))
  }

  async function handleCreateApiToken(event) {
    event.preventDefault()
    if (!canManageApiTokens) {
      pushToast('Your current role cannot create API tokens.', 'error')
      return
    }
    const errors = buildApiTokenFormErrors(apiTokenForm)
    if (Object.keys(errors).length) {
      setApiTokenFormErrors(errors)
      pushToast('Fix the highlighted token fields and try again.', 'error')
      return
    }
    try {
      setApiTokenBusyKey('create')
      setApiTokenFormErrors({})
      const payload = await createOrganizationApiToken({
        name: apiTokenForm.name,
        scopes: apiTokenForm.scopes,
        expires_in_days: apiTokenForm.expiresInDays ? Number(apiTokenForm.expiresInDays) : null,
      })
      setCreatedToken(payload.token)
      setApiTokenSnapshot({ tenant: apiTokenSnapshot?.tenant || activeOrganization, tokens: payload.tokens })
      setApiTokenForm(DEFAULT_API_TOKEN_FORM)
      setApiTokenFormErrors({})
      await loadSaasSurface()
      pushToast(`${payload.token.name} created. Copy the secret now; it will not be shown again.`, 'success')
    } catch (error) {
      pushToast(error?.response?.data?.detail || error.message || 'Failed to create API token.', 'error')
    } finally {
      setApiTokenBusyKey('')
    }
  }

  async function handleRevokeApiToken(token) {
    if (!canManageApiTokens) {
      pushToast('Your current role cannot revoke API tokens.', 'error')
      return
    }
    try {
      setApiTokenBusyKey(token.id)
      const payload = await revokeOrganizationApiToken(token.id)
      setApiTokenSnapshot({ tenant: apiTokenSnapshot?.tenant || activeOrganization, tokens: payload.tokens })
      if (createdToken?.id === token.id) {
        setCreatedToken(null)
      }
      await loadSaasSurface()
      pushToast(`${token.name} revoked.`, 'info')
    } catch (error) {
      pushToast(error?.response?.data?.detail || error.message || 'Failed to revoke API token.', 'error')
    } finally {
      setApiTokenBusyKey('')
    }
  }

  function toggleWebhookEvent(eventKey) {
    setWebhookForm((current) => {
      const exists = current.events.includes(eventKey)
      return {
        ...current,
        events: exists
          ? current.events.filter((event) => event !== eventKey)
          : [...current.events, eventKey],
      }
    })
    setWebhookFormErrors((current) => omitKeys(current, ['events']))
  }

  async function handleCreateWebhook(event) {
    event.preventDefault()
    if (!canManageWebhooks) {
      pushToast('Your current role cannot create partner webhooks.', 'error')
      return
    }
    const errors = buildWebhookFormErrors(webhookForm)
    if (Object.keys(errors).length) {
      setWebhookFormErrors(errors)
      pushToast('Fix the highlighted webhook fields and try again.', 'error')
      return
    }
    try {
      setWebhookBusyKey('create')
      setWebhookFormErrors({})
      const payload = await createOrganizationWebhook({
        name: webhookForm.name,
        url: webhookForm.url,
        events: webhookForm.events,
      })
      setWebhookSnapshot({ tenant: webhookSnapshot?.tenant || activeOrganization, webhooks: payload.webhooks })
      setWebhookSecret(payload.webhook)
      setWebhookForm(DEFAULT_WEBHOOK_FORM)
      setWebhookFormErrors({})
      await loadSaasSurface()
      pushToast(`${payload.webhook.name} created. Copy the signing secret now.`, 'success')
    } catch (error) {
      pushToast(error?.response?.data?.detail || error.message || 'Failed to create webhook.', 'error')
    } finally {
      setWebhookBusyKey('')
    }
  }

  async function handleWebhookAction(webhook, action, successMessage) {
    if (!canManageWebhooks) {
      pushToast('Your current role cannot manage partner webhooks.', 'error')
      return
    }
    try {
      setWebhookBusyKey(`${webhook.id}:${action}`)
      const payload = await runOrganizationWebhookAction({ webhook_id: webhook.id, action })
      setWebhookSnapshot({ tenant: webhookSnapshot?.tenant || activeOrganization, webhooks: payload.webhooks })
      if (payload.secret) {
        setWebhookSecret({ ...webhook, secret: payload.secret.secret })
      }
      await loadSaasSurface()
      pushToast(successMessage, action === 'send_test' ? 'info' : 'success')
    } catch (error) {
      pushToast(error?.response?.data?.detail || error.message || 'Failed to run webhook action.', 'error')
    } finally {
      setWebhookBusyKey('')
    }
  }

  async function handleOrganizationStatusChange(nextStatus) {
    if (!canChangeOrganizationStatus) {
      pushToast('Your current role cannot change organization status.', 'error')
      return
    }
    try {
      setSupportBusyKey(nextStatus)
      await updateOrganizationStatus(nextStatus)
      await refreshSession()
      await loadSaasSurface()
      pushToast(`Organization moved to ${nextStatus}.`, nextStatus === 'paused' ? 'info' : 'success')
    } catch (error) {
      pushToast(error?.response?.data?.detail || error.message || 'Failed to update organization status.', 'error')
    } finally {
      setSupportBusyKey('')
    }
  }

  async function handleInviteMember(event) {
    event.preventDefault()
    if (!canManageMembers) {
      pushToast('Your current role cannot invite organization members.', 'error')
      return
    }
    const errors = buildMemberInviteErrors(memberInviteForm)
    if (Object.keys(errors).length) {
      setMemberInviteErrors(errors)
      pushToast('Fix the highlighted invite fields and try again.', 'error')
      return
    }
    try {
      setMemberBusyKey('invite')
      setMemberInviteErrors({})
      const payload = await inviteOrganizationMember({
        email: memberInviteForm.email,
        name: memberInviteForm.name || null,
        role: memberInviteForm.role,
        message: memberInviteForm.message || null,
      })
      setSupportSnapshot(payload.support)
      setMemberInviteForm(DEFAULT_MEMBER_INVITE_FORM)
      setMemberInviteErrors({})
      await loadSaasSurface()
      pushToast(`Invitation prepared for ${payload.invitation.email}.`, 'success')
    } catch (error) {
      pushToast(error?.response?.data?.detail || error.message || 'Failed to create organization invitation.', 'error')
    } finally {
      setMemberBusyKey('')
    }
  }

  async function handleUpdateMemberRole(member, role) {
    if (!canManageMembers) {
      pushToast('Your current role cannot change member roles.', 'error')
      return
    }
    try {
      setMemberBusyKey(`role:${member.membership_id}`)
      const payload = await updateOrganizationMember({ membership_id: member.membership_id, role })
      setSupportSnapshot(payload.support)
      await loadSaasSurface()
      pushToast(`${member.name} is now ${role}.`, 'success')
    } catch (error) {
      pushToast(error?.response?.data?.detail || error.message || 'Failed to update member role.', 'error')
    } finally {
      setMemberBusyKey('')
    }
  }

  async function handleRemoveMember(member) {
    if (!canManageMembers) {
      pushToast('Your current role cannot remove members.', 'error')
      return
    }
    try {
      setMemberBusyKey(`remove:${member.membership_id}`)
      const payload = await removeOrganizationMember(member.membership_id)
      setSupportSnapshot(payload.support)
      await loadSaasSurface()
      pushToast(`${member.name} removed from the organization.`, 'info')
    } catch (error) {
      pushToast(error?.response?.data?.detail || error.message || 'Failed to remove member.', 'error')
    } finally {
      setMemberBusyKey('')
    }
  }

  async function handleInvitationAction(invitation, action, successMessage) {
    if (!canManageMembers) {
      pushToast('Your current role cannot manage invitations.', 'error')
      return
    }
    try {
      setMemberBusyKey(`${invitation.id}:${action}`)
      const payload = await runOrganizationInvitationAction({ invitation_id: invitation.id, action })
      setSupportSnapshot(payload.support)
      await loadSaasSurface()
      pushToast(successMessage, action === 'resend' ? 'success' : 'info')
    } catch (error) {
      pushToast(error?.response?.data?.detail || error.message || 'Failed to update invitation.', 'error')
    } finally {
      setMemberBusyKey('')
    }
  }

  async function handleFeatureFlagToggle(flag) {
    if (!canManageFeatureFlags) {
      pushToast('Your current role cannot change feature rollout flags.', 'error')
      return
    }
    try {
      setFeatureFlagBusyKey(flag.key)
      const next = await updateOrganizationFeatureFlag({
        flag_key: flag.key,
        enabled: !flag.effective_enabled,
      })
      setFeatureFlags(next)
      await loadSaasSurface()
      pushToast(`${flag.label} ${flag.effective_enabled ? 'disabled' : 'enabled'} for ${activeOrganization?.name || 'the active organization'}.`, 'success')
    } catch (error) {
      pushToast(error?.response?.data?.detail || error.message || 'Failed to update feature flag.', 'error')
    } finally {
      setFeatureFlagBusyKey('')
    }
  }

  async function handleFeatureFlagReset(flag) {
    if (!canManageFeatureFlags) {
      pushToast('Your current role cannot reset feature rollout flags.', 'error')
      return
    }
    try {
      setFeatureFlagBusyKey(`${flag.key}:reset`)
      const next = await updateOrganizationFeatureFlag({
        flag_key: flag.key,
        reset: true,
      })
      setFeatureFlags(next)
      await loadSaasSurface()
      pushToast(`${flag.label} reset to the organization plan default.`, 'info')
    } catch (error) {
      pushToast(error?.response?.data?.detail || error.message || 'Failed to reset feature flag.', 'error')
    } finally {
      setFeatureFlagBusyKey('')
    }
  }

  if (appConfig.customerReadyMode) {
    return (
      <>
        <PageIntro
          kicker="Account setup"
          title="Settings"
          description="Manage linked Alpaca accounts, billing plan visibility, trading defaults, and live safety defaults without exposing admin operations."
          badge={activeOrganization?.slug || 'customer-workspace'}
        />
        {saasSurfaceIssue ? (
          saasSurfaceIssue.tone === 'negative' ? (
            <ErrorState
              title={saasSurfaceIssue.title}
              description={saasSurfaceIssue.description}
              actionLabel="Refresh account setup"
              onAction={handleRefreshSaasSurface}
            />
          ) : (
            <FeedbackState
              tone={saasSurfaceIssue.tone}
              eyebrow="Account setup"
              title={saasSurfaceIssue.title}
              description={saasSurfaceIssue.description}
              actions={[{ label: 'Refresh account setup', onAction: handleRefreshSaasSurface, variant: 'solid' }]}
              role="status"
            />
          )
        ) : null}

        <section className="metrics-grid metrics-grid--compact">
          <MetricCard
            label="Workspace"
            value={activeOrganization?.name || 'Trading workspace'}
            helper={activeOrganization?.slug || 'customer'}
          />
          <MetricCard
            label="Plan"
            value={(billingSummary?.plan?.name || activeOrganization?.plan_key || 'Professional').toString()}
            helper={billingSummary?.subscription?.status || 'active'}
          />
          <MetricCard
            label="Account profile"
            value={activeAccountProfileDefinition.badgeLabel}
            helper={activeAccountProfileDefinition.settingsTitle}
          />
          <MetricCard
            label="Trading style"
            value={getTradingStyleLabel(preferences.tradingStyle)}
            helper={preferences.tradingStyle === 'intraday' ? intradayPresetProfile.shortLabel : `${preferences.defaultInterval} default`}
          />
        </section>

        <LinkedBrokerageAccountsSection
          title="Linked Alpaca accounts"
          subtitle="Connect Alpaca paper or live accounts for account setup. Trading remains controlled by risk gates, approval rules, and the selected execution profile."
          showBrokerageBinding
        />

        <ExecutionProviderDiagnosticsSection />

        <SectionCard
          title="Launch checklist"
          subtitle="Customer-safe checks for a purchased workspace before relying on unattended Alpaca paper automation."
          actions={(
            <Button type="button" variant="ghost" size="sm" onClick={() => window.open('/live', '_blank', 'noopener,noreferrer')}>
              Open live console
            </Button>
          )}
        >
          <div className="metrics-grid metrics-grid--compact">
            {[
              {
                label: 'Alpaca paper linked',
                value: launchAlpaca.status ? String(launchAlpaca.status) : 'Loading',
                tone: launchAlpaca.status === 'ready' ? 'positive' : launchAlpaca.status === 'blocked' ? 'negative' : 'warning',
                helper: launchAlpaca.account_heartbeat?.available
                  ? 'Alpaca paper account heartbeat is visible.'
                  : 'Linked accounts handle custody, statements, and execution.',
              },
              {
                label: 'Automation armed',
                value: launchAutomation?.settings?.enabled === false ? 'Off' : launchAutomation?.settings?.armed === false ? 'Not armed' : 'Review',
                tone: launchAutomation?.settings?.enabled === false || launchAutomation?.settings?.armed === false ? 'warning' : 'neutral',
                helper: launchEntryWindow.next_action || 'Verify armed state in the Market Watchdog before the market opens.',
              },
              {
                label: 'Kill switch',
                value: launchKillSwitchOff ? 'Off' : 'On',
                tone: launchKillSwitchOff ? 'positive' : 'negative',
                helper: launchKillSwitchOff ? 'Kill switch is clear in the latest safety snapshot.' : 'The system never clears it by itself; operator proof is required.',
              },
              {
                label: 'Risk caps',
                value: `${launchSettingsProof.passed_count ?? 0}/${launchSettingsProof.count ?? 0}`,
                tone: launchSettingsProof.status === 'ready' ? 'positive' : 'warning',
                helper: launchSettingsProof.next_action || 'Daily loss, notional, cooldown, duplicate-order, and paper-route gates stay authoritative.',
              },
              {
                label: 'Desks',
                value: launchDeskCount == null ? 'Loading' : `${launchDeskCount} active`,
                tone: Number(launchDeskCount || 0) >= 5 ? 'positive' : 'warning',
                helper: 'Fast scalper, stat arb, intraday momentum, swing, and macro report through Market Ops.',
              },
              {
                label: 'Entry window',
                value: launchEntryWindow.state ? launchEntryWindow.state.replaceAll('_', ' ') : 'Loading',
                tone: launchEntryWindow.entry_allowed ? 'positive' : 'neutral',
                helper: launchEntryWindow.plain_language || 'Market closed is a session state, not a broken system.',
              },
              {
                label: 'Production trust',
                value: launchProductionTrust.status ? String(launchProductionTrust.status).replaceAll('_', ' ') : 'Loading',
                tone: launchProductionTrust.status === 'ready' ? 'positive' : launchProductionTrust.status === 'blocked' ? 'negative' : 'warning',
                helper: launchProductionTrust.next_action || 'Alerts, support bundle, replay proof, provider reliability, and release validation are tracked in Market Watchdog.',
              },
              {
                label: 'Trust onboarding',
                value: `${launchProductionOnboarding.completed_count ?? 0}/${launchProductionOnboarding.total_count ?? 0}`,
                tone: launchProductionOnboarding.status === 'ready' ? 'positive' : 'warning',
                helper: 'This checklist cannot enable live trading; it only proves the paper environment is customer-ready.',
              },
              {
                label: 'Evidence quality',
                value: `${Number(launchEvidenceQuality.quality_score || 0).toFixed(0)}%`,
                tone: launchEvidenceQuality.status === 'ready' ? 'positive' : 'warning',
                helper: 'Evidence 100M tracks useful, stale, duplicate, blocker, missed-move, AI, and session-proof events.',
              },
            ].map((item) => (
              <MetricCard key={`launch-checklist:${item.label}`} {...item} />
            ))}
          </div>
        </SectionCard>

        <SectionCard
          title="Billing plan"
          subtitle="Compare the purchased plan against the product ladder. Premium value comes from controls, evidence, auditability, and operating workflow."
          actions={(
            <>
              <Button type="button" variant="ghost" size="sm" onClick={handleRefreshSaasSurface}>
                Refresh plan
              </Button>
              <Button type="button" variant="ghost" size="sm" onClick={() => window.open('/pricing', '_blank', 'noopener,noreferrer')}>
                Public pricing
              </Button>
            </>
          )}
        >
          <div className="pricing-settings-intro">
            <div>
              <Kicker as="div">Paper-validated live automation control</Kicker>
              <h3>{billingSummary?.plan?.name || 'Professional'} plan active</h3>
              <p>
                The plan comparison stays visible for operators while billing operations, delivery routing,
                and support internals are handled outside the standard customer workspace.
              </p>
            </div>
            <StatusBadge tone="positive">{billingSummary?.subscription?.status || 'Active'}</StatusBadge>
          </div>
          <div className="analysis-form analysis-form--wide">
            <SelectField value={billingCycle} onChange={(event) => setBillingCycle(event.target.value)}>
              <option value="monthly">Monthly billing</option>
              <option value="annual">Annual billing</option>
            </SelectField>
          </div>
          <div className="pricing-tier-grid pricing-tier-grid--settings">
            {publicPricingPlans.map((plan) => (
              <PricingTierCard
                key={plan.key}
                plan={plan}
                billingCycle={billingCycle}
                isActive={plan.key === billingSummary?.plan?.key}
                busy={billingBusyKey === plan.key}
                disabled={!canManageBilling}
                onAction={handlePlanChange}
              />
            ))}
          </div>
          <PricingComparisonTable plans={publicPricingPlans} />
        </SectionCard>

        <SectionCard title="Trading defaults" subtitle="Browser preferences for the customer workstation. These do not bypass risk gates or paper-route safety.">
          <div className="ui-field-grid ui-field-grid--settings">
            <TextField
              label="Default ticker"
              value={preferences.defaultTicker}
              onChange={(e) => setPreference('defaultTicker', e.target.value.toUpperCase())}
              placeholder="Default ticker"
            />
            <SelectField
              label="Default interval"
              hint={`${getTradingStyleLabel(preferences.tradingStyle)} mode keeps ${orderedIntervalOptions.slice(0, 3).join(', ')} closest to the front of the workflow.`}
              value={preferences.defaultInterval}
              onChange={(e) => setPreference('defaultInterval', e.target.value)}
            >
              {orderedIntervalOptions.map((interval) => (
                <option key={interval} value={interval}>{interval}</option>
              ))}
            </SelectField>
            <TextField
              label="Default horizon"
              hint={defaultIntervalModel.recommendedDetail}
              type="number"
              min="1"
              max="50"
              value={preferences.defaultHorizon}
              onChange={(e) => setPreference('defaultHorizon', Number(e.target.value))}
            />
            <TextField
              label="Polling cadence"
              type="number"
              min="5000"
              step="1000"
              value={preferences.pollingMs}
              onChange={(e) => setPreference('pollingMs', Number(e.target.value))}
            />
          </div>
          <ActionBar className="settings-action-bar">
            <Button type="button" variant="solid" onClick={saveNotice}>Save</Button>
            <Button
              type="button"
              variant="ghost"
              onClick={async () => {
                await clearRecentTickers()
                pushToast('Recent ticker history cleared.', 'info')
              }}
            >
              Clear recents
            </Button>
            <Button
              type="button"
              variant="subtle"
              onClick={() => {
                resetPreferences()
                pushToast('Preferences reset to defaults.', 'info')
              }}
            >
              Reset
            </Button>
          </ActionBar>
        </SectionCard>

        <SectionCard title="Live safety defaults" subtitle="Customer-visible workflow controls. Admin rollout, token, webhook, and support operations stay out of this view.">
          <ActionBar className="settings-action-bar">
            <Button type="button" variant={preferences.tradingStyle === 'swing' ? 'solid' : 'ghost'} onClick={() => applyTradingStylePreset('swing')}>
              Apply swing defaults
            </Button>
            <Button type="button" variant={preferences.tradingStyle === 'intraday' ? 'solid' : 'ghost'} onClick={() => applyTradingStylePreset('intraday')}>
              Apply intraday defaults
            </Button>
            {preferences.tradingStyle === 'intraday' ? (
              <Button type="button" variant="ghost" onClick={() => applyTradingStylePreset('intraday', intradayPreset)}>
                Apply {intradayPresetProfile.shortLabel} preset
              </Button>
            ) : null}
          </ActionBar>
          <div className="ui-field-grid ui-field-grid--settings">
            <SelectField
              label="Trading style"
              value={preferences.tradingStyle}
              onChange={(e) => setPreference('tradingStyle', e.target.value)}
            >
              {TRADING_STYLE_OPTIONS.map((option) => (
                <option key={option.value} value={option.value}>{option.label}</option>
              ))}
            </SelectField>
            {preferences.tradingStyle === 'intraday' ? (
              <SelectField
                label="Intraday preset"
                value={intradayPreset}
                onChange={(e) => setPreference('intradayPreset', normalizeIntradayPreset(e.target.value, DEFAULT_INTRADAY_PRESET))}
              >
                {INTRADAY_PRESET_OPTIONS.map((option) => (
                  <option key={option.value} value={option.value}>{option.label}</option>
                ))}
              </SelectField>
            ) : null}
            <SelectField
              label="Startup surface"
              value={preferences.startupSurface}
              onChange={(e) => setPreference('startupSurface', e.target.value)}
            >
              {STARTUP_SURFACE_OPTIONS.map((option) => (
                <option key={option.value} value={option.value}>{option.label}</option>
              ))}
            </SelectField>
            <SelectField
              label="Review surface"
              value={preferences.defaultReviewSurface}
              onChange={(e) => setPreference('defaultReviewSurface', e.target.value)}
            >
              {REVIEW_SURFACE_OPTIONS.map((option) => (
                <option key={option.value} value={option.value}>{option.label}</option>
              ))}
            </SelectField>
            <ToggleField
              label="Resume last workflow surface"
              checked={preferences.rememberLastWorkflowSurface}
              onChange={(e) => setPreference('rememberLastWorkflowSurface', e.target.checked)}
            />
            <ToggleField
              label="Show workflow status strip"
              checked={preferences.showWorkflowStatusStrip}
              onChange={(e) => setPreference('showWorkflowStatusStrip', e.target.checked)}
            />
          </div>
        </SectionCard>

        <TradeAutomationSection />

        {renderIntradayMarketModelSection()}
      </>
    )
  }

  return (
    <>
      <PageIntro
        kicker="Brokerage account"
        title={activeAccountProfileDefinition.settingsTitle}
        description={activeAccountProfileDefinition.settingsDescription}
        badge={activeOrganization?.slug || 'Alpaca profile'}
      />
      {saasSurfaceIssue ? (
        saasSurfaceIssue.tone === 'negative' ? (
          <ErrorState
            title={saasSurfaceIssue.title}
            description={saasSurfaceIssue.description}
            actionLabel="Refresh control plane"
            onAction={handleRefreshSaasSurface}
          />
        ) : (
          <FeedbackState
            tone={saasSurfaceIssue.tone}
            eyebrow="Control plane"
            title={saasSurfaceIssue.title}
            description={saasSurfaceIssue.description}
            actions={[{ label: 'Refresh control plane', onAction: handleRefreshSaasSurface, variant: 'solid' }]}
            role="status"
          />
        )
      ) : null}
      <section className="metrics-grid metrics-grid--compact">
        <MetricCard
          label="Active organization"
                      value={activeOrganization?.name || 'Systematic Equities Desk'}
                      helper={activeOrganization?.slug || 'systematic-equities'}
        />
        <MetricCard
          label="Active plan"
          value={(billingSummary?.plan?.name || activeOrganization?.plan_key || 'Pro').toString()}
          helper={billingSummary?.subscription?.status || 'active'}
        />
        <MetricCard
          label="Adoption score"
          value={analyticsSummary?.adoption_score ?? 0}
          helper={analyticsSummary?.activation_stage || 'Provisioning'}
        />
        <MetricCard
          label="Launch readiness"
          value={`${analyticsSummary?.rollout_readiness ?? 0}%`}
          helper={`${onboarding?.completed_count ?? 0}/${onboarding?.count ?? 0} checkpoints`}
        />
        <MetricCard
          label="Seats remaining"
          value={formatRemaining(usage?.members?.remaining)}
          helper={`${usage?.members?.used ?? 0} in use`}
        />
        <MetricCard
          label="Flag overrides"
          value={featureFlags?.override_count ?? 0}
          helper={`${featureFlags?.enabled_count ?? 0} enabled`}
        />
      </section>

      <SectionCard
        title="Organization & billing"
        subtitle="Commercial state for the active organization. This is the first step toward a real platform control plane."
        actions={(
          <>
            <Button type="button" variant="ghost" size="sm" disabled={!canManageBilling} onClick={handleBillingPortal}>
              Billing portal
            </Button>
            <Button type="button" variant="ghost" size="sm" onClick={handleRefreshSaasSurface}>
              Refresh SaaS data
            </Button>
          </>
        )}
      >
        <div className="saas-info-grid">
          <div className="saas-stat">
            <span>Organization slug</span>
                    <strong>{activeOrganization?.slug || 'systematic-equities'}</strong>
          </div>
          <div className="saas-stat">
            <span>Billing email</span>
              <strong>{billingSummary?.tenant?.billing_email || session?.user?.email || 'demo@example.test'}</strong>
          </div>
          <div className="saas-stat">
            <span>Provider</span>
            <strong>{billingSummary?.subscription?.provider || 'internal-demo'}</strong>
          </div>
          <div className="saas-stat">
            <span>Managed mode</span>
            <strong>{billingSummary?.subscription?.managed_mode || 'demo'}</strong>
          </div>
          <div className="saas-stat">
            <span>Saved preset allowance</span>
            <strong>{formatRemaining(usage?.workspaces?.limit)}</strong>
          </div>
          <div className="saas-stat">
            <span>Saved layout allowance</span>
            <strong>{formatRemaining(usage?.layouts?.limit)}</strong>
          </div>
          <div className="saas-stat">
            <span>Checkout mode</span>
            <strong>{checkout.mode || 'demo'}</strong>
          </div>
          <div className="saas-stat">
            <span>Stripe configured</span>
            <strong>{checkout.configured ? 'Yes' : 'No'}</strong>
          </div>
        </div>
        <div className="analysis-form analysis-form--wide">
          <SelectField value={billingCycle} onChange={(event) => setBillingCycle(event.target.value)} disabled={!canManageBilling}>
            <option value="monthly">Monthly billing</option>
            <option value="annual">Annual billing</option>
          </SelectField>
          <div className="form-hint">
            {!canManageBilling
              ? 'Your current role can view billing state but cannot change plans or open the billing portal.'
              : checkout.configured
                ? 'Upgrades will open Stripe Checkout.'
                : 'Stripe is not configured yet, so checkout falls back to a demo plan activation.'}
          </div>
        </div>

        <div className="analytics-grid">
          <article className="support-card">
            <div className="organization-item__title-row">
              <h3>Billing sync health</h3>
              <StatusBadge tone={getBillingSyncBadgeClass(billingSync.status)}>
                {billingSync.status || 'demo'}
              </StatusBadge>
            </div>
            <div className="support-card__stats">
              <div><span>Last processed</span><strong>{formatDateTime(billingSync.last_processed_at)}</strong></div>
              <div><span>Last failure</span><strong>{formatDateTime(billingSync.last_failed_at)}</strong></div>
              <div><span>Recent failures</span><strong>{billingSync.recent_failure_count ?? 0}</strong></div>
              <div><span>Duplicate replays</span><strong>{billingSync.duplicate_count ?? 0}</strong></div>
            </div>
            <p>{billingSync.message || 'Billing sync state unavailable.'}</p>
            {billingSync.needs_reconciliation ? (
              <FeedbackState
                compact
                tone="negative"
                eyebrow="Billing sync"
                title="Billing reconciliation needed"
            description="Billing needs attention. Review the recent event trail before trusting organization access changes."
                actions={[
                  { label: 'Refresh control plane', onAction: handleRefreshSaasSurface, variant: 'ghost' },
                  ...(canManageBilling
                    ? [{ label: 'Billing portal', onAction: handleBillingPortal, variant: 'ghost' }]
                    : []),
                ]}
                role="alert"
              />
            ) : null}
          </article>

          <article className="support-card">
            <div className="organization-item__title-row">
              <h3>Recovery & reconciliation</h3>
              <StatusBadge tone={billingRecovery.pending_job_count ? 'neutral' : 'positive'}>
                {billingRecovery.pending_job_count ? `${billingRecovery.pending_job_count} queued` : 'Idle'}
              </StatusBadge>
            </div>
            <div className="support-card__stats">
              <div><span>Last reconciled</span><strong>{formatDateTime(billingRecovery.last_reconciled_at)}</strong></div>
              <div><span>Last recovery</span><strong>{billingRecovery.last_recovery_action || 'None'}</strong></div>
              <div><span>Failed events</span><strong>{billingRecovery.failed_event_count ?? 0}</strong></div>
              <div><span>Dead-letter jobs</span><strong>{billingRecovery.jobs?.summary?.dead_letter ?? 0}</strong></div>
            </div>
            <p>
              {billingRecovery.last_recovery_error
                ? `Most recent recovery issue: ${billingRecovery.last_recovery_error}`
                : 'Queue reconciliation, retry the latest failed Stripe event, or resync entitlements from the active plan.'}
            </p>
            <div className="analysis-form analysis-form--wide">
              {(billingSync.available_actions || billingRecovery.available_actions || []).map((action) => (
                <Button
                  key={action}
                  type="button"
                  variant="ghost"
                  size="sm"
                  disabled={!canManageBilling || Boolean(billingRecoveryBusyKey)}
                  onClick={() => handleBillingRecovery(action)}
                >
                  {billingRecoveryBusyKey === action ? 'Queueing...' : action.replace(/_/g, ' ')}
                </Button>
              ))}
            </div>
            <div className="support-timeline">
              {(billingRecovery.recent_jobs || []).slice(0, 4).map((job) => (
                <article key={job.id} className="support-event">
                  <div className="support-event__header">
                    <strong>{String(job.payload?.action || job.job_type || 'recovery').replace(/_/g, ' ')}</strong>
                    <span>{formatDateTime(job.finished_at || job.started_at || job.available_at)}</span>
                  </div>
                  <p>{job.status} | attempt {job.attempt_count ?? 0}/{job.max_attempts ?? 0}</p>
                  <code>{JSON.stringify({ job_id: job.id, error: job.error_message || null })}</code>
                </article>
              ))}
              {!(billingRecovery.recent_jobs || []).length ? (
                <EmptyState
                  title="No billing sync jobs"
                  description="No billing sync jobs have run for this organization yet."
                />
              ) : null}
            </div>
          </article>

          <article className="support-card">
            <div className="organization-item__title-row">
              <h3>Recent billing events</h3>
              <StatusBadge tone="neutral">{billingEvents.count ?? 0} total</StatusBadge>
            </div>
            <div className="support-card__stats">
              <div><span>Processed</span><strong>{billingEvents.status_counts?.processed ?? 0}</strong></div>
              <div><span>Ignored</span><strong>{billingEvents.status_counts?.ignored ?? 0}</strong></div>
              <div><span>Failed</span><strong>{billingEvents.status_counts?.failed ?? 0}</strong></div>
              <div><span>Duplicates</span><strong>{billingEvents.status_counts?.duplicate ?? 0}</strong></div>
            </div>
            <div className="support-timeline">
              {(billingEvents.items || []).slice(0, 6).map((event) => (
                <article key={event.id} className="support-event">
                  <div className="support-event__header">
                    <strong>{formatBillingEventLabel(event.event_key)}</strong>
                    <span>{formatDateTime(event.processed_at || event.received_at)}</span>
                  </div>
                  <p>{event.provider} | {event.source} | {event.status}</p>
                  <code>{JSON.stringify({ event_id: event.external_event_id || null, plan_key: event.plan_key || null, error: event.error_message || null })}</code>
                </article>
              ))}
              {!(billingEvents.items || []).length ? (
                <EmptyState
                  title="No billing events yet"
                  description="Billing events will appear here after plan changes, checkout, or Stripe webhook activity."
                />
              ) : null}
            </div>
          </article>
        </div>
      </SectionCard>

      <SectionCard
        title="Onboarding"
        subtitle="Launch checklist for taking an organization from freshly created to pilot-ready without engineer intervention."
        actions={(
          <>
            <StatusBadge tone="neutral">
              {onboarding?.progress_percent ?? 0}% complete
            </StatusBadge>
            <Button
              type="button"
              variant="ghost"
              size="sm"
              disabled={!canManageOnboarding || onboardingBusyKey === 'seed-workspace'}
              onClick={handleSeedWorkspace}
            >
              {onboardingBusyKey === 'seed-workspace' ? 'Seeding...' : 'Seed starter preset'}
            </Button>
          </>
        )}
      >
        <div className="onboarding-summary-grid">
          <div className="saas-stat">
            <span>Completed steps</span>
            <strong>{onboarding?.completed_count ?? 0}/{onboarding?.count ?? 0}</strong>
          </div>
          <div className="saas-stat">
            <span>Saved presets</span>
            <strong>{onboarding?.workspace_count ?? 0}</strong>
          </div>
          <div className="saas-stat">
            <span>Organization status</span>
            <strong>{supportSnapshot?.status || activeOrganization?.status || 'active'}</strong>
          </div>
        </div>

        <div className="onboarding-list">
          {(onboarding?.steps || []).map((step) => (
            <article key={step.key} className={`onboarding-item ${step.completed ? 'onboarding-item--complete' : ''}`}>
              <div className="onboarding-item__copy">
                <div className="organization-item__title-row">
                  <h3>{step.title}</h3>
                    <StatusBadge tone={step.completed ? 'positive' : 'neutral'}>
                      {step.completed ? 'Complete' : step.source}
                    </StatusBadge>
                </div>
                <p>{step.description}</p>
                </div>
              <Button
                type="button"
                variant="ghost"
                size="sm"
                disabled={!canManageOnboarding || onboardingBusyKey === step.key}
                onClick={() => handleOnboardingToggle(step)}
              >
                {onboardingBusyKey === step.key ? 'Updating...' : step.completed ? 'Mark incomplete' : step.action_label || 'Mark complete'}
              </Button>
            </article>
          ))}
        </div>
      </SectionCard>

      <SectionCard
        title="Launch kit templates"
        subtitle="Reusable launch kits for onboarding organizations without engineering help. Stable templates are always eligible; pilot and beta kits also require release lanes."
        actions={(
          <StatusBadge tone="neutral">
            {templatesSummary.applied_count ?? 0} applied | {formatRemaining(templatesSummary.remaining)} remaining
          </StatusBadge>
        )}
      >
        <div className="onboarding-summary-grid">
          <div className="saas-stat">
            <span>Template library</span>
            <strong>{templatesSummary.count ?? 0}</strong>
          </div>
          <div className="saas-stat">
            <span>Applied kits</span>
            <strong>{templatesSummary.applied_count ?? 0}</strong>
          </div>
          <div className="saas-stat">
            <span>Release lanes</span>
            <strong>{templatesSummary.release_channels_enabled ? 'Enabled' : 'Stable only'}</strong>
          </div>
        </div>

        <div className="feature-flag-grid">
          {(templatesSummary.items || []).map((template) => (
            <article key={template.key} className={`feature-flag-card ${template.is_applied ? 'feature-flag-card--override' : ''}`}>
              <div className="feature-flag-card__header">
                <div className="entitlement-item__copy">
                  <div className="entitlement-item__title-row">
                    <h3>{template.name}</h3>
                    <StatusBadge tone={template.is_applied ? 'positive' : template.available ? 'neutral' : 'negative'}>
                      {template.is_applied ? 'Applied' : template.lane}
                    </StatusBadge>
                  </div>
                  <p>{template.description}</p>
                </div>
                <div className="feature-flag-card__badges">
                  <StatusBadge tone="neutral">{template.page}</StatusBadge>
                  <StatusBadge tone="neutral">{template.release_lane_required ? 'Release lane' : 'Stable'}</StatusBadge>
                </div>
              </div>

              <div className="feature-flag-card__meta">
                <span>Lane available: {template.release_lane_available ? 'Yes' : 'No'}</span>
                <span>Preset: {template.workspace_name || '--'}</span>
                <span>Applied at: {formatDateTime(template.applied_at)}</span>
                <span>Tags: {(template.tags || []).join(', ') || '--'}</span>
              </div>

              <div className="feature-flag-card__actions">
                <Button
                  type="button"
                  variant="ghost"
                  size="sm"
                  disabled={!canManageOnboarding || !template.available || template.is_applied || templateBusyKey === template.key}
                  onClick={() => handleApplyTemplate(template)}
                >
                  {templateBusyKey === template.key ? 'Applying...' : template.is_applied ? 'Already applied' : 'Apply template'}
                </Button>
              </div>
            </article>
          ))}
        </div>

        <div className="form-hint">
          {templatesSummary.enabled
            ? 'Templates save real organization-scoped presets and audit events. Use them to standardize pilots and white-label launch kits.'
            : 'Onboarding templates are not enabled for this organization yet. Turn the flag on or move the organization to a plan that includes launch kits.'}
        </div>
      </SectionCard>

      <SectionCard
        title="Organization analytics"
        subtitle="Activation and rollout telemetry for the current organization so launch readiness is visible without digging through logs."
        actions={(
          <StatusBadge tone="neutral">
            {analyticsSummary?.activation_stage || 'Provisioning'}
          </StatusBadge>
        )}
      >
        <div className="analytics-grid">
          <article className="support-card">
            <div className="organization-item__title-row">
              <h3>Activation metrics</h3>
              <StatusBadge tone="neutral">
                {analyticsSummary?.adoption_score ?? 0}/100
              </StatusBadge>
            </div>
            <div className="analytics-stat-grid">
              <div>
                <span>Members</span>
                <strong>{analyticsSummary?.member_count ?? 0}</strong>
              </div>
              <div>
                <span>Presets</span>
                <strong>{analyticsSummary?.workspace_count ?? 0}</strong>
              </div>
              <div>
                <span>Recent events</span>
                <strong>{analyticsSummary?.recent_activity_count ?? 0}</strong>
              </div>
              <div>
                <span>Enabled flags</span>
                <strong>{analyticsSummary?.enabled_flag_count ?? 0}</strong>
              </div>
              <div>
                <span>Overrides</span>
                <strong>{analyticsSummary?.override_count ?? 0}</strong>
              </div>
              <div>
                <span>Last activity</span>
                <strong>{formatDateTime(analyticsSummary?.last_activity_at)}</strong>
              </div>
              <div>
                <span>Launch path</span>
                <strong>{analyticsSnapshot?.launch_ops?.enabled ? 'White-label' : 'Standard'}</strong>
              </div>
              <div>
                <span>Launch ready</span>
                <strong>{analyticsSummary?.launch_ready === false ? 'Blocked' : 'Ready'}</strong>
              </div>
              <div>
                <span>Last ready</span>
                <strong>{formatDateTime(analyticsSummary?.last_ready_at)}</strong>
              </div>
              <div>
                <span>Last issue</span>
                <strong>{formatDateTime(analyticsSummary?.last_failed_at)}</strong>
              </div>
            </div>
          </article>

          <article className="support-card">
            <div className="organization-item__title-row">
              <h3>Rollout funnel</h3>
              <StatusBadge tone="neutral">
                {analyticsSummary?.rollout_readiness ?? 0}% ready
              </StatusBadge>
            </div>
            <div className="analytics-funnel">
              {(analyticsSnapshot?.rollout_funnel || []).map((item) => (
                <div key={item.key} className={`analytics-funnel__item ${item.complete ? 'analytics-funnel__item--complete' : ''}`}>
                  <strong>{item.label}</strong>
                  <span>{item.complete ? 'Complete' : 'Pending'}</span>
                </div>
              ))}
            </div>
            {analyticsSnapshot?.launch_ops?.blockers?.length ? (
              <FeedbackState
                compact
                tone="negative"
                eyebrow="Desk setup"
                title="Launch blockers active"
                description={(analyticsSnapshot.launch_ops.blockers || []).join(' ')}
                actions={[{ label: 'Refresh control plane', onAction: handleRefreshSaasSurface, variant: 'ghost' }]}
                role="alert"
              />
            ) : null}
          </article>
        </div>

        <div className="support-timeline">
          {(analyticsSnapshot?.recent_activity?.items || []).map((event) => (
            <article key={event.id} className="support-event">
              <div className="support-event__header">
                <strong>{event.event_type}</strong>
                <span>{formatDateTime(event.created_at)}</span>
              </div>
              <p>{event.actor_email || 'System event'}</p>
              <code>{JSON.stringify(event.payload || {})}</code>
            </article>
          ))}
          {!(analyticsSnapshot?.recent_activity?.items || []).length ? (
            <EmptyState
              title="No organization activity"
              description="No organization activity has been recorded yet."
            />
          ) : null}
        </div>
      </SectionCard>

      <SectionCard
        title="Organizations"
        subtitle="Create pilot organizations and switch the active organization without leaving the app."
      >
        <div className="organization-list">
          {organizations.items.map((organization) => {
            const isActive = organization.slug === activeOrganization?.slug
            return (
              <article
                key={organization.id || organization.slug}
                className={`organization-item ${isActive ? 'organization-item--active' : ''}`}
              >
                <div className="organization-item__body">
                  <div className="organization-item__title-row">
                    <h3>{organization.name}</h3>
                    <StatusBadge tone={isActive ? 'positive' : 'neutral'}>
                      {isActive ? 'Active' : organization.plan_key}
                    </StatusBadge>
                  </div>
                  <p>{organization.slug} | {organization.billing_email || 'No billing email'}</p>
                  <div className="organization-item__meta">
                    <span>{organization.subscription?.status || 'active'} subscription</span>
                    <span>{organization.membership_role || 'owner'} role</span>
                  </div>
                </div>
                <Button
                  type="button"
                  variant="ghost"
                  size="sm"
                  disabled={isActive || organizationBusyKey === organization.slug}
                  onClick={() => handleActivateOrganization(organization.slug)}
                >
                  {isActive ? 'Current organization' : organizationBusyKey === organization.slug ? 'Switching...' : 'Switch organization'}
                </Button>
              </article>
            )
          })}
        </div>

        <form className="analysis-form analysis-form--wide" onSubmit={handleCreateOrganization}>
          <TextField
            label="Organization name"
            hint="Use the organization name shown in the control plane and branded shell."
            error={organizationFormErrors.name}
            required
            value={organizationForm.name}
            onChange={(event) => {
              setOrganizationForm((current) => ({ ...current, name: event.target.value }))
              setOrganizationFormErrors((current) => omitKeys(current, ['name']))
            }}
            placeholder="New organization name"
            disabled={!canCreateOrganization || organizationBusyKey === 'create'}
          />
          <SelectField
            label="Plan"
            hint="Pick the commercial lane this organization should start in."
            value={organizationForm.planKey}
            onChange={(event) => setOrganizationForm((current) => ({ ...current, planKey: event.target.value }))}
            disabled={!canCreateOrganization || organizationBusyKey === 'create'}
          >
            {plans.items.map((plan) => (
              <option key={plan.key} value={plan.key}>{plan.name}</option>
            ))}
          </SelectField>
          <TextField
            label="Billing email"
            hint="Optional billing or admin address for invoices and plan follow-up."
            error={organizationFormErrors.billingEmail}
            value={organizationForm.billingEmail}
            onChange={(event) => {
              setOrganizationForm((current) => ({ ...current, billingEmail: event.target.value }))
              setOrganizationFormErrors((current) => omitKeys(current, ['billingEmail']))
            }}
            placeholder="Billing email (optional)"
            type="email"
            disabled={!canCreateOrganization || organizationBusyKey === 'create'}
          />
          <Button type="submit" variant="solid" disabled={!canCreateOrganization || organizationBusyKey === 'create'}>
            {organizationBusyKey === 'create' ? 'Creating...' : 'Create organization'}
          </Button>
        </form>
      </SectionCard>

      <SectionCard
        title="Organization branding"
        subtitle="White-label the active organization with brand copy, support links, and a custom color system."
      >
        <div className="branding-layout">
          <article className="brand-preview-card">
            <div className="brand-preview-card__header">
              <div className="brand-preview-card__mark" aria-hidden="true">
                {brandingForm.logoUrl ? <img src={brandingForm.logoUrl} alt="" /> : <span>{(brandingForm.appName || brandingForm.name || 'TD').slice(0, 2).toUpperCase()}</span>}
              </div>
              <div>
          <Kicker as="div">{brandingForm.name || activeOrganization?.name || 'Active organization'}</Kicker>
                <h3>{brandingForm.appName || 'Organization app name'}</h3>
                <p>{brandingForm.appTagline || 'Add a brand-specific tagline so pilots feel like their own product from the first login.'}</p>
              </div>
            </div>
            <div className="brand-preview-card__palette">
              {[
                { label: 'Accent', value: brandingForm.accentPrimary },
                { label: 'Accent alt', value: brandingForm.accentSecondary },
                { label: 'Background', value: brandingForm.backgroundColor },
                { label: 'Surface', value: brandingForm.surfaceColor },
                { label: 'Text', value: brandingForm.textColor },
              ].map((swatch) => (
                <div key={swatch.label} className="brand-preview-swatch">
                  <span>{swatch.label}</span>
                  <div style={{ backgroundColor: swatch.value }} />
                  <strong>{swatch.value}</strong>
                </div>
              ))}
            </div>
            <div className="brand-preview-card__meta">
              <span>{brandingForm.supportEmail || 'No support email yet'}</span>
              <span>{brandingForm.supportUrl || 'No support URL yet'}</span>
            </div>
          </article>

          <form className="branding-form" onSubmit={handleBrandingSave}>
            <div className="analysis-form analysis-form--wide branding-form__row">
              <TextField
                label="Organization display name"
                hint="Used in the shell, billing context, and branded organization surfaces."
                error={brandingFormErrors.name}
                required
                value={brandingForm.name}
                onChange={(event) => {
                  setBrandingForm((current) => ({ ...current, name: event.target.value }))
                  setBrandingFormErrors((current) => omitKeys(current, ['name']))
                }}
                placeholder="Organization display name"
                disabled={!brandingEnabled || !canManageBranding || brandingBusy}
              />
              <TextField
                label="App name"
                hint="Name shown inside the branded organization application."
                value={brandingForm.appName}
                onChange={(event) => setBrandingForm((current) => ({ ...current, appName: event.target.value }))}
                placeholder="App name"
                disabled={!brandingEnabled || !canManageBranding || brandingBusy}
              />
              <TextField
                label="Billing email"
                hint="Optional organization billing or operations inbox."
                error={brandingFormErrors.billingEmail}
                value={brandingForm.billingEmail}
                onChange={(event) => {
                  setBrandingForm((current) => ({ ...current, billingEmail: event.target.value }))
                  setBrandingFormErrors((current) => omitKeys(current, ['billingEmail']))
                }}
                placeholder="Billing email"
                type="email"
                disabled={!brandingEnabled || !canManageBranding || brandingBusy}
              />
              <Button type="submit" variant="solid" disabled={!brandingEnabled || !canManageBranding || brandingBusy}>
                {brandingBusy ? 'Saving...' : 'Save branding'}
              </Button>
            </div>

            <div className="analysis-form analysis-form--wide branding-form__row">
              <TextField
                label="Logo URL"
                hint="Optional hosted image used in the organization shell and launch surfaces."
                error={brandingFormErrors.logoUrl}
                value={brandingForm.logoUrl}
                onChange={(event) => {
                  setBrandingForm((current) => ({ ...current, logoUrl: event.target.value }))
                  setBrandingFormErrors((current) => omitKeys(current, ['logoUrl']))
                }}
                placeholder="Logo URL"
                disabled={!brandingEnabled || !canManageBranding || brandingBusy}
              />
              <TextField
                label="Support email"
                hint="Inbox shown to organization operators when they need help."
                error={brandingFormErrors.supportEmail}
                value={brandingForm.supportEmail}
                onChange={(event) => {
                  setBrandingForm((current) => ({ ...current, supportEmail: event.target.value }))
                  setBrandingFormErrors((current) => omitKeys(current, ['supportEmail']))
                }}
                placeholder="Support email"
                type="email"
                disabled={!brandingEnabled || !canManageBranding || brandingBusy}
              />
              <TextField
                label="Support URL"
                hint="Optional support portal or knowledge-base entry point."
                error={brandingFormErrors.supportUrl}
                value={brandingForm.supportUrl}
                onChange={(event) => {
                  setBrandingForm((current) => ({ ...current, supportUrl: event.target.value }))
                  setBrandingFormErrors((current) => omitKeys(current, ['supportUrl']))
                }}
                placeholder="Support URL"
                disabled={!brandingEnabled || !canManageBranding || brandingBusy}
              />
              <TextField
                label="Brand tagline"
                hint="Short positioning line used in previews and branded onboarding."
                value={brandingForm.appTagline}
                onChange={(event) => setBrandingForm((current) => ({ ...current, appTagline: event.target.value }))}
                placeholder="Brand tagline"
                disabled={!brandingEnabled || !canManageBranding || brandingBusy}
              />
            </div>

            <div className="branding-color-grid">
              {[
                ['accentPrimary', 'Primary accent'],
                ['accentSecondary', 'Secondary accent'],
                ['backgroundColor', 'Background'],
                ['surfaceColor', 'Surface'],
                ['textColor', 'Text'],
              ].map(([key, label]) => (
                <div key={key} className="branding-color-field">
                  <span>{label}</span>
                  <div>
                    <TextField
                      ariaLabel={`${label} swatch`}
                      type="color"
                      value={brandingForm[key]}
                      className="branding-color-field__control"
                      inputClassName="branding-color-field__swatch"
                      onChange={(event) => {
                        setBrandingForm((current) => ({ ...current, [key]: event.target.value.toUpperCase() }))
                        setBrandingFormErrors((current) => omitKeys(current, [key]))
                      }}
                      disabled={!brandingEnabled || !canManageBranding || brandingBusy}
                    />
                    <TextField
                      label={`${label} hex`}
                      hint="Six-digit hex value."
                      error={brandingFormErrors[key]}
                      value={brandingForm[key]}
                      className="branding-color-field__control"
                      inputClassName="branding-color-field__hex"
                      onChange={(event) => {
                        setBrandingForm((current) => ({ ...current, [key]: event.target.value.toUpperCase() }))
                        setBrandingFormErrors((current) => omitKeys(current, [key]))
                      }}
                      disabled={!brandingEnabled || !canManageBranding || brandingBusy}
                    />
                  </div>
                </div>
              ))}
            </div>

            <div className="form-hint">
              {brandingEnabled
                ? canManageBranding
                  ? 'Branding updates apply immediately to the active organization shell and persist across organization switches.'
                  : 'Your current role can view organization branding but cannot change it.'
                : 'Organization branding is disabled on this plan. Upgrade to Pro or higher to unlock white-label controls.'}
            </div>
          </form>
        </div>
      </SectionCard>

      <SectionCard
        title="Domain & sender delivery"
        subtitle="Scaffold organization domains and branded sender identity so white-label pilots can move toward production delivery."
        actions={(
          <>
            <StatusBadge tone={customDomainDelivery.configured ? 'positive' : 'neutral'}>
              {customDomainDelivery.configured ? customDomainDelivery.domain_status || 'configured' : 'domain draft'}
            </StatusBadge>
            <StatusBadge tone={brandedEmailDelivery.configured ? 'positive' : 'neutral'}>
              {brandedEmailDelivery.configured ? 'sender ready' : 'sender draft'}
            </StatusBadge>
          </>
        )}
      >
        <div className="delivery-layout">
          <article className="support-card">
            <div className="organization-item__title-row">
              <h3>Delivery readiness</h3>
              <StatusBadge tone="neutral">
                {customDomainDelivery.primary_domain || brandedEmailDelivery.preview_from || 'Not configured'}
              </StatusBadge>
            </div>
            <div className="support-card__stats">
              <div><span>Primary domain</span><strong>{customDomainDelivery.primary_domain || '--'}</strong></div>
              <div><span>Secondary domains</span><strong>{customDomainDelivery.secondary_domains?.length ?? 0}</strong></div>
              <div><span>Sender</span><strong>{brandedEmailDelivery.preview_from || '--'}</strong></div>
              <div><span>Mail-from</span><strong>{brandedEmailDelivery.mail_from_domain || '--'}</strong></div>
            </div>
            <div className="support-card__stats">
              <div><span>Auth org</span><strong>{authRoutingDelivery.organization_hint || '--'}</strong></div>
              <div><span>SSO connection</span><strong>{authRoutingDelivery.connection_hint || '--'}</strong></div>
              <div><span>Email domain</span><strong>{authRoutingDelivery.email_domain_hint || '--'}</strong></div>
              <div><span>Enabled providers</span><strong>{(authRoutingDelivery.enabled_providers || []).join(', ') || 'All available'}</strong></div>
              <div><span>Provider records</span><strong>{authRoutingDelivery.provider_record_count ?? 0}</strong></div>
              <div><span>Mapped domains</span><strong>{authRoutingDelivery.provider_domain_count ?? 0}</strong></div>
              <div><span>Auth policy</span><strong>{(authRoutingDelivery.auth_policy || 'default').replace(/_/g, ' ')}</strong></div>
              <div><span>Preferred provider</span><strong>{authRoutingDelivery.preferred_provider || '--'}</strong></div>
              <div><span>Login entry</span><strong>{authRoutingDelivery.entry_path || '--'}</strong></div>
              <div><span>Post-login path</span><strong>{authRoutingDelivery.post_login_path || '--'}</strong></div>
              <div><span>Launch ready</span><strong>{authRoutingDelivery.launch_ready === false ? 'Blocked' : 'Ready'}</strong></div>
              <div><span>Last ready</span><strong>{authRoutingDelivery.last_ready_at ? formatDateTime(authRoutingDelivery.last_ready_at) : '--'}</strong></div>
              <div><span>Last issue</span><strong>{authRoutingDelivery.last_failed_at ? formatDateTime(authRoutingDelivery.last_failed_at) : '--'}</strong></div>
            </div>
            {authRoutingDelivery.launch_blockers?.length ? (
              <FeedbackState
                compact
                tone="negative"
                eyebrow="Delivery auth"
                title="Auth launch blockers active"
                description={(authRoutingDelivery.launch_blockers || []).join(' ')}
                actions={[{ label: 'Refresh control plane', onAction: handleRefreshSaasSurface, variant: 'ghost' }]}
                role="alert"
              />
            ) : null}
            <div className="delivery-verification">
              <div>
                <span>Verification host</span>
                <strong>{customDomainDelivery.verification_host || '--'}</strong>
              </div>
              <div>
                <span>Verification value</span>
                <code>{customDomainDelivery.verification_value || '--'}</code>
              </div>
              <div>
                <span>Provider</span>
                <strong>{brandedEmailDelivery.provider_label || 'Not configured'}</strong>
              </div>
              <div>
                <span>Release lane</span>
                <strong>{(brandedEmailDelivery.release_channel || 'stable').toUpperCase()}</strong>
              </div>
            </div>
            <div className="delivery-action-grid">
              <Button
                type="button"
                variant="ghost"
                size="sm"
                disabled={!canManageDelivery || !customDomainDelivery.actions?.request_verification || deliveryActionBusyKey === 'request_verification'}
                onClick={() => handleDeliveryAction('request_verification', 'Domain verification requested.')}
              >
                {deliveryActionBusyKey === 'request_verification' ? 'Running...' : 'Request verification'}
              </Button>
              <Button
                type="button"
                variant="ghost"
                size="sm"
                disabled={!canManageDelivery || !customDomainDelivery.actions?.mark_verified || deliveryActionBusyKey === 'mark_verified'}
                onClick={() => handleDeliveryAction('mark_verified', 'Domain marked as verified.')}
              >
                {deliveryActionBusyKey === 'mark_verified' ? 'Running...' : 'Mark verified'}
              </Button>
              <Button
                type="button"
                variant="ghost"
                size="sm"
                disabled={!canManageDelivery || !customDomainDelivery.actions?.activate_live || deliveryActionBusyKey === 'activate_live' || (authRoutingDelivery.configured && authRoutingDelivery.launch_ready === false)}
                onClick={() => handleDeliveryAction('activate_live', 'Domain and sender stack promoted live.')}
              >
                {deliveryActionBusyKey === 'activate_live' ? 'Running...' : 'Go live'}
              </Button>
              <Button
                type="button"
                variant="ghost"
                size="sm"
                disabled={!canManageDelivery || !brandedEmailDelivery.actions?.send_test || deliveryActionBusyKey === 'send_test'}
                onClick={() => handleDeliveryAction('send_test', 'Test delivery recorded for this organization.')}
              >
                {deliveryActionBusyKey === 'send_test' ? 'Running...' : 'Send test'}
              </Button>
            </div>
            <div className="delivery-checklists">
              <div>
                  <Kicker>Domain checklist</Kicker>
                <div className="delivery-checklist">
                  {(customDomainDelivery.checklist || []).map((item) => (
                    <div key={item.key} className={`delivery-checklist__item ${item.complete ? 'delivery-checklist__item--complete' : ''}`}>
                      <strong>{item.label}</strong>
                      <span>{item.detail}</span>
                    </div>
                  ))}
                </div>
              </div>
              <div>
                  <Kicker>Sender checklist</Kicker>
                <div className="delivery-checklist">
                  {(brandedEmailDelivery.checklist || []).map((item) => (
                    <div key={item.key} className={`delivery-checklist__item ${item.complete ? 'delivery-checklist__item--complete' : ''}`}>
                      <strong>{item.label}</strong>
                      <span>{item.detail}</span>
                    </div>
                  ))}
                </div>
              </div>
            </div>
            <div className="delivery-records">
                  <Kicker>Recommended DNS records</Kicker>
              {(customDomainDelivery.dns_records || []).length ? (
                (customDomainDelivery.dns_records || []).map((record) => (
                  <div key={`${record.type}-${record.host}`} className="delivery-record">
                    <strong>{record.type}</strong>
                    <span>{record.host}</span>
                    <code>{record.value}</code>
                    <small>{record.purpose}</small>
                  </div>
                ))
              ) : (
                <EmptyState
                  title="No DNS records yet"
                  description="Start here by adding a primary domain. This section will generate the verification and sender DNS records."
                  actionLabel="Configure delivery"
                  onAction={() => scrollToSettingsForm('settings-delivery-form')}
                />
              )}
            </div>
            <div className="delivery-records">
                  <Kicker>Recent auth operations</Kicker>
              {(authRoutingDelivery.recent_operations || []).length ? (
                (authRoutingDelivery.recent_operations || []).map((item) => (
                  <div key={`${item.provider_id || item.provider_key}-${item.checked_at}-${item.event}`} className="delivery-record">
                    <strong>{item.provider_label || item.provider_key || 'Provider'}</strong>
                    <span>{formatInlineMeta([formatAuthOperationLabel(item.event), String(item.target || 'live').replace(/_/g, ' ')])}</span>
                    <code>{item.status || 'unchecked'}</code>
                    <small>{item.message || 'No details recorded.'}</small>
                    <small>{item.checked_at ? formatDateTime(item.checked_at) : 'Unknown time'}</small>
                  </div>
                ))
              ) : (
                <EmptyState
                  title="No auth operations yet"
                  description="Validate an organization auth provider to start building an SSO operations history."
                />
              )}
            </div>
          </article>

          <form id="settings-delivery-form" tabIndex={-1} className="branding-form" onSubmit={handleDeliverySave}>
            <div className="analysis-form analysis-form--wide branding-form__row">
              <TextField
                label="Primary domain"
                hint="Main branded domain used for verification and launch routing."
                error={deliveryFormErrors.primaryDomain}
                value={deliveryForm.primaryDomain}
                onChange={(event) => {
                  setDeliveryForm((current) => ({ ...current, primaryDomain: event.target.value }))
                  setDeliveryFormErrors((current) => omitKeys(current, ['primaryDomain']))
                }}
                placeholder="Primary domain"
                disabled={!customDomainsEnabled || !canManageDelivery || deliveryBusy}
              />
              <TextField
                label="Secondary domains"
                hint="Optional fallback or alias domains, comma separated."
                error={deliveryFormErrors.secondaryDomains}
                value={deliveryForm.secondaryDomains}
                onChange={(event) => {
                  setDeliveryForm((current) => ({ ...current, secondaryDomains: event.target.value }))
                  setDeliveryFormErrors((current) => omitKeys(current, ['secondaryDomains']))
                }}
                placeholder="Secondary domains (comma separated)"
                disabled={!customDomainsEnabled || !canManageDelivery || deliveryBusy}
              />
              <SelectField
                label="Domain status"
                hint="Current launch state for branded domains."
                value={deliveryForm.domainStatus}
                onChange={(event) => setDeliveryForm((current) => ({ ...current, domainStatus: event.target.value }))}
                disabled={!customDomainsEnabled || !canManageDelivery || deliveryBusy}
              >
                <option value="draft">Draft</option>
                <option value="pending_verification">Pending verification</option>
                <option value="verified">Verified</option>
                <option value="live">Live</option>
              </SelectField>
              <Button type="submit" variant="solid" disabled={!canManageDelivery || deliveryBusy || (!customDomainsEnabled && !brandedEmailEnabled)}>
                {deliveryBusy ? 'Saving...' : 'Save delivery'}
              </Button>
            </div>

            <div className="analysis-form analysis-form--wide branding-form__row">
              <SelectField
                label="Email provider"
                hint="Delivery stack used for branded email sends."
                value={deliveryForm.emailProvider}
                onChange={(event) => setDeliveryForm((current) => ({ ...current, emailProvider: event.target.value }))}
                disabled={!brandedEmailEnabled || !canManageDelivery || deliveryBusy}
              >
                <option value="none">No provider yet</option>
                <option value="resend">Resend</option>
                <option value="postmark">Postmark</option>
                <option value="sendgrid">SendGrid</option>
                <option value="ses">Amazon SES</option>
                <option value="custom-smtp">Custom SMTP</option>
              </SelectField>
              <SelectField
                label="Provider status"
                hint="Readiness of the current sender integration."
                value={deliveryForm.providerStatus}
                onChange={(event) => setDeliveryForm((current) => ({ ...current, providerStatus: event.target.value }))}
                disabled={!brandedEmailEnabled || !canManageDelivery || deliveryBusy}
              >
                <option value="draft">Provider draft</option>
                <option value="configured">Configured</option>
                <option value="ready">Ready</option>
                <option value="live">Live</option>
              </SelectField>
              <TextField
                label="Template set"
                hint="Optional email template family for this organization."
                value={deliveryForm.templateSetName}
                onChange={(event) => setDeliveryForm((current) => ({ ...current, templateSetName: event.target.value }))}
                placeholder="Template set"
                disabled={!brandedEmailEnabled || !canManageDelivery || deliveryBusy}
              />
              <SelectField
                label="Release channel"
                hint="Lane used for rollout-sensitive delivery changes."
                value={deliveryForm.releaseChannel}
                onChange={(event) => setDeliveryForm((current) => ({ ...current, releaseChannel: event.target.value }))}
                disabled={!brandedEmailEnabled || !canManageDelivery || deliveryBusy || !releaseChannelsEnabled}
              >
                <option value="stable">Stable lane</option>
                <option value="pilot">Pilot lane</option>
                <option value="beta">Beta lane</option>
              </SelectField>
            </div>

            <div className="analysis-form analysis-form--wide branding-form__row">
              <TextField
                label="Sender name"
                hint="Friendly from-name shown in branded email."
                value={deliveryForm.senderName}
                onChange={(event) => setDeliveryForm((current) => ({ ...current, senderName: event.target.value }))}
                placeholder="Sender name"
                disabled={!brandedEmailEnabled || !canManageDelivery || deliveryBusy}
              />
              <TextField
                label="Sender email"
                hint="Primary from-address for outbound email."
                error={deliveryFormErrors.senderEmail}
                value={deliveryForm.senderEmail}
                onChange={(event) => {
                  setDeliveryForm((current) => ({ ...current, senderEmail: event.target.value }))
                  setDeliveryFormErrors((current) => omitKeys(current, ['senderEmail']))
                }}
                placeholder="Sender email"
                type="email"
                disabled={!brandedEmailEnabled || !canManageDelivery || deliveryBusy}
              />
              <TextField
                label="Reply-to email"
                hint="Optional inbox for replies and support callbacks."
                error={deliveryFormErrors.replyToEmail}
                value={deliveryForm.replyToEmail}
                onChange={(event) => {
                  setDeliveryForm((current) => ({ ...current, replyToEmail: event.target.value }))
                  setDeliveryFormErrors((current) => omitKeys(current, ['replyToEmail']))
                }}
                placeholder="Reply-to email"
                type="email"
                disabled={!brandedEmailEnabled || !canManageDelivery || deliveryBusy}
              />
              <TextField
                label="Mail-from subdomain"
                hint="Optional bounce and return-path subdomain."
                value={deliveryForm.mailFromSubdomain}
                onChange={(event) => setDeliveryForm((current) => ({ ...current, mailFromSubdomain: event.target.value }))}
                placeholder="Mail-from subdomain"
                disabled={!brandedEmailEnabled || !canManageDelivery || deliveryBusy}
              />
            </div>

            <div className="analysis-form analysis-form--wide branding-form__row">
              <TextAreaField
                label="Email signature"
                hint="Footer copy for branded outbound messages."
                value={deliveryForm.emailSignature}
                onChange={(event) => setDeliveryForm((current) => ({ ...current, emailSignature: event.target.value }))}
                placeholder="Email signature / footer"
                disabled={!brandedEmailEnabled || !canManageDelivery || deliveryBusy}
              />
            </div>

              <div className="analysis-form analysis-form--wide branding-form__row">
                <TextField
                  label="Auth0 organization hint"
                  hint="Organization slug or routing hint used during SSO routing."
                  value={deliveryForm.auth0Organization}
                  onChange={(event) => setDeliveryForm((current) => ({ ...current, auth0Organization: event.target.value }))}
                  placeholder="Auth0 organization hint"
                  disabled={!brandingEnabled || !canManageDelivery || deliveryBusy}
                />
                <TextField
                  label="Connection hint"
                hint="Enterprise connection or IdP key used for this organization."
                  value={deliveryForm.auth0Connection}
                  onChange={(event) => setDeliveryForm((current) => ({ ...current, auth0Connection: event.target.value }))}
                  placeholder="SSO connection / enterprise IdP"
                  disabled={!brandingEnabled || !canManageDelivery || deliveryBusy}
                />
                <TextField
                  label="Allowed email domain"
                  hint="Restrict SSO users to this domain when needed."
                  error={deliveryFormErrors.ssoEmailDomain}
                  value={deliveryForm.ssoEmailDomain}
                  onChange={(event) => {
                    setDeliveryForm((current) => ({ ...current, ssoEmailDomain: event.target.value.toLowerCase() }))
                    setDeliveryFormErrors((current) => omitKeys(current, ['ssoEmailDomain']))
                  }}
                  placeholder="Allowed email domain"
                  disabled={!brandingEnabled || !canManageDelivery || deliveryBusy}
                />
                <SelectField
                  label="Auth policy"
                hint="How strongly organization login should favor SSO."
                  value={deliveryForm.authPolicy}
                  onChange={(event) => setDeliveryForm((current) => ({ ...current, authPolicy: event.target.value }))}
                  disabled={!brandingEnabled || !canManageDelivery || deliveryBusy}
                >
                  <option value="default">Default policy</option>
                  <option value="prefer_sso">Prefer SSO</option>
                  <option value="require_sso">Require SSO</option>
                  <option value="local_only">Local only</option>
                </SelectField>
              </div>

              <div className="analysis-form analysis-form--wide branding-form__row">
                <div className="delivery-provider-toggle-group">
                  {[
                    ['local-session', 'Local session'],
                    ['auth0', 'Auth0'],
                    ['oidc', 'Enterprise OIDC'],
                  ].map(([providerKey, label]) => {
                    const enabled = deliveryForm.enabledProviders.includes(providerKey)
                    return (
                      <Button
                        key={providerKey}
                        type="button"
                        variant={enabled ? 'solid' : 'ghost'}
                        size="sm"
                        className="delivery-provider-toggle"
                        aria-pressed={enabled}
                        disabled={!brandingEnabled || !canManageDelivery || deliveryBusy}
                        onClick={() => {
                          setDeliveryForm((current) => {
                            const nextProviders = enabled
                              ? current.enabledProviders.filter((value) => value !== providerKey)
                              : [...current.enabledProviders, providerKey]
                            return {
                              ...current,
                              enabledProviders: Array.from(new Set(nextProviders)),
                            }
                          })
                        }}
                      >
                        {label}
                      </Button>
                    )
                  })}
                </div>
                <SelectField
                  label="Preferred provider"
                  hint="Default route when multiple providers are enabled."
                  value={deliveryForm.preferredProvider}
                  onChange={(event) => setDeliveryForm((current) => ({ ...current, preferredProvider: event.target.value }))}
                  disabled={!brandingEnabled || !canManageDelivery || deliveryBusy}
                >
                  <option value="default">Default provider routing</option>
                  <option value="auth0">Prefer Auth0</option>
                  <option value="oidc">Prefer Enterprise OIDC</option>
                  <option value="local-session">Prefer local fallback</option>
                </SelectField>
              </div>

            <div className="delivery-provider-catalog">
              <div className="organization-item__title-row">
              <h3>Organization provider catalog</h3>
                <div className="delivery-provider-record-card__badges">
                  <StatusBadge tone="neutral">
                    {(deliveryForm.authProviderRecords || []).length} staged
                  </StatusBadge>
                  <StatusBadge tone="positive">
                    ready {authRoutingDelivery.provider_health?.ready || 0}
                  </StatusBadge>
                  <StatusBadge tone="neutral">
                    unchecked {authRoutingDelivery.provider_health?.unchecked || 0}
                  </StatusBadge>
                  <StatusBadge tone="neutral">
                    staged {authRoutingDelivery.provider_health?.pending || 0}
                  </StatusBadge>
                  <StatusBadge tone="negative">
                    issues {(authRoutingDelivery.provider_health?.incomplete || 0) + (authRoutingDelivery.provider_health?.error || 0)}
                  </StatusBadge>
                </div>
              </div>

              {(deliveryForm.authProviderRecords || []).length ? (
                <div className="delivery-provider-records">
                  {(deliveryForm.authProviderRecords || []).map((record) => (
                    <div key={record.id || record.provider_id || `${record.provider_key}-${record.label}`} className="delivery-provider-record-card">
                      <div className="delivery-provider-record-card__header">
                        <strong>{record.label}</strong>
                        <div className="delivery-provider-record-card__badges">
                          <StatusBadge tone="neutral">{record.provider_key}</StatusBadge>
                          {record.is_default ? <StatusBadge tone="positive">default</StatusBadge> : null}
                          {record.enabled === false ? <StatusBadge tone="negative">disabled</StatusBadge> : null}
                          {record.has_pending_client_secret ? <StatusBadge tone="neutral">staged secret</StatusBadge> : null}
                          <StatusBadge
                            tone={
                              record.health_status === 'ready'
                                ? 'positive'
                                : record.health_status === 'unchecked'
                                  ? 'neutral'
                                  : 'negative'
                            }
                          >
                            {record.health_status || 'unchecked'}
                          </StatusBadge>
                        </div>
                      </div>
                      <div className="delivery-provider-record-card__meta">
                        <span>Domains: {(record.email_domains || []).join(', ') || '--'}</span>
                        <span>Org hint: {record.organization_hint || '--'}</span>
                        <span>Connection: {record.connection_hint || '--'}</span>
                        <span>{record.provider_key === 'auth0' ? `Auth0 domain: ${record.auth0_domain || '--'}` : `Issuer: ${record.issuer || '--'}`}</span>
                        <span>Client ID: {record.client_id || '--'}</span>
                        <span>
                          Runtime: {record.ready ? 'ready' : 'incomplete'}
                          {record.has_client_secret ? ' | secret saved' : ' | no secret'}
                          {record.has_pending_client_secret ? ' | staged replacement pending' : ''}
                        </span>
                        <span>Health: {record.health_message || 'Validate this provider to confirm metadata and routing readiness.'}</span>
                        {record.has_pending_client_secret ? (
                          <span>
                            Staged secret: {record.pending_health_message || 'Validate the staged secret before promoting it live.'}
                            {record.pending_last_checked_at ? ` | checked ${formatDateTime(record.pending_last_checked_at)}` : ''}
                          </span>
                        ) : null}
                        <span>
                          Discovery: {record.discovery_source || '--'}
                          {record.last_checked_at ? ` | checked ${formatDateTime(record.last_checked_at)}` : ''}
                        </span>
                        {record.pending_resolved_authorize_url ? <span>Staged authorize: {record.pending_resolved_authorize_url}</span> : null}
                        {record.resolved_authorize_url ? <span>Authorize: {record.resolved_authorize_url}</span> : null}
                        <span>Last ready: {record.last_ready_at ? formatDateTime(record.last_ready_at) : '--'}</span>
                        <span>Last issue: {record.last_failed_at ? formatDateTime(record.last_failed_at) : '--'}</span>
                        {record.config_issues?.length ? <span>Needs: {record.config_issues.join(' ')}</span> : null}
                      </div>
                      {(record.health_history || []).length ? (
                        <div className="delivery-records">
                          {(record.health_history || []).slice(0, 3).map((item, index) => (
                            <div key={`${record.id || record.provider_id}-history-${index}`} className="delivery-record">
                              <strong>{formatAuthOperationLabel(item.event)}</strong>
                              <span>{String(item.target || 'live').replace(/_/g, ' ')}</span>
                              <code>{item.status || 'unchecked'}</code>
                              <small>{item.message || 'No details recorded.'}</small>
                              <small>{item.checked_at ? formatDateTime(item.checked_at) : 'Unknown time'}</small>
                            </div>
                          ))}
                        </div>
                      ) : null}
                      <div className="delivery-provider-record-card__actions">
                        <Button
                          type="button"
                          variant="ghost"
                          size="sm"
                          onClick={() => handleDeliveryAction(
                            { action: 'validate_auth_provider', provider_id: record.id || record.provider_id },
                            `${record.label} validated.`
                          )}
                          disabled={!canManageDelivery || deliveryBusy || record.enabled === false || deliveryActionBusyKey === `validate_auth_provider:${record.id || record.provider_id}`}
                        >
                          {deliveryActionBusyKey === `validate_auth_provider:${record.id || record.provider_id}` ? 'Validating...' : record.has_pending_client_secret ? 'Validate staged secret' : 'Validate'}
                        </Button>
                        <Button
                          type="button"
                          variant="ghost"
                          size="sm"
                          onClick={() => handleDeliveryAction(
                            { action: 'promote_auth_provider_secret', provider_id: record.id || record.provider_id },
                            `${record.label} staged secret promoted live.`
                          )}
                          disabled={!canManageDelivery || deliveryBusy || !record.has_pending_client_secret || record.pending_health_status !== 'ready' || deliveryActionBusyKey === `promote_auth_provider_secret:${record.id || record.provider_id}`}
                        >
                          {deliveryActionBusyKey === `promote_auth_provider_secret:${record.id || record.provider_id}` ? 'Promoting...' : 'Promote staged secret'}
                        </Button>
                        <Button
                          type="button"
                          variant="ghost"
                          size="sm"
                          onClick={() => handleDeliveryAction(
                            { action: 'discard_auth_provider_secret', provider_id: record.id || record.provider_id },
                            `${record.label} staged secret discarded.`
                          )}
                          disabled={!canManageDelivery || deliveryBusy || !record.has_pending_client_secret || deliveryActionBusyKey === `discard_auth_provider_secret:${record.id || record.provider_id}`}
                        >
                          {deliveryActionBusyKey === `discard_auth_provider_secret:${record.id || record.provider_id}` ? 'Discarding...' : 'Discard staged secret'}
                        </Button>
                        <Button
                          type="button"
                          variant="ghost"
                          size="sm"
                          onClick={() => handleDeliveryAction(
                            { action: 'rotate_auth_provider_secret', provider_id: record.id || record.provider_id },
                            `${record.label} secret cleared.`
                          )}
                          disabled={!canManageDelivery || deliveryBusy || !record.has_client_secret || deliveryActionBusyKey === `rotate_auth_provider_secret:${record.id || record.provider_id}`}
                        >
                          {deliveryActionBusyKey === `rotate_auth_provider_secret:${record.id || record.provider_id}` ? 'Clearing...' : 'Clear secret'}
                        </Button>
                        <Button
                          type="button"
                          variant="ghost"
                          size="sm"
                          onClick={() => handleEditAuthProviderRecord(record)}
                          disabled={!canManageDelivery || deliveryBusy}
                        >
                          Edit
                        </Button>
                        <Button
                          type="button"
                          variant="ghost"
                          size="sm"
                          onClick={() => handleRemoveAuthProviderRecord(record.id || record.provider_id)}
                          disabled={!canManageDelivery || deliveryBusy}
                        >
                          Remove
                        </Button>
                      </div>
                    </div>
                  ))}
                </div>
              ) : (
                <EmptyState
                  title="No staged SSO records"
                description="No organization-specific SSO records are staged yet. Add one below to map an enterprise IdP to email domains and routing hints."
                />
              )}

              <div className="analysis-form analysis-form--wide branding-form__row">
                <SelectField
                  label="Provider type"
                hint="Choose the identity provider you are staging into the organization catalog."
                  value={authProviderDraft.providerKey}
                  onChange={(event) => {
                    setAuthProviderDraft((current) => ({ ...current, providerKey: event.target.value }))
                    setAuthProviderDraftErrors((current) => omitKeys(current, ['auth0Domain', 'issuer', 'authorizeUrl', 'tokenUrl', 'userinfoUrl', 'logoutUrl']))
                  }}
                  disabled={!brandingEnabled || !canManageDelivery || deliveryBusy}
                >
                  <option value="auth0">Auth0</option>
                  <option value="oidc">Enterprise OIDC</option>
                </SelectField>
                <TextField
                  label="Provider label"
                hint="Name shown to operators in the organization provider catalog."
                  error={authProviderDraftErrors.label}
                  required
                  value={authProviderDraft.label}
                  onChange={(event) => {
                    setAuthProviderDraft((current) => ({ ...current, label: event.target.value }))
                    setAuthProviderDraftErrors((current) => omitKeys(current, ['label']))
                  }}
                  placeholder="Provider label"
                  disabled={!brandingEnabled || !canManageDelivery || deliveryBusy}
                />
                <TextField
                  label="Email domains"
                  hint="Comma-separated domains that should route into this provider."
                  error={authProviderDraftErrors.emailDomains}
                  value={authProviderDraft.emailDomains}
                  onChange={(event) => {
                    setAuthProviderDraft((current) => ({ ...current, emailDomains: event.target.value.toLowerCase() }))
                    setAuthProviderDraftErrors((current) => omitKeys(current, ['emailDomains']))
                  }}
                  placeholder="Email domains (comma separated)"
                  disabled={!brandingEnabled || !canManageDelivery || deliveryBusy}
                />
              </div>

              <div className="analysis-form analysis-form--wide branding-form__row">
                <TextField
                  label="Organization hint"
                hint="Organization-specific org hint used by the provider."
                  value={authProviderDraft.organizationHint}
                  onChange={(event) => setAuthProviderDraft((current) => ({ ...current, organizationHint: event.target.value }))}
                  placeholder="Organization hint"
                  disabled={!brandingEnabled || !canManageDelivery || deliveryBusy}
                />
                <TextField
                  label="Connection hint"
                  hint="Connection or enterprise IdP mapping hint."
                  value={authProviderDraft.connectionHint}
                  onChange={(event) => setAuthProviderDraft((current) => ({ ...current, connectionHint: event.target.value }))}
                  placeholder="Connection hint"
                  disabled={!brandingEnabled || !canManageDelivery || deliveryBusy}
                />
                {authProviderDraft.providerKey === 'auth0' ? (
                  <TextField
                    label="Auth0 domain"
                hint="Organization Auth0 domain, for example org.us.auth0.com."
                    error={authProviderDraftErrors.auth0Domain}
                    value={authProviderDraft.auth0Domain}
                    onChange={(event) => {
                      setAuthProviderDraft((current) => ({ ...current, auth0Domain: event.target.value }))
                      setAuthProviderDraftErrors((current) => omitKeys(current, ['auth0Domain']))
                    }}
                    placeholder="Auth0 domain"
                    disabled={!brandingEnabled || !canManageDelivery || deliveryBusy}
                  />
                ) : (
                  <TextField
                    label="OIDC issuer"
                    hint="Issuer URL used to discover or validate the provider."
                    error={authProviderDraftErrors.issuer}
                    value={authProviderDraft.issuer}
                    onChange={(event) => {
                      setAuthProviderDraft((current) => ({ ...current, issuer: event.target.value }))
                      setAuthProviderDraftErrors((current) => omitKeys(current, ['issuer']))
                    }}
                    placeholder="OIDC issuer"
                    disabled={!brandingEnabled || !canManageDelivery || deliveryBusy}
                  />
                )}
                <TextField
                  label="Client ID"
                hint="Application client identifier for the organization provider."
                  value={authProviderDraft.clientId}
                  onChange={(event) => setAuthProviderDraft((current) => ({ ...current, clientId: event.target.value }))}
                  placeholder="Client ID"
                  disabled={!brandingEnabled || !canManageDelivery || deliveryBusy}
                />
                <TextField
                  label="Client secret"
                  hint="Leave blank to preserve an existing saved or staged secret."
                  value={authProviderDraft.clientSecret}
                  onChange={(event) => setAuthProviderDraft((current) => ({ ...current, clientSecret: event.target.value }))}
                  placeholder={
                    authProviderDraft.hasPendingClientSecret
                      ? 'Client secret (leave blank to keep staged secret)'
                      : authProviderDraft.hasClientSecret
                        ? 'Client secret (leave blank to keep saved secret)'
                        : 'Client secret'
                  }
                  type="password"
                  disabled={!brandingEnabled || !canManageDelivery || deliveryBusy}
                />
              </div>

              {authProviderDraft.providerKey === 'oidc' ? (
                <div className="analysis-form analysis-form--wide branding-form__row">
                  <TextField
                    label="Authorize URL"
                    hint="Authorization endpoint for the OIDC provider."
                    error={authProviderDraftErrors.authorizeUrl}
                    value={authProviderDraft.authorizeUrl}
                    onChange={(event) => {
                      setAuthProviderDraft((current) => ({ ...current, authorizeUrl: event.target.value }))
                      setAuthProviderDraftErrors((current) => omitKeys(current, ['authorizeUrl']))
                    }}
                    placeholder="Authorize URL"
                    disabled={!brandingEnabled || !canManageDelivery || deliveryBusy}
                  />
                  <TextField
                    label="Token URL"
                hint="Token endpoint used by the organization app."
                    error={authProviderDraftErrors.tokenUrl}
                    value={authProviderDraft.tokenUrl}
                    onChange={(event) => {
                      setAuthProviderDraft((current) => ({ ...current, tokenUrl: event.target.value }))
                      setAuthProviderDraftErrors((current) => omitKeys(current, ['tokenUrl']))
                    }}
                    placeholder="Token URL"
                    disabled={!brandingEnabled || !canManageDelivery || deliveryBusy}
                  />
                  <TextField
                    label="Userinfo URL"
                    hint="Optional endpoint for profile lookup."
                    error={authProviderDraftErrors.userinfoUrl}
                    value={authProviderDraft.userinfoUrl}
                    onChange={(event) => {
                      setAuthProviderDraft((current) => ({ ...current, userinfoUrl: event.target.value }))
                      setAuthProviderDraftErrors((current) => omitKeys(current, ['userinfoUrl']))
                    }}
                    placeholder="Userinfo URL"
                    disabled={!brandingEnabled || !canManageDelivery || deliveryBusy}
                  />
                  <TextField
                    label="Logout URL"
                    hint="Optional single-logout endpoint."
                    error={authProviderDraftErrors.logoutUrl}
                    value={authProviderDraft.logoutUrl}
                    onChange={(event) => {
                      setAuthProviderDraft((current) => ({ ...current, logoutUrl: event.target.value }))
                      setAuthProviderDraftErrors((current) => omitKeys(current, ['logoutUrl']))
                    }}
                    placeholder="Logout URL"
                    disabled={!brandingEnabled || !canManageDelivery || deliveryBusy}
                  />
                </div>
              ) : null}

              <div className="analysis-form analysis-form--wide branding-form__row">
                <TextField
                  label="Audience"
                  hint="Optional API audience for OIDC-based providers."
                  value={authProviderDraft.audience}
                  onChange={(event) => setAuthProviderDraft((current) => ({ ...current, audience: event.target.value }))}
                  placeholder="Audience (optional)"
                  disabled={!brandingEnabled || !canManageDelivery || deliveryBusy}
                />
                <TextField
                  label="Scope"
                  hint="Defaults to openid profile email when left blank."
                  value={authProviderDraft.scope}
                  onChange={(event) => setAuthProviderDraft((current) => ({ ...current, scope: event.target.value }))}
                  placeholder="Scope (defaults to openid profile email)"
                  disabled={!brandingEnabled || !canManageDelivery || deliveryBusy}
                />
                <ToggleField
                  label="Allow self-serve signup"
                  checked={authProviderDraft.allowSignup}
                  onChange={(event) => setAuthProviderDraft((current) => ({ ...current, allowSignup: event.target.checked }))}
                  disabled={!brandingEnabled || !canManageDelivery || deliveryBusy}
                />
                <ToggleField
                  label="Enabled"
                  checked={authProviderDraft.enabled}
                  onChange={(event) => setAuthProviderDraft((current) => ({ ...current, enabled: event.target.checked }))}
                  disabled={!brandingEnabled || !canManageDelivery || deliveryBusy}
                />
                <ToggleField
                  label="Default route"
                  checked={authProviderDraft.isDefault}
                  onChange={(event) => setAuthProviderDraft((current) => ({ ...current, isDefault: event.target.checked }))}
                  disabled={!brandingEnabled || !canManageDelivery || deliveryBusy}
                />
                <Button
                  type="button"
                  variant="solid"
                  onClick={handleSaveAuthProviderRecord}
                  disabled={!brandingEnabled || !canManageDelivery || deliveryBusy}
                >
                  {authProviderDraft.providerId ? 'Update provider' : 'Add provider'}
                </Button>
                {authProviderDraft.providerId ? (
                  <Button
                    type="button"
                    variant="ghost"
                    onClick={resetAuthProviderDraft}
                    disabled={!canManageDelivery || deliveryBusy}
                  >
                    Cancel edit
                  </Button>
                ) : null}
              </div>
            </div>

            <div className="form-hint">
              {customDomainsEnabled || brandedEmailEnabled
                ? canManageDelivery
                  ? `Use this as the organization delivery control plane. Next actions: ${customDomainDelivery.next_action || 'configure the domain'} | ${brandedEmailDelivery.next_action || 'configure the sender stack'} | ${authRoutingDelivery.next_action || 'configure organization auth routing'}. Save delivery after staging provider records so login routing updates with the rest of the organization delivery stack.`
                  : 'Your current role can view delivery readiness but cannot change organization domains or sender settings.'
                : 'Custom domains and branded sender identity are not enabled for this organization yet. Turn them on in admin rollout controls or move the organization to Enterprise.'}
            </div>
          </form>
        </div>
      </SectionCard>

      <SectionCard
        title="Plan catalog"
        subtitle="Public plan positioning for the premium live trading control plane. The price is tied to controls, evidence, workflow, and support."
      >
        <div className="pricing-settings-intro">
          <div>
            <Kicker as="div">Paper-validated live automation control</Kicker>
            <h3>Price supervised operation, not commodity connectivity.</h3>
            <p>
              Starter and Pro keep live trading explicit and approval-led. Professional is the recommended $499 tier
              because it adds readiness gates, risk policy enforcement, audit replay, execution quality, and kill-switch control.
            </p>
          </div>
          <Button type="button" variant="ghost" size="sm" onClick={() => window.open('/pricing', '_blank', 'noopener,noreferrer')}>
            Open public pricing
          </Button>
        </div>

        <div className="pricing-tier-grid pricing-tier-grid--settings">
          {publicPricingPlans.map((plan) => (
            <PricingTierCard
              key={plan.key}
              plan={plan}
              billingCycle={billingCycle}
              isActive={plan.key === billingSummary?.plan?.key}
              busy={billingBusyKey === plan.key}
              disabled={!canManageBilling}
              onAction={handlePlanChange}
            />
          ))}
        </div>

        <PricingComparisonTable plans={publicPricingPlans} />
      </SectionCard>

      <SectionCard
        title="Support console"
        subtitle="Inspect organization state, recent audit events, and use admin actions during pilot support."
        actions={(
          <>
            <Button type="button" variant="ghost" size="sm" onClick={handleRefreshSaasSurface}>
              Refresh support data
            </Button>
            <Button
              type="button"
              variant="ghost"
              size="sm"
              disabled={!canManageSupport || !canChangeOrganizationStatus || !supportSnapshot?.support_actions?.can_pause || supportBusyKey === 'paused'}
              onClick={() => handleOrganizationStatusChange('paused')}
            >
              {supportBusyKey === 'paused' ? 'Pausing...' : 'Pause organization'}
            </Button>
            <Button
              type="button"
              variant="ghost"
              size="sm"
              disabled={!canManageSupport || !canChangeOrganizationStatus || !supportSnapshot?.support_actions?.can_resume || supportBusyKey === 'active'}
              onClick={() => handleOrganizationStatusChange('active')}
            >
              {supportBusyKey === 'active' ? 'Resuming...' : 'Resume organization'}
            </Button>
          </>
        )}
      >
        <div className="support-grid">
          <div className="support-card">
            <div className="organization-item__title-row">
              <h3>Organization status</h3>
              <StatusBadge tone={supportSnapshot?.status === 'paused' ? 'negative' : 'positive'}>
                {supportSnapshot?.status || activeOrganization?.status || 'active'}
              </StatusBadge>
            </div>
            <div className="support-card__stats">
              <div><span>Plan</span><strong>{supportSnapshot?.tenant?.plan_key || activeOrganization?.plan_key || 'starter'}</strong></div>
              <div><span>Members</span><strong>{supportSnapshot?.memberships?.count ?? 0}</strong></div>
              <div><span>Audit events</span><strong>{supportSnapshot?.timeline?.count ?? 0}</strong></div>
              <div><span>Provider</span><strong>{supportSnapshot?.billing?.subscription?.provider || 'internal-demo'}</strong></div>
            </div>
            {supportSnapshot?.support_actions?.resume_blockers?.length ? (
              <FeedbackState
                compact
                tone="negative"
                eyebrow="Organization status"
                title="Resume blocked"
                description={(supportSnapshot.support_actions.resume_blockers || []).join(' ')}
                actions={[{ label: 'Refresh control plane', onAction: handleRefreshSaasSurface, variant: 'ghost' }]}
                role="alert"
              />
            ) : null}
          </div>

          <div className="support-card">
            <div className="organization-item__title-row">
              <h3>Launch operations</h3>
              <StatusBadge tone={supportSnapshot?.launch_ops?.launch_ready === false ? 'negative' : 'positive'}>
                {supportSnapshot?.launch_ops?.stage || 'Standard organization'}
              </StatusBadge>
            </div>
            <div className="support-card__stats">
              <div><span>Mode</span><strong>{supportSnapshot?.launch_ops?.enabled ? 'White-label' : 'Standard'}</strong></div>
              <div><span>Release lane</span><strong>{(supportSnapshot?.launch_ops?.release_channel || 'stable').toUpperCase()}</strong></div>
              <div><span>Last ready</span><strong>{formatDateTime(supportSnapshot?.launch_ops?.last_ready_at)}</strong></div>
              <div><span>Last issue</span><strong>{formatDateTime(supportSnapshot?.launch_ops?.last_failed_at)}</strong></div>
            </div>
            {(supportSnapshot?.launch_ops?.checklist || []).length ? (
              <div className="analytics-funnel">
                {(supportSnapshot.launch_ops.checklist || []).map((item) => (
                  <div key={item.key} className={`analytics-funnel__item ${item.complete ? 'analytics-funnel__item--complete' : ''}`}>
                    <strong>{item.label}</strong>
                    <span>{item.complete ? 'Complete' : 'Pending'}</span>
                  </div>
                ))}
              </div>
            ) : (
              <EmptyState
                title="Standard launch path"
                description="This organization is using the standard internal launch path."
              />
            )}
            {supportSnapshot?.launch_ops?.blockers?.length ? (
              <FeedbackState
                compact
                tone="negative"
                eyebrow="Desk setup"
                title="Launch blockers active"
                description={(supportSnapshot.launch_ops.blockers || []).join(' ')}
                actions={[{ label: 'Refresh control plane', onAction: handleRefreshSaasSurface, variant: 'ghost' }]}
                role="alert"
              />
            ) : null}
            <div className="support-timeline">
              {launchTimeline.map((event) => (
                <article key={event.id} className="support-event">
                  <div className="support-event__header">
                    <strong>{event.event_type}</strong>
                    <span>{formatDateTime(event.created_at)}</span>
                  </div>
                  <p>{event.actor_email || 'System event'}</p>
                  <code>{JSON.stringify(event.payload || {})}</code>
                </article>
              ))}
              {!launchTimeline.length ? (
                <EmptyState
                  title="No launch events"
                  description="No launch-state events recorded yet."
                />
              ) : null}
            </div>
          </div>

          <div className="support-card">
            <div className="organization-item__title-row">
              <h3>Invite member</h3>
              <StatusBadge tone="neutral">
                {supportSnapshot?.support_actions?.can_manage_members ? 'Admin enabled' : 'Read only'}
              </StatusBadge>
            </div>
            <form className="branding-form" onSubmit={handleInviteMember}>
              <div className="analysis-form analysis-form--wide branding-form__row">
                <TextField
                  label="Invite email"
                  hint="This address receives the organization invite and auto-attach flow."
                  error={memberInviteErrors.email}
                  required
                  value={memberInviteForm.email}
                  onChange={(event) => {
                    setMemberInviteForm((current) => ({ ...current, email: event.target.value }))
                    setMemberInviteErrors((current) => omitKeys(current, ['email']))
                  }}
                  placeholder="Invite email"
                  type="email"
                  disabled={!canManageMembers || memberBusyKey === 'invite'}
                />
                <TextField
                  label="Name"
                  hint="Optional display name to prefill the membership record."
                  value={memberInviteForm.name}
                  onChange={(event) => setMemberInviteForm((current) => ({ ...current, name: event.target.value }))}
                  placeholder="Name (optional)"
                  disabled={!canManageMembers || memberBusyKey === 'invite'}
                />
                <SelectField
                  label="Role"
                  hint="Starting organization role assigned after sign-in."
                  value={memberInviteForm.role}
                  onChange={(event) => setMemberInviteForm((current) => ({ ...current, role: event.target.value }))}
                  disabled={!canManageMembers || memberBusyKey === 'invite'}
                >
                  {(supportSnapshot?.support_actions?.role_options || []).filter((role) => role.assignable).map((role) => (
                    <option key={role.key} value={role.key}>{role.label}</option>
                  ))}
                </SelectField>
                <Button type="submit" variant="solid" disabled={!canManageMembers || memberBusyKey === 'invite'}>
                  {memberBusyKey === 'invite' ? 'Inviting...' : 'Create invite'}
                </Button>
              </div>
              <div className="analysis-form analysis-form--wide branding-form__row">
                <TextField
                  label="Invite message"
                  hint="Optional internal note or operator-facing context."
                  value={memberInviteForm.message}
                  onChange={(event) => setMemberInviteForm((current) => ({ ...current, message: event.target.value }))}
                  placeholder="Internal note / invite message"
                  disabled={!canManageMembers || memberBusyKey === 'invite'}
                />
              </div>
              <div className="form-hint">
                {canManageMembers
                  ? 'Invitations are organization-scoped and will auto-attach when the invited email signs in.'
                  : 'Your current role can view member state but cannot invite or manage organization members.'}
              </div>
            </form>
          </div>

          <div className="support-card">
            <div className="organization-item__title-row">
              <h3>Members</h3>
              <StatusBadge tone="neutral">{supportSnapshot?.memberships?.count ?? 0}</StatusBadge>
            </div>
            <div className="support-member-list">
              {(supportSnapshot?.memberships?.items || []).map((member) => (
                <div key={member.membership_id} className="support-member-row">
                  <div>
                    <strong>{member.name}</strong>
                    <span>{member.email || 'No email available'}</span>
                  </div>
                  <div className="support-member-row__meta">
                    <SelectField
                      value={member.role}
                      className="support-member-row__field"
                      disabled={!member.can_edit_role || memberBusyKey === `role:${member.membership_id}`}
                      onChange={(event) => handleUpdateMemberRole(member, event.target.value)}
                    >
                      {(supportSnapshot?.support_actions?.role_options || []).filter((role) => role.assignable || role.key === member.role).map((role) => (
                        <option key={role.key} value={role.key}>{role.label}</option>
                      ))}
                    </SelectField>
                    <span>{member.status}</span>
                    <Button
                      type="button"
                      variant="ghost"
                      size="sm"
                      disabled={!member.can_remove || memberBusyKey === `remove:${member.membership_id}`}
                      onClick={() => handleRemoveMember(member)}
                    >
                      {memberBusyKey === `remove:${member.membership_id}` ? 'Removing...' : 'Remove'}
                    </Button>
                  </div>
                </div>
              ))}
            </div>
          </div>
        </div>

        <div className="support-grid">
          <div className="support-card">
            <div className="organization-item__title-row">
              <h3>Pending invites</h3>
              <StatusBadge tone="neutral">{supportSnapshot?.invitations?.count ?? 0}</StatusBadge>
            </div>
            <div className="api-token-list">
              {(supportSnapshot?.invitations?.items || []).map((invitation) => (
                <article key={invitation.id} className={`feature-flag-card ${invitation.status === 'pending' ? 'feature-flag-card--override' : ''}`}>
                  <div className="feature-flag-card__header">
                    <div className="entitlement-item__copy">
                      <div className="entitlement-item__title-row">
                        <h3>{invitation.name || invitation.email}</h3>
                        <StatusBadge tone={invitation.status === 'pending' ? 'positive' : 'neutral'}>
                          {invitation.status}
                        </StatusBadge>
                      </div>
                      <p>{invitation.email}</p>
                    </div>
                  </div>
                  <div className="feature-flag-card__meta">
                    <span>Role: {invitation.role}</span>
                    <span>Invited by: {invitation.inviter_email || 'System'}</span>
                    <span>Expires: {formatDateTime(invitation.expires_at)}</span>
                    <span>Last update: {formatDateTime(invitation.updated_at)}</span>
                  </div>
                  <div className="feature-flag-card__actions">
                    <Button
                      type="button"
                      variant="ghost"
                      size="sm"
                      disabled={!canManageMembers || !invitation.can_resend || memberBusyKey === `${invitation.id}:resend`}
                      onClick={() => handleInvitationAction(invitation, 'resend', `${invitation.email} invite refreshed.`)}
                    >
                      {memberBusyKey === `${invitation.id}:resend` ? 'Resending...' : 'Resend'}
                    </Button>
                    <Button
                      type="button"
                      variant="ghost"
                      size="sm"
                      disabled={!canManageMembers || !invitation.can_cancel || memberBusyKey === `${invitation.id}:cancel`}
                      onClick={() => handleInvitationAction(invitation, 'cancel', `${invitation.email} invite cancelled.`)}
                    >
                      {memberBusyKey === `${invitation.id}:cancel` ? 'Cancelling...' : 'Cancel'}
                    </Button>
                  </div>
                </article>
              ))}
              {!(supportSnapshot?.invitations?.items || []).length ? (
                <EmptyState
                  title="No invitations"
                  description="No pending or historical invitations for this organization yet."
                />
              ) : null}
            </div>
          </div>
        </div>

        <div className="support-timeline">
          {(supportSnapshot?.timeline?.items || []).map((event) => (
            <article key={event.id} className="support-event">
              <div className="support-event__header">
                <strong>{event.event_type}</strong>
                <span>{formatDateTime(event.created_at)}</span>
              </div>
              <p>{event.actor_email || 'System event'}</p>
              <code>{JSON.stringify(event.payload || {})}</code>
            </article>
          ))}
          {!(supportSnapshot?.timeline?.items || []).length ? (
            <EmptyState
              title="No audit events"
              description="No audit events recorded for this organization yet."
            />
          ) : null}
        </div>
      </SectionCard>

      <SectionCard
        title="Security posture"
        subtitle="Track organization credential hygiene, partner delivery risk, and SSO launch blockers from one control-plane view."
        actions={(
          <StatusBadge tone={getSecurityBadgeClass(securitySummary.status)}>
            {formatSecuritySeverity(securitySummary.status)}
          </StatusBadge>
        )}
      >
        <div className="onboarding-summary-grid">
          <div className="saas-stat">
            <span>Critical issues</span>
            <strong>{securitySummary.critical_count ?? 0}</strong>
          </div>
          <div className="saas-stat">
            <span>Warnings</span>
            <strong>{securitySummary.warning_count ?? 0}</strong>
          </div>
          <div className="saas-stat">
            <span>Admin tokens</span>
            <strong>{securitySummary.active_admin_tokens ?? 0}</strong>
          </div>
          <div className="saas-stat">
            <span>Stale tokens</span>
            <strong>{securitySummary.stale_tokens ?? 0}</strong>
          </div>
          <div className="saas-stat">
            <span>Expiring soon</span>
            <strong>{securitySummary.expiring_tokens ?? 0}</strong>
          </div>
          <div className="saas-stat">
            <span>Webhook failures</span>
            <strong>{securitySummary.failed_webhooks ?? 0}</strong>
          </div>
          <div className="saas-stat">
            <span>Dead letters</span>
            <strong>{securitySummary.dead_letter_jobs ?? 0}</strong>
          </div>
          <div className="saas-stat">
            <span>Throttle events</span>
            <strong>{securitySummary.rate_limit_events ?? 0}</strong>
          </div>
          <div className="saas-stat">
            <span>Blocked actors</span>
            <strong>{securitySummary.blocked_actors ?? 0}</strong>
          </div>
          <div className="saas-stat">
            <span>Last security event</span>
            <strong>{formatDateTime(securitySummary.last_security_event_at)}</strong>
          </div>
        </div>

        <div className="support-grid">
          <article className="support-card">
            <div className="organization-item__title-row">
              <h3>Risk overview</h3>
              <StatusBadge tone={getSecurityBadgeClass(securitySummary.status)}>
                {formatSecuritySeverity(securitySummary.status)}
              </StatusBadge>
            </div>
            <div className="support-card__stats">
              <div><span>Audit events</span><strong>{securityAudit.count ?? 0}</strong></div>
              <div><span>Launch blockers</span><strong>{securitySummary.auth_launch_blockers ?? 0}</strong></div>
              <div><span>SSO provider</span><strong>{securityAuth.provider || 'none'}</strong></div>
              <div><span>Next action</span><strong>{securityAuth.next_action || 'Review token and webhook posture'}</strong></div>
            </div>
            <div className="support-timeline">
              {(securitySnapshot?.risk_items || []).map((risk) => (
                <article key={`${risk.category}-${risk.key}`} className="support-event">
                  <div className="support-event__header">
                    <strong>{risk.title}</strong>
                    <StatusBadge tone={getSecurityBadgeClass(risk.severity)}>{formatSecuritySeverity(risk.severity)}</StatusBadge>
                  </div>
                  <p>{risk.message}</p>
                  <code>{JSON.stringify({ category: risk.category, count: risk.count ?? null })}</code>
                </article>
              ))}
              {!(securitySnapshot?.risk_items || []).length ? (
                <EmptyState
                  title="No active security risks"
              description="No active security risks detected for this organization right now."
                />
              ) : null}
            </div>
          </article>

          <article className="support-card">
            <div className="organization-item__title-row">
              <h3>Token hygiene</h3>
              <StatusBadge tone={securityTokens.enabled ? 'positive' : 'negative'}>
                {securityTokens.enabled ? `${securityTokens.active_count ?? 0} active` : 'Disabled'}
              </StatusBadge>
            </div>
            <div className="support-card__stats">
              <div><span>Total tokens</span><strong>{securityTokens.count ?? 0}</strong></div>
              <div><span>Unused active</span><strong>{securityTokens.unused_active_count ?? 0}</strong></div>
              <div><span>Oldest active</span><strong>{formatDateTime(securityTokens.oldest_active_created_at)}</strong></div>
              <div><span>Next expiry</span><strong>{formatDateTime(securityTokens.next_expiring_at)}</strong></div>
            </div>
            <div className="support-timeline">
              {(securityTokens.risk_items || []).map((risk) => (
                <article key={`token-risk-${risk.key}`} className="support-event">
                  <div className="support-event__header">
                    <strong>{risk.title}</strong>
                    <StatusBadge tone={getSecurityBadgeClass(risk.severity)}>{formatSecuritySeverity(risk.severity)}</StatusBadge>
                  </div>
                  <p>{risk.message}</p>
                </article>
              ))}
              {!(securityTokens.risk_items || []).length ? (
                <EmptyState
                  title="Token issuance healthy"
                  description="Token issuance looks healthy. Review the API access section below for individual credentials."
                />
              ) : null}
            </div>
          </article>

          <article className="support-card">
            <div className="organization-item__title-row">
              <h3>Webhook integrity</h3>
              <StatusBadge tone={securityWebhooks.enabled ? 'positive' : 'negative'}>
                {securityWebhooks.enabled ? `${securityWebhooks.active_count ?? 0} active` : 'Disabled'}
              </StatusBadge>
            </div>
            <div className="support-card__stats">
              <div><span>Paused</span><strong>{securityWebhooks.paused_count ?? 0}</strong></div>
              <div><span>Retrying</span><strong>{securityWebhooks.retrying_count ?? 0}</strong></div>
              <div><span>Dead letters</span><strong>{securityWebhooks.dead_letter_count ?? 0}</strong></div>
              <div><span>Last failure</span><strong>{formatDateTime(securityWebhooks.last_failure_at)}</strong></div>
            </div>
            <div className="support-timeline">
              {(securityWebhooks.risk_items || []).map((risk) => (
                <article key={`webhook-risk-${risk.key}`} className="support-event">
                  <div className="support-event__header">
                    <strong>{risk.title}</strong>
                    <StatusBadge tone={getSecurityBadgeClass(risk.severity)}>{formatSecuritySeverity(risk.severity)}</StatusBadge>
                  </div>
                  <p>{risk.message}</p>
                </article>
              ))}
              {!(securityWebhooks.risk_items || []).length ? (
                <EmptyState
                  title="Webhook delivery clean"
                  description="Partner delivery looks clean. Use the webhook section below for endpoint-level actions and logs."
                />
              ) : null}
            </div>
          </article>

          <article className="support-card">
            <div className="organization-item__title-row">
              <h3>Identity and SSO</h3>
              <StatusBadge tone={securityAuth.launch_ready === false ? 'negative' : 'positive'}>
                {securityAuth.launch_ready === false ? 'Blocked' : 'Ready'}
              </StatusBadge>
            </div>
            <div className="support-card__stats">
              <div><span>Policy</span><strong>{securityAuth.auth_policy || 'default'}</strong></div>
              <div><span>Provider records</span><strong>{securityAuth.provider_record_count ?? 0}</strong></div>
              <div><span>Ready providers</span><strong>{securityAuth.provider_health?.ready ?? 0}</strong></div>
              <div><span>Pending secrets</span><strong>{securityAuth.provider_health?.pending ?? 0}</strong></div>
            </div>
            {securityAuth.launch_blockers?.length ? (
              <FeedbackState
                compact
                tone="negative"
                eyebrow="Identity routing"
                title="Identity launch blockers active"
                description={(securityAuth.launch_blockers || []).join(' ')}
                actions={[{ label: 'Refresh control plane', onAction: handleRefreshSaasSurface, variant: 'ghost' }]}
                role="alert"
              />
            ) : null}
            <div className="support-timeline">
              {(securityAuth.risk_items || []).map((risk) => (
                <article key={`auth-risk-${risk.key}`} className="support-event">
                  <div className="support-event__header">
                    <strong>{risk.title}</strong>
                    <StatusBadge tone={getSecurityBadgeClass(risk.severity)}>{formatSecuritySeverity(risk.severity)}</StatusBadge>
                  </div>
                  <p>{risk.message}</p>
                </article>
              ))}
              {!(securityAuth.risk_items || []).length ? (
                <EmptyState
                  title="Identity routing healthy"
              description="Identity routing looks launch-ready for the current organization configuration."
                />
              ) : null}
            </div>
          </article>

          <article className="support-card">
            <div className="organization-item__title-row">
              <h3>Traffic and abuse controls</h3>
              <StatusBadge tone={securityRateLimits.enabled ? 'positive' : 'negative'}>
                {securityRateLimits.enabled ? 'Enabled' : 'Disabled'}
              </StatusBadge>
            </div>
            <div className="support-card__stats">
              <div><span>Throttle events</span><strong>{securityRateLimits.throttle_event_count ?? 0}</strong></div>
              <div><span>Blocked actors</span><strong>{securityRateLimits.blocked_actor_count ?? 0}</strong></div>
              <div><span>Auth lockouts</span><strong>{securityRateLimits.auth_lockout_count ?? 0}</strong></div>
              <div><span>Last throttle</span><strong>{formatDateTime(securityRateLimits.last_throttle_at)}</strong></div>
            </div>
            <div className="support-timeline">
              {(securityRateLimits.risk_items || []).map((risk) => (
                <article key={`rate-limit-risk-${risk.key}`} className="support-event">
                  <div className="support-event__header">
                    <strong>{risk.title}</strong>
                    <StatusBadge tone={getSecurityBadgeClass(risk.severity)}>{formatSecuritySeverity(risk.severity)}</StatusBadge>
                  </div>
                  <p>{risk.message}</p>
                </article>
              ))}
              {!(securityRateLimits.risk_items || []).length ? (
                <EmptyState
                  title="No active throttling risks"
              description="No active throttling or abuse-control risks detected for this organization right now."
                />
              ) : null}
            </div>
            <div className="support-timeline">
              {(securityRateLimits.blocked_actors || []).slice(0, 4).map((actor) => (
                <article key={`${actor.actor_key}-${actor.blocked_until}`} className="support-event">
                  <div className="support-event__header">
                    <strong>{actor.actor_key}</strong>
                    <span>{formatDateTime(actor.blocked_until)}</span>
                  </div>
                  <p>{actor.reason || 'rate limited'}</p>
                  <code>{JSON.stringify({ tenant_slug: actor.tenant_slug || null, email: actor.email || null, ip_address: actor.ip_address || null })}</code>
                </article>
              ))}
              {!(securityRateLimits.blocked_actors || []).length ? (
                <EmptyState
                  title="No blocked actors"
              description="No active blocked actors for this organization."
                />
              ) : null}
            </div>
          </article>
        </div>

        <div className="support-timeline">
          {(securityRateLimits.recent_events || []).slice(0, 4).map((event, index) => (
            <article key={`${event.policy_key}-${event.at}-${index}`} className="support-event">
              <div className="support-event__header">
                <strong>{event.policy_label || event.policy_key}</strong>
                <span>{formatDateTime(event.at)}</span>
              </div>
              <p>{event.method} {event.path}</p>
              <code>{JSON.stringify({ bucket: event.bucket, retry_after_seconds: event.retry_after_seconds })}</code>
            </article>
          ))}
          {(securityAudit.items || []).map((event) => (
            <article key={event.id} className="support-event">
              <div className="support-event__header">
                <strong>{formatSecurityEventLabel(event.event_type)}</strong>
                <span>{formatDateTime(event.created_at)}</span>
              </div>
              <p>{event.actor_email || 'System event'}</p>
              <code>{JSON.stringify(event.payload || {})}</code>
            </article>
          ))}
          {!(securityAudit.items || []).length ? (
            <EmptyState
              title="No security audit events"
              description="No recent security-sensitive organization events have been recorded yet."
            />
          ) : null}
        </div>
      </SectionCard>

      <SectionCard
        title="API access"
        subtitle="Issue organization-scoped service tokens for partner automations, backend integrations, and scripted desk setup."
        actions={(
          <StatusBadge tone={apiTokensSummary.enabled ? 'positive' : 'negative'}>
            {apiTokensSummary.enabled ? `${apiTokensSummary.active_count ?? 0} active token${(apiTokensSummary.active_count ?? 0) === 1 ? '' : 's'}` : 'Disabled'}
          </StatusBadge>
        )}
      >
        <div className="onboarding-summary-grid">
          <div className="saas-stat">
            <span>Active tokens</span>
            <strong>{apiTokensSummary.active_count ?? 0}</strong>
          </div>
          <div className="saas-stat">
            <span>Revoked</span>
            <strong>{apiTokensSummary.revoked_count ?? 0}</strong>
          </div>
          <div className="saas-stat">
            <span>Remaining</span>
            <strong>{formatRemaining(apiTokensSummary.remaining)}</strong>
          </div>
          <div className="saas-stat">
            <span>API requests</span>
            <strong>{apiUsageSummary.total_requests ?? 0}</strong>
          </div>
          <div className="saas-stat">
            <span>Last 24h</span>
            <strong>{apiUsageSummary.last_24h_requests ?? 0}</strong>
          </div>
          <div className="saas-stat">
            <span>Last request</span>
            <strong>{formatDateTime(apiUsageSummary.last_request_at)}</strong>
          </div>
        </div>

        {createdToken ? (
          <div className="api-token-secret-card">
            <div className="organization-item__title-row">
              <h3>Copy this token now</h3>
              <StatusBadge tone="neutral">{createdToken.name}</StatusBadge>
            </div>
            <p>This secret is only shown once. Use it as a Bearer token or send it in the <code>x-api-token</code> header.</p>
            <code className="api-token-secret">{createdToken.secret}</code>
            <div className="feature-flag-card__meta">
              <span>Scopes: {(createdToken.scopes || []).join(', ') || '--'}</span>
              <span>Expires: {formatDateTime(createdToken.expires_at)}</span>
            </div>
          </div>
        ) : null}

        <div className="delivery-layout">
          <article className="support-card">
            <div className="organization-item__title-row">
              <h3>Issue a service token</h3>
              <StatusBadge tone="neutral">{activeOrganization?.slug || 'organization'}</StatusBadge>
            </div>
            <form id="settings-api-token-form" tabIndex={-1} className="branding-form" onSubmit={handleCreateApiToken}>
              <div className="analysis-form analysis-form--wide branding-form__row">
                <TextField
                  label="Token name"
                  hint="Describe what this token is for before you issue it."
                  error={apiTokenFormErrors.name}
                  required
                  value={apiTokenForm.name}
                  onChange={(event) => {
                    setApiTokenForm((current) => ({ ...current, name: event.target.value }))
                    setApiTokenFormErrors((current) => omitKeys(current, ['name']))
                  }}
                  placeholder="Token name"
                  disabled={!apiAccessEnabled || !canManageApiTokens || apiTokenBusyKey === 'create'}
                />
                <TextField
                  label="Expires in days"
                  hint="Whole number between 1 and 3650."
                  error={apiTokenFormErrors.expiresInDays}
                  type="number"
                  min="1"
                  max="3650"
                  value={apiTokenForm.expiresInDays}
                  onChange={(event) => {
                    setApiTokenForm((current) => ({ ...current, expiresInDays: event.target.value }))
                    setApiTokenFormErrors((current) => omitKeys(current, ['expiresInDays']))
                  }}
                  placeholder="Expires in days"
                  disabled={!apiAccessEnabled || !canManageApiTokens || apiTokenBusyKey === 'create'}
                />
                <Button type="submit" variant="solid" disabled={!apiAccessEnabled || !canManageApiTokens || apiTokenBusyKey === 'create'}>
                  {apiTokenBusyKey === 'create' ? 'Issuing...' : 'Create token'}
                </Button>
              </div>

              <div className="api-token-scope-grid">
                {(apiTokensSummary.scope_catalog || API_TOKEN_SCOPE_OPTIONS).map((scope) => {
                  const checked = apiTokenForm.scopes.includes(scope.key)
                  return (
                    <div key={scope.key} className={`delivery-checklist__item api-token-scope ${checked ? 'delivery-checklist__item--complete' : ''}`}>
                      <ToggleField
                        label={scope.label}
                        className="api-token-scope__toggle"
                        checked={checked}
                        onChange={() => toggleApiTokenScope(scope.key)}
                        disabled={!apiAccessEnabled || !canManageApiTokens || apiTokenBusyKey === 'create'}
                      />
                      <span>{scope.description || scope.key}</span>
                    </div>
                  )
                })}
              </div>
              {apiTokenFormErrors.scopes ? <p className="ui-field__error">{apiTokenFormErrors.scopes}</p> : null}

              <div className="form-hint">
                {apiAccessEnabled
                  ? canManageApiTokens
                    ? 'Service tokens are organization-scoped. Use organization admin only for automation that must manage rollout or branding.'
                    : 'Your current role can view API access but cannot issue or revoke service tokens.'
                  : 'API access is disabled for this organization. Enable the flag or move the organization to Team or higher to issue tokens.'}
              </div>
            </form>
          </article>

          <article className="support-card">
            <div className="organization-item__title-row">
              <h3>Issued tokens</h3>
              <StatusBadge tone="neutral">{apiTokensSummary.count ?? 0} total</StatusBadge>
            </div>
            <div className="api-token-list">
              {(apiTokensSummary.items || []).map((token) => (
                <article key={token.id} className={`feature-flag-card ${token.status === 'active' ? 'feature-flag-card--override' : ''}`}>
                  <div className="feature-flag-card__header">
                    <div className="entitlement-item__copy">
                      <div className="entitlement-item__title-row">
                        <h3>{token.name}</h3>
                        <StatusBadge tone={token.status === 'active' ? 'positive' : 'negative'}>
                          {token.status}
                        </StatusBadge>
                      </div>
                      <p>{token.token_prefix || token.id}</p>
                    </div>
                  </div>
                  <div className="feature-flag-card__meta">
                    <span>Scopes: {(token.scope_labels || token.scopes || []).join(', ') || '--'}</span>
                    <span>Created: {formatDateTime(token.created_at)}</span>
                    <span>Last used: {formatDateTime(token.last_used_at)}</span>
                    <span>Expires: {formatDateTime(token.expires_at)}</span>
                  </div>
                  <div className="feature-flag-card__actions">
                    <Button
                      type="button"
                      variant="ghost"
                      size="sm"
                      disabled={!canManageApiTokens || !token.can_revoke || apiTokenBusyKey === token.id}
                      onClick={() => handleRevokeApiToken(token)}
                    >
                      {apiTokenBusyKey === token.id ? 'Revoking...' : token.can_revoke ? 'Revoke token' : 'Unavailable'}
                    </Button>
                  </div>
                </article>
              ))}
              {!(apiTokensSummary.items || []).length ? (
                <EmptyState
                  title="No organization tokens"
                  description="Start here by issuing the first organization token so automation and API traffic can be metered."
                  actionLabel="Issue token"
                  onAction={() => scrollToSettingsForm('settings-api-token-form')}
                />
              ) : null}
            </div>
          </article>
        </div>

        <div className="analytics-grid">
          <article className="support-card">
            <div className="organization-item__title-row">
              <h3>Route usage</h3>
              <StatusBadge tone="neutral">{apiUsageSummary.route_group_count ?? 0} groups</StatusBadge>
            </div>
            <div className="analytics-funnel">
              {(apiUsageSnapshot?.route_groups || []).map((group) => (
                <div key={group.key} className="analytics-funnel__item analytics-funnel__item--complete">
                  <strong>{group.key}</strong>
                  <span>{group.count} requests</span>
                </div>
              ))}
              {!(apiUsageSnapshot?.route_groups || []).length ? (
                <EmptyState
                  title="No token traffic"
                  description="No token traffic recorded yet."
                />
              ) : null}
            </div>
          </article>

          <article className="support-card">
            <div className="organization-item__title-row">
              <h3>Recent token traffic</h3>
              <StatusBadge tone="neutral">{(apiUsageSnapshot?.recent || []).length}</StatusBadge>
            </div>
            <div className="support-timeline">
              {(apiUsageSnapshot?.recent || []).slice(0, 6).map((event, index) => (
                <article key={`${event.token_id}-${event.at}-${index}`} className="support-event">
                  <div className="support-event__header">
                    <strong>{event.token_name || event.token_id}</strong>
                    <span>{formatDateTime(event.at)}</span>
                  </div>
                  <p>{event.method} {event.path}</p>
                  <code>{JSON.stringify({ route_group: event.route_group, status_code: event.status_code })}</code>
                </article>
              ))}
              {!(apiUsageSnapshot?.recent || []).length ? (
                <EmptyState
                  title="No recent token traffic"
                  description="Issue a token and use it against the API to start metering."
                />
              ) : null}
            </div>
          </article>
        </div>
      </SectionCard>

      <SectionCard
        title="Partner webhooks"
        subtitle="Configure outbound organization callbacks, rotate signing secrets, and keep delivery logs for partner integrations."
        actions={(
          <StatusBadge tone={webhookSummary.enabled ? 'positive' : 'negative'}>
            {webhookSummary.enabled ? `${webhookSummary.active_count ?? 0} active` : 'Disabled'}
          </StatusBadge>
        )}
      >
        <div className="onboarding-summary-grid">
          <div className="saas-stat">
            <span>Configured</span>
            <strong>{webhookSummary.count ?? 0}</strong>
          </div>
          <div className="saas-stat">
            <span>Active</span>
            <strong>{webhookSummary.active_count ?? 0}</strong>
          </div>
          <div className="saas-stat">
            <span>Remaining</span>
            <strong>{formatRemaining(webhookSummary.remaining)}</strong>
          </div>
          <div className="saas-stat">
            <span>Queued jobs</span>
            <strong>{webhookJobSummary.queued ?? 0}</strong>
          </div>
          <div className="saas-stat">
            <span>Retrying</span>
            <strong>{webhookJobSummary.retrying ?? 0}</strong>
          </div>
          <div className="saas-stat">
            <span>Dead letters</span>
            <strong>{webhookJobSummary.dead_letter ?? 0}</strong>
          </div>
        </div>

        {webhookSecret ? (
          <div className="api-token-secret-card">
            <div className="organization-item__title-row">
              <h3>Copy this signing secret now</h3>
              <StatusBadge tone="neutral">{webhookSecret.name}</StatusBadge>
            </div>
            <p>Use this to verify `X-StockSignals-Signature` on incoming deliveries. It is only shown after create or rotate.</p>
            <code className="api-token-secret">{webhookSecret.secret}</code>
          </div>
        ) : null}

        <div className="delivery-layout">
          <article className="support-card">
            <div className="organization-item__title-row">
              <h3>Create webhook</h3>
              <StatusBadge tone="neutral">{activeOrganization?.slug || 'organization'}</StatusBadge>
            </div>
            <form id="settings-webhook-form" tabIndex={-1} className="branding-form" onSubmit={handleCreateWebhook}>
              <div className="analysis-form analysis-form--wide branding-form__row">
                <TextField
                  label="Webhook name"
                  hint="Name the partner endpoint so delivery logs stay readable."
                  error={webhookFormErrors.name}
                  required
                  value={webhookForm.name}
                  onChange={(event) => {
                    setWebhookForm((current) => ({ ...current, name: event.target.value }))
                    setWebhookFormErrors((current) => omitKeys(current, ['name']))
                  }}
                  placeholder="Webhook name"
                  disabled={!partnerWebhooksEnabled || !canManageWebhooks || webhookBusyKey === 'create'}
                />
                <TextField
                  label="Webhook URL"
                  hint="Full partner callback URL, including protocol."
                  error={webhookFormErrors.url}
                  required
                  value={webhookForm.url}
                  onChange={(event) => {
                    setWebhookForm((current) => ({ ...current, url: event.target.value }))
                    setWebhookFormErrors((current) => omitKeys(current, ['url']))
                  }}
                  placeholder="https://partner.example.com/hooks/stocksignals"
                  disabled={!partnerWebhooksEnabled || !canManageWebhooks || webhookBusyKey === 'create'}
                />
                <Button type="submit" variant="solid" disabled={!partnerWebhooksEnabled || !canManageWebhooks || webhookBusyKey === 'create'}>
                  {webhookBusyKey === 'create' ? 'Creating...' : 'Create webhook'}
                </Button>
              </div>

              <div className="api-token-scope-grid">
                {(webhookSummary.event_catalog || []).map((event) => {
                  const checked = webhookForm.events.includes(event.key)
                  return (
                    <div key={event.key} className={`delivery-checklist__item api-token-scope ${checked ? 'delivery-checklist__item--complete' : ''}`}>
                      <ToggleField
                        label={event.label}
                        className="api-token-scope__toggle"
                        checked={checked}
                        onChange={() => toggleWebhookEvent(event.key)}
                        disabled={!partnerWebhooksEnabled || !canManageWebhooks || webhookBusyKey === 'create'}
                      />
                      <span>{event.description}</span>
                    </div>
                  )
                })}
              </div>
              {webhookFormErrors.events ? <p className="ui-field__error">{webhookFormErrors.events}</p> : null}

              <div className="form-hint">
                {partnerWebhooksEnabled
                  ? canManageWebhooks
                    ? 'Partner webhooks are organization-scoped and signed. Use test deliveries after configuration to validate remote handling.'
                    : 'Your current role can view webhook delivery state but cannot create or manage endpoints.'
                  : 'Partner webhooks are disabled for this organization. Enable the rollout flag or move the organization to Team or higher.'}
              </div>
            </form>
          </article>

          <article className="support-card">
            <div className="organization-item__title-row">
              <h3>Configured endpoints</h3>
              <StatusBadge tone="neutral">{webhookSummary.count ?? 0} total</StatusBadge>
            </div>
            <div className="api-token-list">
              {(webhookSummary.items || []).map((webhook) => (
                <article key={webhook.id} className={`feature-flag-card ${webhook.status === 'active' ? 'feature-flag-card--override' : ''}`}>
                  <div className="feature-flag-card__header">
                    <div className="entitlement-item__copy">
                      <div className="entitlement-item__title-row">
                        <h3>{webhook.name}</h3>
                        <StatusBadge tone={webhook.status === 'active' ? 'positive' : 'negative'}>
                          {webhook.status}
                        </StatusBadge>
                      </div>
                      <p>{webhook.url}</p>
                    </div>
                  </div>
                  <div className="feature-flag-card__meta">
                    <span>Events: {(webhook.events || []).join(', ') || '--'}</span>
                    <span>Secret prefix: {webhook.secret_prefix || '--'}</span>
                    <span>Last test: {formatDateTime(webhook.last_test_at)}</span>
                    <span>Last delivery: {formatDateTime(webhook.last_delivery_at)}</span>
                  </div>
                  <div className="feature-flag-card__actions webhook-action-grid">
                    <Button type="button" variant="ghost" size="sm" disabled={!canManageWebhooks || webhookBusyKey === `${webhook.id}:send_test`} onClick={() => handleWebhookAction(webhook, 'send_test', `${webhook.name} test delivery attempted.`)}>
                      {webhookBusyKey === `${webhook.id}:send_test` ? 'Testing...' : 'Send test'}
                    </Button>
                    <Button type="button" variant="ghost" size="sm" disabled={!canManageWebhooks || webhookBusyKey === `${webhook.id}:rotate_secret`} onClick={() => handleWebhookAction(webhook, 'rotate_secret', `${webhook.name} secret rotated.`)}>
                      {webhookBusyKey === `${webhook.id}:rotate_secret` ? 'Rotating...' : 'Rotate secret'}
                    </Button>
                    <Button type="button" variant="ghost" size="sm" disabled={!canManageWebhooks || webhookBusyKey === `${webhook.id}:${webhook.status === 'active' ? 'pause' : 'resume'}`} onClick={() => handleWebhookAction(webhook, webhook.status === 'active' ? 'pause' : 'resume', `${webhook.name} ${webhook.status === 'active' ? 'paused' : 'resumed'}.`)}>
                      {webhook.status === 'active' ? 'Pause' : 'Resume'}
                    </Button>
                    <Button type="button" variant="ghost" size="sm" disabled={!canManageWebhooks || webhookBusyKey === `${webhook.id}:delete`} onClick={() => handleWebhookAction(webhook, 'delete', `${webhook.name} deleted.`)}>
                      {webhookBusyKey === `${webhook.id}:delete` ? 'Deleting...' : 'Delete'}
                    </Button>
                  </div>
                </article>
              ))}
              {!(webhookSummary.items || []).length ? (
                <EmptyState
                  title="No partner webhooks"
                  description="Start here by creating the first partner webhook so outbound delivery and test traffic have a live endpoint."
                  actionLabel="Create webhook"
                  onAction={() => scrollToSettingsForm('settings-webhook-form')}
                />
              ) : null}
            </div>
          </article>
        </div>

        <div className="support-timeline">
          {(webhookSummary.deliveries || []).map((delivery) => (
            <article key={delivery.id} className="support-event">
              <div className="support-event__header">
                <strong>{delivery.webhook_name || delivery.webhook_id}</strong>
                <span>{formatDateTime(delivery.delivered_at)}</span>
              </div>
              <p>{delivery.event_key} | {delivery.status}</p>
              <code>{JSON.stringify({ status_code: delivery.status_code, error: delivery.error || null })}</code>
            </article>
          ))}
          {!(webhookSummary.deliveries || []).length ? (
            <EmptyState
              title="No test deliveries"
              description="Test deliveries will appear here after you send them."
            />
          ) : null}
        </div>

        <div className="support-timeline">
          {(webhookSummary.jobs?.recent_jobs || []).map((job) => (
            <article key={job.id} className="support-event">
              <div className="support-event__header">
                <strong>{job.job_label || job.job_type}</strong>
                <span>{formatDateTime(job.finished_at || job.started_at || job.available_at)}</span>
              </div>
              <p>{job.status} | attempt {job.attempt_count}/{job.max_attempts}</p>
              <code>{JSON.stringify({ http_status: job.last_http_status ?? null, error: job.error_message || null })}</code>
            </article>
          ))}
          {!(webhookSummary.jobs?.recent_jobs || []).length ? (
            <EmptyState
              title="No delivery jobs"
              description="Queued delivery jobs will appear here as partner webhook traffic flows."
            />
          ) : null}
        </div>
      </SectionCard>

      <SectionCard
        title="Admin rollout controls"
        subtitle="Per-organization overrides layered on top of plan defaults so you can turn pilots up or down without changing the catalog."
        actions={(
          <StatusBadge tone="neutral">
            {featureFlags?.override_count ?? 0} overrides active
          </StatusBadge>
        )}
      >
        <div className="onboarding-summary-grid">
          <div className="saas-stat">
            <span>Tracked flags</span>
            <strong>{featureFlags?.count ?? 0}</strong>
          </div>
          <div className="saas-stat">
            <span>Enabled</span>
            <strong>{featureFlags?.enabled_count ?? 0}</strong>
          </div>
          <div className="saas-stat">
            <span>Custom rollouts</span>
            <strong>{featureFlags?.custom_count ?? 0}</strong>
          </div>
        </div>

        <div className="feature-flag-grid">
          {rolloutFlags.map((flag) => (
            <article key={flag.key} className={`feature-flag-card ${flag.is_overridden ? 'feature-flag-card--override' : ''}`}>
              <div className="feature-flag-card__header">
                <div className="entitlement-item__copy">
                  <div className="entitlement-item__title-row">
                    <h3>{flag.label}</h3>
                    <StatusBadge tone={flag.effective_enabled ? 'positive' : 'negative'}>
                      {flag.effective_enabled ? 'Live' : 'Off'}
                    </StatusBadge>
                  </div>
                  <p>{flag.description || flag.key}</p>
                </div>
                <div className="feature-flag-card__badges">
                  <StatusBadge tone="neutral">{flag.plan_defined ? 'Plan-backed' : 'Beta flag'}</StatusBadge>
                  <StatusBadge tone="neutral">{flag.source}</StatusBadge>
                </div>
              </div>

              <div className="feature-flag-card__meta">
                <span>Plan default: {flag.plan_enabled ? 'On' : 'Off'}</span>
                <span>Plan limit: {formatRemaining(flag.plan_limit)}</span>
                <span>Effective limit: {formatRemaining(flag.effective_limit)}</span>
                <span>Override: {flag.is_overridden ? 'Active' : 'None'}</span>
              </div>

              <div className="feature-flag-card__actions">
                <Button
                  type="button"
                  variant="ghost"
                  size="sm"
                  disabled={!canManageFeatureFlags || featureFlagBusyKey === flag.key || featureFlagBusyKey === `${flag.key}:reset`}
                  onClick={() => handleFeatureFlagToggle(flag)}
                >
                  {featureFlagBusyKey === flag.key ? 'Updating...' : flag.effective_enabled ? 'Disable' : 'Enable'}
                </Button>
                <Button
                  type="button"
                  variant="ghost"
                  size="sm"
                  disabled={!canManageFeatureFlags || !flag.is_overridden || featureFlagBusyKey === `${flag.key}:reset` || featureFlagBusyKey === flag.key}
                  onClick={() => handleFeatureFlagReset(flag)}
                >
                  {featureFlagBusyKey === `${flag.key}:reset` ? 'Resetting...' : 'Reset to plan'}
                </Button>
              </div>
            </article>
          ))}
        </div>
      </SectionCard>

      <TickerHub
        activeTicker={preferences.defaultTicker}
        compact
        onSelectTicker={(ticker) => setPreference('defaultTicker', ticker)}
        onLoadFavorites={(favorites) => setPreference('watchlistTickers', favorites.join(','))}
      />

      <SectionCard title="Operations preferences" subtitle="Persisted locally for the platform-operations interface.">
        <div className="ui-field-grid ui-field-grid--settings">
          <TextField
            label="Default ticker"
            value={preferences.defaultTicker}
            onChange={(e) => setPreference('defaultTicker', e.target.value.toUpperCase())}
            placeholder="Default ticker"
          />
          <SelectField
            label="Default interval"
            hint={`${getTradingStyleLabel(preferences.tradingStyle)} mode keeps ${orderedIntervalOptions.slice(0, 3).join(', ')} closest to the front of the workflow.`}
            value={preferences.defaultInterval}
            onChange={(e) => setPreference('defaultInterval', e.target.value)}
          >
            {orderedIntervalOptions.map((interval) => (
              <option key={interval} value={interval}>{interval}</option>
            ))}
          </SelectField>
          <TextField
            label="Default horizon"
            hint={defaultIntervalModel.recommendedDetail}
            type="number"
            min="1"
            max="50"
            value={preferences.defaultHorizon}
            onChange={(e) => setPreference('defaultHorizon', Number(e.target.value))}
          />
          <TextField
            label="Polling cadence"
            type="number"
            min="5000"
            step="1000"
            value={preferences.pollingMs}
            onChange={(e) => setPreference('pollingMs', Number(e.target.value))}
          />
        </div>
        <ActionBar className="settings-action-bar">
          <Button type="button" variant="solid" onClick={saveNotice}>Save</Button>
        </ActionBar>
        <div className="ui-field-grid ui-field-grid--settings">
          <TextField
            label="Default watchlist"
            value={preferences.watchlistTickers}
            onChange={(e) => setPreference('watchlistTickers', e.target.value.toUpperCase())}
            placeholder="Default watchlist tickers"
          />
          <ToggleField
            label="Auto refresh watchlist"
            checked={preferences.autoRefreshWatchlist}
            onChange={(e) => setPreference('autoRefreshWatchlist', e.target.checked)}
          />
            <ToggleField
              label="Dense operations"
              hint="Tighten board rows, replay tables, queue cards, and section spacing across the workstation."
              checked={preferences.compactTables}
              onChange={(e) => setPreference('compactTables', e.target.checked)}
            />
        </div>
        <ActionBar className="settings-action-bar">
          <Button
            type="button"
            variant="ghost"
            onClick={async () => {
              await clearRecentTickers()
              pushToast('Recent ticker history cleared.', 'info')
            }}
          >
            Clear recents
          </Button>
          <Button
            type="button"
            variant="subtle"
            onClick={() => {
              resetPreferences()
              pushToast('Preferences reset to defaults.', 'info')
            }}
          >
            Reset
          </Button>
        </ActionBar>
      </SectionCard>

      <SectionCard
        title="Operations workflow"
        subtitle="Control how the platform-operations shell opens, where review jumps go, and how much workflow guidance stays visible."
      >
        <ActionBar className="settings-action-bar">
          <Button type="button" variant={preferences.tradingStyle === 'swing' ? 'solid' : 'ghost'} onClick={() => applyTradingStylePreset('swing')}>
            Apply swing defaults
          </Button>
          <Button type="button" variant={preferences.tradingStyle === 'intraday' ? 'solid' : 'ghost'} onClick={() => applyTradingStylePreset('intraday')}>
            Apply intraday defaults
          </Button>
          {preferences.tradingStyle === 'intraday' ? (
            <Button type="button" variant="ghost" onClick={() => applyTradingStylePreset('intraday', intradayPreset)}>
              Apply {intradayPresetProfile.shortLabel} preset
            </Button>
          ) : null}
        </ActionBar>
        {preferences.tradingStyle === 'intraday' ? (
          <FeedbackState
            tone="warning"
            title={`${intradayPresetProfile.label} active`}
            description={`${intradayPresetProfile.description} Start from ${intradayWatchlistGuide.title.toLowerCase()} so the preset teaches the first move instead of assuming it.`}
          />
        ) : null}
        <div className="ui-field-grid ui-field-grid--settings">
          <SelectField
            label="Trading style"
            hint="Controls the workstation’s default home and review routing. Use the preset buttons above if you also want the matching interval and session defaults."
            value={preferences.tradingStyle}
            onChange={(e) => setPreference('tradingStyle', e.target.value)}
          >
            {TRADING_STYLE_OPTIONS.map((option) => (
              <option key={option.value} value={option.value}>{option.label}</option>
            ))}
          </SelectField>
          {preferences.tradingStyle === 'intraday' ? (
            <SelectField
              label="Intraday preset"
              hint="Choose the operating style you want the day-trading workstation to teach by default."
              value={intradayPreset}
              onChange={(e) => setPreference('intradayPreset', normalizeIntradayPreset(e.target.value, DEFAULT_INTRADAY_PRESET))}
            >
              {INTRADAY_PRESET_OPTIONS.map((option) => (
                <option key={option.value} value={option.value}>{option.label}</option>
              ))}
            </SelectField>
          ) : null}
          <SelectField
            label="Startup surface"
            hint="Use the trading-style default or choose a fixed opening surface."
            value={preferences.startupSurface}
            onChange={(e) => setPreference('startupSurface', e.target.value)}
          >
            {STARTUP_SURFACE_OPTIONS.map((option) => (
              <option key={option.value} value={option.value}>{option.label}</option>
            ))}
          </SelectField>
          <SelectField
            label="Review surface"
            hint="Use the trading-style default or choose a fixed shell review jump target."
            value={preferences.defaultReviewSurface}
            onChange={(e) => setPreference('defaultReviewSurface', e.target.value)}
          >
            {REVIEW_SURFACE_OPTIONS.map((option) => (
              <option key={option.value} value={option.value}>{option.label}</option>
            ))}
          </SelectField>
          <ToggleField
            label="Resume last workflow surface"
            hint="Reopen the last active workflow page instead of always starting from the home surface."
            checked={preferences.rememberLastWorkflowSurface}
            onChange={(e) => setPreference('rememberLastWorkflowSurface', e.target.checked)}
          />
          <ToggleField
            label="Show workflow status strip"
            hint="Keep the persistent workflow strip visible above every page."
            checked={preferences.showWorkflowStatusStrip}
            onChange={(e) => setPreference('showWorkflowStatusStrip', e.target.checked)}
          />
          <ToggleField
            label="Show page role guides"
            hint="Keep page-level workflow guide cards visible inside heavy workflow surfaces."
            checked={preferences.showWorkflowGuides}
            onChange={(e) => setPreference('showWorkflowGuides', e.target.checked)}
          />
          <ToggleField
            label="Show arrival context banners"
            hint="Keep replay and handoff arrival banners visible when a page is opened from another workflow surface."
            checked={preferences.showArrivalBanners}
            onChange={(e) => setPreference('showArrivalBanners', e.target.checked)}
          />
        </div>
        <ActionBar className="settings-action-bar">
          <StatusBadge tone="neutral">{operatorSurfaceSummary.styleLabel}</StatusBadge>
          {preferences.tradingStyle === 'intraday' ? <StatusBadge tone="warning">{intradayPresetProfile.shortLabel}</StatusBadge> : null}
          <StatusBadge tone="neutral">{`Home ${operatorSurfaceSummary.startupLabel}`}</StatusBadge>
          <StatusBadge tone="neutral">{`Review ${operatorSurfaceSummary.reviewLabel}`}</StatusBadge>
          <StatusBadge tone="neutral">{operatorSurfaceSummary.guidanceLabel}</StatusBadge>
        </ActionBar>
      </SectionCard>

      <TradeAutomationSection />

      <LinkedBrokerageAccountsSection
        title="Client-linked Alpaca accounts"
        subtitle="Link client-owned Alpaca accounts through OAuth, keep approval policy visible, and keep those accounts isolated from the personal env-key execution lane."
        showBrokerageBinding
      />

      {renderIntradayMarketModelSection()}
    </>
  )
}


