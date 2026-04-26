import { useEffect, useMemo, useState } from 'react'
import { clearRecentTickers, getTradeSummary } from '../api/client'
import ActionBar from '../components/ActionBar'
import Button from '../components/Button'
import FeedbackState from '../components/FeedbackState'
import { SelectField, TextField, ToggleField } from '../components/FormFields'
import MetricCard from '../components/MetricCard'
import PageIntro from '../components/PageIntro'
import SectionCard from '../components/SectionCard'
import TickerHub from '../components/TickerHub'
import LinkedBrokerageAccountsSection from '../components/LinkedBrokerageAccountsSection'
import TradeAutomationSection from '../components/TradeAutomationSection'
import { useToast } from '../context/ToastContext'
import { usePreferences } from '../context/PreferencesContext'
import { useAuth } from '../context/useAuth'
import {
  buildCapitalPreservationPolicy,
  buildPromotionGateSummary,
  buildRolloutReadinessSummary,
  formatPromotionGatePolicySummary,
} from '../utils/capitalPreservation'
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
  getStyleIntervalOptions,
} from '../utils/intradayModel'
import { buildIntradayExecutionPlan } from '../utils/intradayExecutionModel'
import {
  getAccountProfileDefinition,
  normalizeAccountProfile,
  resolveAccountProfileExecutionIntent,
} from '../utils/accountProfileModel'

function formatExecutionIntentLabel(value) {
  const normalized = String(value || 'desk').trim().toLowerCase()
  if (normalized === 'broker_live') return 'Broker live'
  if (normalized === 'broker_paper') return 'Broker paper'
  return 'Desk only'
}

function formatMoney(amount) {
  if (typeof amount !== 'number' || Number.isNaN(amount)) return 'Custom'
  return `$${amount.toLocaleString()}`
}

function scrollToSettingsForm(id) {
  if (typeof document === 'undefined') return
  const element = document.getElementById(id)
  if (!element) return
  element.scrollIntoView({ behavior: 'smooth', block: 'center' })
  if (typeof element.focus === 'function') {
    element.focus({ preventScroll: true })
  }
}

export default function OwnAccountSettingsPage() {
  const { preferences, setPreference, applyPreferences, resetPreferences } = usePreferences()
  const { pushToast } = useToast()
  const { session } = useAuth()
  const activeAccountProfile = normalizeAccountProfile(preferences?.activeAccountProfile)
  const activeAccountProfileDefinition = getAccountProfileDefinition(activeAccountProfile)
  const activePersonalExecutionIntent = resolveAccountProfileExecutionIntent({
    activeAccountProfile,
    defaultExecutionIntent: preferences.defaultExecutionIntent,
  })
  const [localTradeSummary, setLocalTradeSummary] = useState(null)

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
  const activeDeskName = session?.active_tenant?.name || 'Own-Account Trading Desk'
  const watchlistCount = String(preferences.watchlistTickers || '')
    .split(',')
    .map((item) => item.trim())
    .filter(Boolean).length
  const localDeskCapitalPreservationPolicy = buildCapitalPreservationPolicy({
    preferences,
    tradeTicket: null,
    defaults: {
      accountSize: preferences.defaultAccountSize,
      riskPercent: preferences.defaultRiskPercent,
    },
  })
  const promotionGatePolicySummary = formatPromotionGatePolicySummary(
    localDeskCapitalPreservationPolicy.promotionGate,
  )
  const promotionGateSummary = useMemo(
    () =>
      buildPromotionGateSummary({
        validationSnapshot: localTradeSummary?.validation_snapshot,
        policy: localDeskCapitalPreservationPolicy.promotionGate,
      }),
    [localDeskCapitalPreservationPolicy.promotionGate, localTradeSummary?.validation_snapshot],
  )
  const rolloutReadiness = buildRolloutReadinessSummary(localTradeSummary?.rollout_readiness)
  const executionPlan = buildIntradayExecutionPlan({
    tradingStyle: preferences.tradingStyle,
    sessionModel: marketModelSummary.sessionModel,
    regularHoursOnly: preferences.regularHoursOnly,
    executionIntent: activePersonalExecutionIntent,
    orderType: preferences.defaultOrderType,
    timeInForce: preferences.regularHoursOnly ? 'day' : 'day_ext',
    riskPercent: preferences.defaultRiskPercent,
    rolloutAllowsLive: rolloutReadiness.allowsLiveRollout,
  })
  const selectedExecutionRouteLabel = formatExecutionIntentLabel(activePersonalExecutionIntent)
  const selectedExecutionRouteTone =
    activePersonalExecutionIntent === 'broker_live' && !rolloutReadiness.allowsLiveRollout
      ? 'negative'
      : activePersonalExecutionIntent === 'broker_live'
        ? 'positive'
        : activePersonalExecutionIntent === 'broker_paper'
          ? 'warning'
          : 'info'

  useEffect(() => {
    getTradeSummary()
      .then((payload) => setLocalTradeSummary(payload))
      .catch(() => undefined)
  }, [])

  function applyTradingStylePreset(tradingStyle, presetOverride = intradayPreset) {
    const preset = buildTradingStylePreset(tradingStyle, presetOverride)
    applyPreferences(preset)
    pushToast(
      tradingStyle === 'intraday'
        ? `${getIntradayPresetProfile(presetOverride).label} defaults applied.`
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

  return (
    <>
      <PageIntro
        kicker="Personal trading system"
        title={activeAccountProfileDefinition.settingsTitle}
        description={activeAccountProfileDefinition.settingsDescription}
        helper={`Operational pages currently use ${selectedExecutionRouteLabel.toLowerCase()} as the active personal route while this profile is selected.`}
        badge={activeAccountProfileDefinition.badgeLabel}
        actions={(
          <Button type="button" variant="subtle" onClick={() => pushToast('Preferences save automatically in this browser.', 'success')}>
            Save desk defaults
          </Button>
        )}
      />
      <section className="metrics-grid metrics-grid--compact">
        <MetricCard label="Desk" value={activeDeskName} helper={session?.active_tenant?.slug || 'local desk'} />
        <MetricCard label="Default ticker" value={preferences.defaultTicker} helper={`${preferences.defaultInterval} | ${preferences.defaultHorizon} bars`} />
        <MetricCard label="Polling" value={`${Math.round(Number(preferences.pollingMs || 15000) / 1000)}s`} helper={preferences.autoRefreshWatchlist ? 'Auto refresh on' : 'Manual refresh'} />
        <MetricCard label="Desk mode" value={preferences.compactTables ? 'Dense' : 'Comfort'} helper={`${watchlistCount} symbols on the opening liquid board`} />
        <MetricCard label="Trading style" value={operatorSurfaceSummary.styleLabel} helper="Operator mode for startup and review defaults." />
        <MetricCard label="Home surface" value={operatorSurfaceSummary.startupLabel} helper="Where the workstation opens when you start or resume." />
        <MetricCard label="Review surface" value={operatorSurfaceSummary.reviewLabel} helper="Quick review jump from the shell." />
        <MetricCard label="Guidance" value={operatorSurfaceSummary.guidanceLabel} helper={`${operatorSurfaceSummary.guidanceCount} workflow layer${operatorSurfaceSummary.guidanceCount === 1 ? '' : 's'} visible`} />
      </section>

      <SectionCard
        title="First-live controls"
        subtitle="Start here. These settings decide whether the desk stays in replay, paper, or tightly controlled first-capital mode."
        actions={(
          <ActionBar compact>
            <Button type="button" variant="ghost" size="sm" onClick={() => scrollToSettingsForm('trade-risk-plan-start')}>
              Risk plan
            </Button>
            <Button type="button" variant="ghost" size="sm" onClick={() => scrollToSettingsForm('execution-defaults-start')}>
              Route defaults
            </Button>
            <Button type="button" variant="ghost" size="sm" onClick={() => scrollToSettingsForm('market-model-start')}>
              Market model
            </Button>
          </ActionBar>
        )}
      >
        <section className="metrics-grid metrics-grid--compact">
          <MetricCard
            label="Trading posture"
            value={preferences.tradingStyle === 'intraday' ? intradayPresetProfile.shortLabel : operatorSurfaceSummary.styleLabel}
            tone={preferences.tradingStyle === 'intraday' ? 'warning' : 'default'}
            helper={preferences.tradingStyle === 'intraday' ? intradayPresetProfile.description : 'Switch to intraday defaults before using this desk for same-session live routing.'}
          />
          <MetricCard
            label="Execution route"
            value={selectedExecutionRouteLabel}
            tone={selectedExecutionRouteTone}
            helper={executionPlan.description}
          />
          <MetricCard
            label="Paper gate"
            value={promotionGatePolicySummary}
            tone={promotionGateSummary?.tone || 'warning'}
            helper={promotionGateSummary?.action || promotionGateSummary?.detail}
          />
          <MetricCard
            label="Broker-live readiness"
            value={rolloutReadiness.label}
            tone={rolloutReadiness.tone}
            helper={rolloutReadiness.nextCheckDetail}
          />
        </section>
      </SectionCard>

      <TradeAutomationSection
        mode="personal"
        title="Autonomous desk"
        subtitle="Run the workstation as an unattended paper-first desk. Use prep, paper autopilot, or a tightly scoped live pilot without leaving the personal settings flow."
        eyebrow="Autonomous mode"
      />

      <LinkedBrokerageAccountsSection
        title="Linked Alpaca accounts"
        subtitle="Keep env-backed personal routes separate from OAuth-linked brokerage accounts. Use this area for account binding and review gates, not a client advisory workflow."
      />

      <SectionCard title="Desk basics" subtitle="Secondary workstation defaults for symbols, polling cadence, and board density.">
        <TickerHub
          activeTicker={preferences.defaultTicker}
          compact
          onSelectTicker={(ticker) => setPreference('defaultTicker', ticker)}
          onLoadFavorites={(favorites) => setPreference('watchlistTickers', favorites.join(','))}
        />

        <div className="ui-field-grid ui-field-grid--settings">
          <TextField
            label="Default ticker"
            hint="Used when the desk opens without an active symbol."
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
              <option key={interval} value={interval}>
                {interval}
              </option>
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
            hint="Refresh speed for local market pulls."
            type="number"
            min="5000"
            step="1000"
            value={preferences.pollingMs}
            onChange={(e) => setPreference('pollingMs', Number(e.target.value))}
          />
        </div>

        <ActionBar className="settings-action-bar">
          <Button type="button" variant="solid" onClick={() => pushToast('Preferences save automatically in this browser.', 'success')}>
            Save desk defaults
          </Button>
        </ActionBar>

        <div className="ui-field-grid ui-field-grid--settings">
          <TextField
            label="Default watchlist"
            hint="Comma-separated symbols for your opening board."
            value={preferences.watchlistTickers}
            onChange={(e) => setPreference('watchlistTickers', e.target.value.toUpperCase())}
            placeholder="Default watchlist tickers"
          />
          <ToggleField
            label="Auto refresh watchlist"
            hint="Keep the board polling during your live session."
            checked={preferences.autoRefreshWatchlist}
            onChange={(e) => setPreference('autoRefreshWatchlist', e.target.checked)}
          />
          <ToggleField
            label="Dense desk"
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
            Reset desk
          </Button>
        </ActionBar>
      </SectionCard>

      <SectionCard
        title="Workflow customization"
        subtitle="Secondary workflow defaults for startup surfaces, review jumps, and on-screen guidance once first-live controls are set."
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
            hint="Controls the workstation's default home and review routing. Use the preset buttons above if you also want the matching interval and session defaults."
            value={preferences.tradingStyle}
            onChange={(e) => setPreference('tradingStyle', e.target.value)}
          >
            {TRADING_STYLE_OPTIONS.map((option) => (
              <option key={option.value} value={option.value}>
                {option.label}
              </option>
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
                <option key={option.value} value={option.value}>
                  {option.label}
                </option>
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
              <option key={option.value} value={option.value}>
                {option.label}
              </option>
            ))}
          </SelectField>
          <SelectField
            label="Review surface"
            hint="Use the trading-style default or choose a fixed shell review jump target."
            value={preferences.defaultReviewSurface}
            onChange={(e) => setPreference('defaultReviewSurface', e.target.value)}
          >
            {REVIEW_SURFACE_OPTIONS.map((option) => (
              <option key={option.value} value={option.value}>
                {option.label}
              </option>
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
      </SectionCard>

      {renderIntradayMarketModelSection()}

      <div id="execution-defaults-start" tabIndex={-1} />
      <SectionCard
        title="Intraday execution defaults"
        subtitle="Make the desk’s route, order posture, and same-session risk budget behave like an intraday workstation instead of a slower swing ticket."
      >
        <FeedbackState tone={executionPlan.tone} title={executionPlan.title} description={executionPlan.description} />
        <section className="metrics-grid metrics-grid--compact">
          {executionPlan.cards.map((item) => <MetricCard key={item.label} {...item} />)}
        </section>
      </SectionCard>

      <div id="trade-risk-plan-start" tabIndex={-1} />
      <SectionCard
        title="Trade risk plan"
        subtitle="Local stop-loss and take-profit rules for the desk. These preset the ticket and your routine, but they do not auto-send exits."
      >
        <div className="ui-field-grid ui-field-grid--settings">
          <TextField label="Default account size" hint="Starting account base for ticket sizing." type="number" min="10" step="10" value={preferences.defaultAccountSize} onChange={(e) => setPreference('defaultAccountSize', Number(e.target.value))} />
          <TextField label="Default risk %" hint={preferences.tradingStyle === 'intraday' ? 'Intraday defaults are cleanest near 0.25% risk and should usually stay at or below 0.50%.' : 'Starting risk budget per trade.'} type="number" min="0.1" max="10" step="0.1" value={preferences.defaultRiskPercent} onChange={(e) => setPreference('defaultRiskPercent', Number(e.target.value))} />
          <SelectField label="Default order type" hint={preferences.tradingStyle === 'intraday' ? 'Intraday mode works best with priced orders so same-session fills stay controlled.' : 'Safer starting route for the ticket.'} value={preferences.defaultOrderType} onChange={(e) => setPreference('defaultOrderType', e.target.value)}>
            <option value="limit">Limit</option>
            <option value="market">Market</option>
            <option value="stop_limit">Stop limit</option>
            <option value="stop_market">Stop market</option>
            <option value="trailing_stop">Trailing stop</option>
          </SelectField>
            <SelectField label="Default execution route" hint={`${preferences.tradingStyle === 'intraday' ? 'Start same-session routing on desk or broker paper first, then move to broker live only after broker-live readiness is clear.' : 'Send tickets to the local desk, broker paper, or broker live once broker-live readiness is clear.'} The global profile selector can still force personal paper or personal live on the trading pages.`} value={preferences.defaultExecutionIntent} onChange={(e) => setPreference('defaultExecutionIntent', e.target.value)}>
            <option value="desk">Desk only</option>
            <option value="broker_paper">Broker paper</option>
            <option value="broker_live">Broker live</option>
          </SelectField>
          <ToggleField label="Regular hours only" hint={`${marketModelSummary.sessionModel.regularHoursOnly ? 'Keep same-day routing in the core session.' : 'Allow after-hours routing when the setup justifies it.'}`} checked={preferences.regularHoursOnly} onChange={(e) => setPreference('regularHoursOnly', e.target.checked)} />
          <TextField label="Breakeven after" hint="Move the stop to breakeven after this many R." type="number" min="0.5" max="10" step="0.5" value={preferences.breakevenAfterR} onChange={(e) => setPreference('breakevenAfterR', Number(e.target.value))} />
          <TextField label="First trim at" hint="Scale out the first piece at this R multiple." type="number" min="0.5" max="10" step="0.5" value={preferences.firstTargetR} onChange={(e) => setPreference('firstTargetR', Number(e.target.value))} />
          <TextField label="First trim %" hint="Percent to take off at the first target." type="number" min="1" max="100" step="1" value={preferences.firstTrimPercent} onChange={(e) => setPreference('firstTrimPercent', Number(e.target.value))} />
          <TextField label="Second trim at" hint="Scale out the second piece at this R multiple." type="number" min="0.5" max="20" step="0.5" value={preferences.secondTargetR} onChange={(e) => setPreference('secondTargetR', Number(e.target.value))} />
          <TextField label="Second trim %" hint="Percent to take off at the second target." type="number" min="1" max="100" step="1" value={preferences.secondTrimPercent} onChange={(e) => setPreference('secondTrimPercent', Number(e.target.value))} />
          <TextField label="Max daily loss" hint="Stand down for the day after this many R of damage." type="number" min="0.5" max="10" step="0.5" value={preferences.maxDailyLossR} onChange={(e) => setPreference('maxDailyLossR', Number(e.target.value))} />
          <TextField label="Max consecutive losses" hint="Stop after this many losses in a row." type="number" min="1" max="10" step="1" value={preferences.maxConsecutiveLosses} onChange={(e) => setPreference('maxConsecutiveLosses', Number(e.target.value))} />
          <ToggleField label="Capital preservation mode" hint="Turn on hard lockouts for loss limits, active-ticket caps, and safer order rules." checked={preferences.capitalPreservationMode} onChange={(e) => setPreference('capitalPreservationMode', e.target.checked)} />
          <ToggleField label="Tiny account mode" hint="Tighten the desk for very small accounts with fractional-share, low-notional, single-position rules." checked={preferences.tinyAccountMode} onChange={(e) => setPreference('tinyAccountMode', e.target.checked)} />
          <ToggleField label="Fractional shares only" hint="Allow sub-1 share equity sizing so the desk can work with very small accounts." checked={preferences.fractionalSharesOnlyMode} onChange={(e) => setPreference('fractionalSharesOnlyMode', e.target.checked)} />
          <ToggleField label="Paper gate" hint="Require replay evidence and acceptable paper-vs-live slippage before first capital can promote from the board." checked={preferences.promotionGateMode} onChange={(e) => setPreference('promotionGateMode', e.target.checked)} />
          <TextField label="Min resolved board outcomes" hint="How many saved board leaders must resolve before the desk can promote fresh first capital automatically." type="number" min="1" max="50" step="1" value={preferences.promotionGateMinResolved} onChange={(e) => setPreference('promotionGateMinResolved', Number(e.target.value))} />
          <TextField label="Min replay win rate %" hint="Required resolved win rate for the paper gate to clear." type="number" min="1" max="100" step="1" value={preferences.promotionGateMinWinRatePercent} onChange={(e) => setPreference('promotionGateMinWinRatePercent', Number(e.target.value))} />
          <TextField label="Max avg slippage bps" hint="Average absolute paper-vs-live slippage allowed before promotion stays in review." type="number" min="1" step="0.5" value={preferences.promotionGateMaxAverageAbsSlippageBps} onChange={(e) => setPreference('promotionGateMaxAverageAbsSlippageBps', Number(e.target.value))} />
          <TextField label="Max worst slippage bps" hint="Worst paper-vs-live slippage allowed before first-capital promotion is blocked." type="number" min="1" step="0.5" value={preferences.promotionGateMaxWorstAbsSlippageBps} onChange={(e) => setPreference('promotionGateMaxWorstAbsSlippageBps', Number(e.target.value))} />
          <TextField label="Max open positions" hint="Total active tickets allowed across live and working trades." type="number" min="1" max="10" step="1" value={preferences.maxOpenPositions} onChange={(e) => setPreference('maxOpenPositions', Number(e.target.value))} />
          <TextField label="Max notional / trade" hint="Hard cap for the projected position cost of a new ticket." type="number" min="10" step="10" value={preferences.maxNotionalPerTrade} onChange={(e) => setPreference('maxNotionalPerTrade', Number(e.target.value))} />
          <ToggleField label="Equities only" hint="Block listed-option tickets when capital preservation mode is active." checked={preferences.equitiesOnlyMode} onChange={(e) => setPreference('equitiesOnlyMode', e.target.checked)} />
          <ToggleField label="Limit orders only" hint="Require price-controlled entries instead of market routing." checked={preferences.limitOrdersOnlyMode} onChange={(e) => setPreference('limitOrdersOnlyMode', e.target.checked)} />
          <ToggleField label="Long only" hint="Keep the desk out of bearish option tickets in strict mode." checked={preferences.longOnlyMode} onChange={(e) => setPreference('longOnlyMode', e.target.checked)} />
        </div>
        <ActionBar className="settings-action-bar">
          <Button
            type="button"
            variant="solid"
            onClick={() => {
              applyPreferences({
                defaultTicker: 'SPY',
                defaultInterval: '5m',
                defaultHorizon: 5,
                watchlistTickers: 'SPY,QQQ,NVDA,AAPL,MSFT',
                defaultAccountSize: 1000,
                defaultRiskPercent: 0.5,
                defaultOrderType: 'limit',
                defaultExecutionIntent: 'desk',
                regularHoursOnly: false,
                capitalPreservationMode: true,
                tinyAccountMode: false,
                fractionalSharesOnlyMode: false,
                promotionGateMode: true,
                promotionGateMinResolved: 4,
                promotionGateMinWinRatePercent: 55,
                promotionGateMaxAverageAbsSlippageBps: 12,
                promotionGateMaxWorstAbsSlippageBps: 25,
                maxOpenPositions: 1,
                maxNotionalPerTrade: 500,
                equitiesOnlyMode: true,
                limitOrdersOnlyMode: true,
                longOnlyMode: true,
                breakevenAfterR: 1,
                firstTargetR: 1,
                firstTrimPercent: 33,
                secondTargetR: 2,
                secondTrimPercent: 33,
                maxDailyLossR: 1.5,
                maxConsecutiveLosses: 2,
              })
              pushToast('Monday risk preset applied to the local desk.', 'success')
            }}
          >
            Apply Monday preset
          </Button>
          <Button
            type="button"
            variant="ghost"
            onClick={() => {
              applyPreferences({
                defaultTicker: 'SPY',
                defaultInterval: '5m',
                defaultHorizon: 5,
                watchlistTickers: 'SPY,QQQ,NVDA,AAPL,MSFT',
                defaultAccountSize: 1000,
                defaultRiskPercent: 0.5,
                defaultOrderType: 'limit',
                defaultExecutionIntent: 'desk',
                regularHoursOnly: false,
                capitalPreservationMode: true,
                tinyAccountMode: false,
                fractionalSharesOnlyMode: false,
                promotionGateMode: true,
                promotionGateMinResolved: 3,
                promotionGateMinWinRatePercent: 55,
                promotionGateMaxAverageAbsSlippageBps: 10,
                promotionGateMaxWorstAbsSlippageBps: 20,
                maxOpenPositions: 1,
                maxNotionalPerTrade: 500,
                equitiesOnlyMode: true,
                limitOrdersOnlyMode: true,
                longOnlyMode: true,
                breakevenAfterR: 1,
                firstTargetR: 1,
                firstTrimPercent: 33,
                secondTargetR: 2,
                secondTrimPercent: 33,
                maxDailyLossR: 1,
                maxConsecutiveLosses: 1,
              })
              pushToast('Capital-preservation preset applied to the local desk.', 'success')
            }}
          >
            Apply preservation preset
          </Button>
          <Button
            type="button"
            variant="ghost"
            onClick={() => {
              applyPreferences({
                defaultTicker: 'SPY',
                defaultInterval: '5m',
                defaultHorizon: 5,
                watchlistTickers: 'SPY,QQQ,AAPL,MSFT',
                defaultAccountSize: 10,
                defaultRiskPercent: 0.5,
                defaultOrderType: 'limit',
                defaultExecutionIntent: 'desk',
                regularHoursOnly: false,
                capitalPreservationMode: true,
                tinyAccountMode: true,
                fractionalSharesOnlyMode: true,
                promotionGateMode: true,
                promotionGateMinResolved: 4,
                promotionGateMinWinRatePercent: 60,
                promotionGateMaxAverageAbsSlippageBps: 8,
                promotionGateMaxWorstAbsSlippageBps: 15,
                maxOpenPositions: 1,
                maxNotionalPerTrade: 5,
                equitiesOnlyMode: true,
                limitOrdersOnlyMode: true,
                longOnlyMode: true,
                breakevenAfterR: 1,
                firstTargetR: 1,
                firstTrimPercent: 33,
                secondTargetR: 2,
                secondTrimPercent: 33,
                maxDailyLossR: 1,
                maxConsecutiveLosses: 1,
              })
              pushToast('Tiny-account preset applied with fractional-share sizing.', 'success')
            }}
          >
            Apply $10 tiny-account preset
          </Button>
        </ActionBar>

        <div className="saas-info-grid">
          <div className="saas-stat">
            <span>Plan summary</span>
            <strong>{`${Number(preferences.defaultRiskPercent || 0.5).toFixed(1)}% risk | ${Number(preferences.firstTargetR || 1).toFixed(1)}R / ${Number(preferences.secondTargetR || 2).toFixed(1)}R trims`}</strong>
          </div>
          <div className="saas-stat">
            <span>Stop discipline</span>
            <strong>{`Breakeven at ${Number(preferences.breakevenAfterR || 1).toFixed(1)}R`}</strong>
          </div>
          <div className="saas-stat">
            <span>Daily cutoff</span>
            <strong>{`${Number(preferences.maxDailyLossR || 1.5).toFixed(1)}R or ${Math.max(1, Math.round(Number(preferences.maxConsecutiveLosses || 2)))} losses`}</strong>
          </div>
          <div className="saas-stat">
            <span>Preservation mode</span>
            <strong>
              {preferences.capitalPreservationMode
                ? `${Math.max(1, Math.round(Number(preferences.maxOpenPositions || 1)))} active | ${formatMoney(Number(preferences.maxNotionalPerTrade || 500))} cap${preferences.fractionalSharesOnlyMode ? ' | fractional only' : ''}`
                : 'Off'}
            </strong>
          </div>
          <div className="saas-stat">
            <span>Execution route</span>
            <strong>{selectedExecutionRouteLabel}</strong>
          </div>
          <div className="saas-stat">
            <span>Paper gate</span>
            <strong>{promotionGatePolicySummary}</strong>
          </div>
          <div className="saas-stat">
            <span>Broker-live readiness</span>
            <strong>{rolloutReadiness.label}</strong>
          </div>
        </div>
      </SectionCard>

      <SectionCard
        title="Broker-live readiness"
        subtitle="Shared broker-live readiness for routing on this personal desk."
        actions={(
          <Button
            type="button"
            variant="ghost"
            onClick={() => {
              getTradeSummary()
                .then((payload) => {
                  setLocalTradeSummary(payload)
                  pushToast('Broker-live readiness refreshed.', 'success')
                })
                .catch((error) => {
                  pushToast(error?.message || 'Failed to refresh broker-live readiness.', 'error')
                })
            }}
          >
            Refresh readiness
          </Button>
        )}
      >
        <section className="metrics-grid metrics-grid--compact">
          <MetricCard
            label="Selected route"
            value={selectedExecutionRouteLabel}
            tone={selectedExecutionRouteTone}
            helper={
              activePersonalExecutionIntent === 'broker_live' && !rolloutReadiness.allowsLiveRollout
                ? 'Broker-live routing is selected but still locked by broker-live readiness.'
                : activePersonalExecutionIntent === 'broker_live'
                  ? 'Broker-live routing is selected and currently cleared for a scoped pilot.'
                  : activePersonalExecutionIntent === 'broker_paper'
                    ? 'Paper routing remains the active broker path.'
                    : 'The desk is still routing locally unless you promote it.'
            }
          />
          {rolloutReadiness.cards.map((item) => <MetricCard key={`settings-rollout-${item.label}`} {...item} />)}
        </section>
        <div className="ui-panel ui-panel--section">
          <div className="ui-panel__kicker">Unlock path</div>
          <div className="ui-panel__title">{rolloutReadiness.unlockSummary}</div>
          <div className="ui-panel__note">{rolloutReadiness.detail}</div>
          <div className="inline-meta-list">
            <span className="inline-meta-list__item">
              <strong>Next check:</strong> {rolloutReadiness.nextCheckDetail}
            </span>
            <span className="inline-meta-list__item">
              <strong>Gate basis:</strong> {rolloutReadiness.basis}
            </span>
            <span className="inline-meta-list__item">
              <strong>Trend:</strong> {rolloutReadiness.historyLabel}
            </span>
          </div>
          {rolloutReadiness.historyItems.length ? (
            <div className="inline-meta-list">
              {rolloutReadiness.historyItems.map((item) => (
                <span key={item.key} className="inline-meta-list__item">
                  <strong>{item.recordedLabel}</strong> {item.label} | {item.resolvedCount}/{item.sampleCount} resolved | {item.averageAbsSlippage}
                </span>
              ))}
            </div>
          ) : null}
        </div>
      </SectionCard>

      <SectionCard title="Desk session" subtitle="Current local session context for this trading desk.">
        <div className="saas-info-grid">
          <div className="saas-stat">
            <span>Signed in as</span>
            <strong>{session?.user?.name || 'Trader'}</strong>
          </div>
          <div className="saas-stat">
            <span>Email</span>
            <strong>{session?.user?.email || 'local session'}</strong>
          </div>
          <div className="saas-stat">
            <span>Role</span>
            <strong>{session?.active_tenant?.role || session?.user?.role || 'owner'}</strong>
          </div>
          <div className="saas-stat">
            <span>Environment</span>
            <strong>{session?.environment || 'development'}</strong>
          </div>
        </div>
      </SectionCard>
    </>
  )
}
