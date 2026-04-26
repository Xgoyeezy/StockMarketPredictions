import { useCallback, useEffect, useMemo, useState } from 'react'
import ActionBar from './ActionBar'
import Button from './Button'
import FeedbackState from './FeedbackState'
import { SelectField } from './FormFields'
import MetricCard from './MetricCard'
import SectionCard from './SectionCard'
import StatusBadge from './StatusBadge'
import {
  getLinkedBrokerageAccounts,
  refreshLinkedBrokerageAccount,
  startAlpacaLinkedAccount,
  unlinkLinkedBrokerageAccount,
  updateLinkedBrokerageAccount,
} from '../api/client'
import { usePreferences } from '../context/PreferencesContext'
import { useToast } from '../context/ToastContext'
import {
  normalizePrimaryBrokerageLinkedAccountId,
} from '../utils/accountProfileModel'

function formatDateTime(value) {
  if (!value) return 'Never'
  const parsed = new Date(value)
  if (Number.isNaN(parsed.getTime())) return value
  return parsed.toLocaleString()
}

function formatMoney(value) {
  const amount = Number(value)
  if (!Number.isFinite(amount)) return '--'
  return amount.toLocaleString(undefined, { style: 'currency', currency: 'USD', maximumFractionDigits: 2 })
}

function mapStatusTone(status) {
  const normalized = String(status || '').trim().toLowerCase()
  if (['connected', 'healthy', 'paper_ready'].includes(normalized)) return 'positive'
  if (['pending', 'paused', 'disabled'].includes(normalized)) return 'warning'
  if (['disconnected', 'unknown'].includes(normalized)) return 'neutral'
  return 'negative'
}

function resolveSyncedFunds(accountSummary) {
  const summary = accountSummary && typeof accountSummary === 'object' ? accountSummary : {}
  for (const [key, label] of [
    ['equity', 'Equity'],
    ['portfolio_value', 'Portfolio value'],
    ['cash', 'Cash'],
    ['buying_power', 'Buying power'],
  ]) {
    const amount = Number(summary[key])
    if (Number.isFinite(amount) && amount > 0) {
      return { value: amount, source: label }
    }
  }
  return { value: null, source: 'Unavailable' }
}

function buildDraft(account) {
  return {
    clientAutoTradingOptIn: Boolean(account?.client_auto_trading_opt_in),
    operatorAutoTradingEnabled: Boolean(account?.operator_auto_trading_enabled),
    automationPaused: Boolean(account?.automation_paused),
    riskPercent: account?.risk_percent ?? '',
    maxNotionalPerTrade: account?.max_notional_per_trade ?? '',
    maxOpenPositions: account?.max_open_positions ?? '',
  }
}

export default function LinkedBrokerageAccountsSection({
  title = 'Linked client accounts',
  subtitle = 'Link Alpaca client accounts through OAuth. Client automation is paper-only, entries-only, and inherits the main desk strategy. Personal env-key routing stays separate.',
  showBrokerageBinding = false,
}) {
  const { preferences, setPreference } = usePreferences()
  const { pushToast } = useToast()
  const [snapshot, setSnapshot] = useState({
    items: [],
    count: 0,
    oauth_configured: false,
    provider: 'alpaca',
    automation_summary: {
      eligible_linked_account_count: 0,
      automated_linked_account_count: 0,
      blocked_linked_account_count: 0,
      last_automated_client_order: null,
      block_reasons_by_account: {},
      items: [],
    },
  })
  const [drafts, setDrafts] = useState({})
  const [loading, setLoading] = useState(true)
  const [busyKey, setBusyKey] = useState('')

  const loadAccounts = useCallback(async () => {
    try {
      const payload = await getLinkedBrokerageAccounts()
      setSnapshot(payload)
    } catch (error) {
      pushToast(error?.response?.data?.detail || error.message || 'Failed to load linked client accounts.', 'error')
    } finally {
      setLoading(false)
    }
  }, [pushToast])

  useEffect(() => {
    loadAccounts()
  }, [loadAccounts])

  useEffect(() => {
    setDrafts((current) => {
      const next = { ...current }
      for (const account of snapshot?.items || []) {
        next[account.id] = {
          ...(next[account.id] || {}),
          ...buildDraft(account),
        }
      }
      return next
    })
  }, [snapshot])

  useEffect(() => {
    if (typeof window === 'undefined') return
    const params = new URLSearchParams(window.location.search)
    if (params.get('brokerage_provider') !== 'alpaca') return
    const status = String(params.get('brokerage_status') || '').trim().toLowerCase()
    if (!status) return
    if (status === 'connected') {
      pushToast('Alpaca client account linked.', 'success')
    } else if (status === 'error') {
      pushToast(params.get('brokerage_error') || 'Alpaca account link failed.', 'error')
    }
  }, [pushToast])

  async function handleStart(environment, linkedAccountId = null) {
    try {
      setBusyKey(`start:${environment}:${linkedAccountId || 'new'}`)
      const payload = await startAlpacaLinkedAccount({
        environment,
        redirect_path: typeof window === 'undefined' ? '/settings' : window.location.pathname,
        linked_account_id: linkedAccountId,
      })
      if (typeof window !== 'undefined') {
        window.location.assign(payload.authorize_url)
      }
    } catch (error) {
      pushToast(error?.response?.data?.detail || error.message || 'Failed to start Alpaca account linking.', 'error')
    } finally {
      setBusyKey('')
    }
  }

  async function handleRefresh(linkedAccountId) {
    try {
      setBusyKey(`refresh:${linkedAccountId}`)
      await refreshLinkedBrokerageAccount(linkedAccountId)
      pushToast('Linked account status refreshed.', 'success')
      await loadAccounts()
    } catch (error) {
      pushToast(error?.response?.data?.detail || error.message || 'Failed to refresh linked account status.', 'error')
    } finally {
      setBusyKey('')
    }
  }

  async function handleSave(linkedAccountId) {
    const draft = drafts[linkedAccountId]
    if (!draft) return
    try {
      setBusyKey(`save:${linkedAccountId}`)
      await updateLinkedBrokerageAccount(linkedAccountId, {
        client_auto_trading_opt_in: Boolean(draft.clientAutoTradingOptIn),
        operator_auto_trading_enabled: Boolean(draft.operatorAutoTradingEnabled),
        automation_paused: Boolean(draft.automationPaused),
        risk_percent: Number(draft.riskPercent),
        max_notional_per_trade: Number(draft.maxNotionalPerTrade),
        max_open_positions: Number(draft.maxOpenPositions),
      })
      pushToast('Client automation settings saved.', 'success')
      await loadAccounts()
    } catch (error) {
      pushToast(error?.response?.data?.detail || error.message || 'Failed to save linked account automation settings.', 'error')
    } finally {
      setBusyKey('')
    }
  }

  async function handleUnlink(linkedAccountId) {
    try {
      setBusyKey(`unlink:${linkedAccountId}`)
      await unlinkLinkedBrokerageAccount(linkedAccountId)
      pushToast('Linked account disconnected.', 'info')
      await loadAccounts()
    } catch (error) {
      pushToast(error?.response?.data?.detail || error.message || 'Failed to unlink the account.', 'error')
    } finally {
      setBusyKey('')
    }
  }

  function updateDraft(linkedAccountId, patch) {
    setDrafts((current) => ({
      ...current,
      [linkedAccountId]: {
        ...(current[linkedAccountId] || {}),
        ...patch,
      },
    }))
  }

  const metrics = useMemo(() => {
    const automationSummary = snapshot?.automation_summary || {}
    return [
      { label: 'Linked accounts', value: Number(snapshot?.count || 0) },
      {
        label: 'Eligible',
        value: Number(automationSummary?.eligible_linked_account_count || 0),
        tone: Number(automationSummary?.eligible_linked_account_count || 0) > 0 ? 'positive' : 'default',
      },
      {
        label: 'Auto-enabled',
        value: Number(automationSummary?.automated_linked_account_count || 0),
        tone: Number(automationSummary?.automated_linked_account_count || 0) > 0 ? 'warning' : 'default',
      },
      {
        label: 'Blocked',
        value: Number(automationSummary?.blocked_linked_account_count || 0),
        tone: Number(automationSummary?.blocked_linked_account_count || 0) > 0 ? 'negative' : 'positive',
      },
    ]
  }, [snapshot])

  const lastAutomatedOrder = snapshot?.automation_summary?.last_automated_client_order
  const primaryBrokerageLinkedAccountId = normalizePrimaryBrokerageLinkedAccountId(
    preferences?.primaryBrokerageLinkedAccountId,
  )
  const brokerageBindingCandidates = useMemo(
    () =>
      Array.isArray(snapshot?.items)
        ? snapshot.items.filter((account) => {
            const connectionStatus = String(account?.connection_status || '').trim().toLowerCase()
            const tokenHealth = String(account?.token_health || '').trim().toLowerCase()
            return connectionStatus === 'connected' && !Boolean(account?.relink_required) && ['healthy', 'unknown'].includes(tokenHealth)
          })
        : [],
    [snapshot?.items],
  )
  const boundBrokerageAccount = useMemo(
    () =>
      brokerageBindingCandidates.find(
        (account) => String(account?.id || '').trim() === primaryBrokerageLinkedAccountId,
      ) || null,
    [brokerageBindingCandidates, primaryBrokerageLinkedAccountId],
  )
  const brokerageBindingMissing =
    showBrokerageBinding && !boundBrokerageAccount && !loading
  const brokerageBindingValue =
    boundBrokerageAccount?.id ||
    (brokerageBindingCandidates[0]?.id ? '' : 'no-account')

  function handlePrimaryBrokerageAccountChange(event) {
    const nextValue = normalizePrimaryBrokerageLinkedAccountId(event.target.value)
    setPreference('primaryBrokerageLinkedAccountId', nextValue)
    if (!nextValue) {
      pushToast('Primary broker account cleared. Brokerage profile trading will stay locked until you bind another account.', 'info')
      return
    }
    const account = brokerageBindingCandidates.find((item) => item.id === nextValue)
    pushToast(`Primary broker account set to ${account?.label || 'the selected linked account'}.`, 'success')
  }

  return (
    <SectionCard
      title={title}
      subtitle={subtitle}
      actions={(
        <ActionBar compact>
          <Button type="button" variant="ghost" onClick={() => handleStart('paper')} disabled={busyKey.startsWith('start:')}>
            Link Alpaca paper
          </Button>
          <Button type="button" variant="subtle" onClick={() => handleStart('live')} disabled={busyKey.startsWith('start:')}>
            Link Alpaca live
          </Button>
          <Button type="button" variant="ghost" onClick={loadAccounts} disabled={loading}>
            Refresh list
          </Button>
        </ActionBar>
      )}
    >
      <section className="metrics-grid metrics-grid--compact">
        {metrics.map((item) => <MetricCard key={item.label} {...item} />)}
      </section>

      {!snapshot?.oauth_configured ? (
        <FeedbackState
          tone="warning"
          title="Alpaca OAuth is not configured"
          description="Set ALPACA_OAUTH_CLIENT_ID and ALPACA_OAUTH_CLIENT_SECRET before linking client-owned accounts."
        />
      ) : null}

      {lastAutomatedOrder ? (
        <FeedbackState
          tone="info"
          title="Latest automated client submission"
          description={`${lastAutomatedOrder.account_label || 'Linked account'} last auto-submitted ${lastAutomatedOrder.order?.ticker || lastAutomatedOrder.ticker || '--'} at ${formatDateTime(lastAutomatedOrder.submitted_at || lastAutomatedOrder.order?.submitted_at)}`}
        />
      ) : null}

      {showBrokerageBinding ? (
        <SectionCard
          title="Primary broker account"
          subtitle="Bind the Brokerage profile to one linked broker account so brokerage trades cannot fall back to the personal env-backed lane."
        >
          <SelectField
            label="Bound broker account"
            hint="When the global profile is Brokerage, orders route only to this linked broker account. Switch the global profile to a Personal mode first if you want to use personal funds."
            value={brokerageBindingValue}
            onChange={handlePrimaryBrokerageAccountChange}
          >
            <option value="">Select a linked broker account</option>
            {!brokerageBindingCandidates.length ? (
              <option value="no-account" disabled>
                No connected linked broker accounts available
              </option>
            ) : null}
            {brokerageBindingCandidates.map((account) => (
              <option key={account.id} value={account.id}>
                {`${account.label} (${String(account.account_environment || 'paper').toUpperCase()})`}
              </option>
            ))}
          </SelectField>
          {boundBrokerageAccount ? (
            <FeedbackState
              tone="positive"
              title={`Brokerage profile bound to ${boundBrokerageAccount.label}`}
              description={`Environment ${String(boundBrokerageAccount.account_environment || 'paper').toUpperCase()}. Connection ${boundBrokerageAccount.connection_status}. Last refresh ${formatDateTime(boundBrokerageAccount.last_refreshed_at)}.`}
            />
          ) : brokerageBindingMissing ? (
            <FeedbackState
              tone="warning"
              title="Brokerage trading is currently locked"
              description="Bind a connected linked broker account here before using the Brokerage profile for trading. Personal funds will stay unavailable while Brokerage is active."
            />
          ) : null}
        </SectionCard>
      ) : null}

      {loading ? (
        <FeedbackState tone="info" title="Loading linked accounts" description="Refreshing the client-account connection state." />
      ) : null}

      {!loading && (!Array.isArray(snapshot?.items) || snapshot.items.length === 0) ? (
        <FeedbackState
          tone="info"
          title="No linked client accounts yet"
          description="Use the OAuth link buttons above to connect each client's Alpaca paper or live account. Client automation stays paper-only and entries-only in this lane."
        />
      ) : null}

      <div className="stack">
        {(snapshot?.items || []).map((account) => {
          const draft = drafts[account.id] || buildDraft(account)
          const syncedFunds = resolveSyncedFunds(account.account_summary)
          return (
            <div key={account.id} className="surface-card surface-card--soft">
              <div className="surface-card__header">
                <div>
                  <strong>{account.label}</strong>
                  <div className="ui-muted">
                    {String(account.account_environment || 'paper').toUpperCase()} | {account.external_account_number_masked || 'Masked account pending'}
                  </div>
                </div>
                <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
                  <StatusBadge tone={mapStatusTone(account.connection_status)}>
                    {account.connection_status || 'unknown'}
                  </StatusBadge>
                  <StatusBadge tone={mapStatusTone(account.automation_status)}>
                    {account.automation_status_label || account.automation_status || 'disabled'}
                  </StatusBadge>
                </div>
              </div>
              <div className="surface-card__body">
                <div className="metrics-grid metrics-grid--compact">
                  <MetricCard label="Token health" value={account.token_health || '--'} tone={mapStatusTone(account.token_health)} />
                  <MetricCard label="Equity" value={formatMoney(account.account_summary?.equity)} />
                  <MetricCard label="Cash" value={formatMoney(account.account_summary?.cash)} />
                  <MetricCard label="Buying power" value={formatMoney(account.account_summary?.buying_power)} />
                  <MetricCard label="Block reason" value={account.automation_block_label || 'Ready'} tone={account.automation_block_reason ? 'warning' : 'positive'} />
                </div>

                <p className="ui-muted">
                  Linked {formatDateTime(account.linked_at)}. Last refreshed {formatDateTime(account.last_refreshed_at)}. Strategy binding: main desk. Entries only.
                </p>

                <div className="metrics-grid metrics-grid--compact" style={{ marginTop: 12 }}>
                  <label className="surface-card surface-card--outline">
                    <div><strong>Client opt-in</strong></div>
                    <input
                      type="checkbox"
                      checked={Boolean(draft.clientAutoTradingOptIn)}
                      onChange={(event) => updateDraft(account.id, { clientAutoTradingOptIn: event.target.checked })}
                    />
                  </label>
                  <label className="surface-card surface-card--outline">
                    <div><strong>Operator active</strong></div>
                    <input
                      type="checkbox"
                      checked={Boolean(draft.operatorAutoTradingEnabled)}
                      onChange={(event) => updateDraft(account.id, { operatorAutoTradingEnabled: event.target.checked })}
                    />
                  </label>
                  <label className="surface-card surface-card--outline">
                    <div><strong>Paused</strong></div>
                    <input
                      type="checkbox"
                      checked={Boolean(draft.automationPaused)}
                      onChange={(event) => updateDraft(account.id, { automationPaused: event.target.checked })}
                    />
                  </label>
                </div>

                <div className="metrics-grid metrics-grid--compact" style={{ marginTop: 12 }}>
                  <div className="surface-card surface-card--outline">
                    <div><strong>Synced funds</strong></div>
                    <div>{formatMoney(syncedFunds.value)}</div>
                    <div className="ui-muted">Sizing source: {syncedFunds.source}</div>
                  </div>
                  <label className="surface-card surface-card--outline">
                    <div><strong>Risk %</strong></div>
                    <input
                      type="number"
                      min="0.05"
                      max="100"
                      step="0.05"
                      value={draft.riskPercent}
                      onChange={(event) => updateDraft(account.id, { riskPercent: event.target.value })}
                    />
                  </label>
                  <label className="surface-card surface-card--outline">
                    <div><strong>Max notional</strong></div>
                    <input
                      type="number"
                      min="100"
                      step="100"
                      value={draft.maxNotionalPerTrade}
                      onChange={(event) => updateDraft(account.id, { maxNotionalPerTrade: event.target.value })}
                    />
                  </label>
                  <label className="surface-card surface-card--outline">
                    <div><strong>Max open positions</strong></div>
                    <input
                      type="number"
                      min="1"
                      max="100"
                      step="1"
                      value={draft.maxOpenPositions}
                      onChange={(event) => updateDraft(account.id, { maxOpenPositions: event.target.value })}
                    />
                  </label>
                </div>

                <p className="ui-muted" style={{ marginTop: 12 }}>
                  Last automated submission: {formatDateTime(account.last_automated_submission_at)}.
                </p>

                <ActionBar compact>
                  <Button
                    type="button"
                    variant="ghost"
                    onClick={() => handleSave(account.id)}
                    disabled={busyKey === `save:${account.id}`}
                  >
                    {busyKey === `save:${account.id}` ? 'Saving...' : 'Save automation'}
                  </Button>
                  <Button
                    type="button"
                    variant="ghost"
                    onClick={() => handleRefresh(account.id)}
                    disabled={busyKey === `refresh:${account.id}`}
                  >
                    {busyKey === `refresh:${account.id}` ? 'Refreshing...' : 'Refresh status'}
                  </Button>
                  <Button
                    type="button"
                    variant="ghost"
                    onClick={() => handleStart(account.account_environment || 'paper', account.id)}
                    disabled={busyKey.startsWith('start:')}
                  >
                    Reconnect
                  </Button>
                  <Button
                    type="button"
                    variant="subtle"
                    onClick={() => handleUnlink(account.id)}
                    disabled={busyKey === `unlink:${account.id}`}
                  >
                    {busyKey === `unlink:${account.id}` ? 'Disconnecting...' : 'Unlink'}
                  </Button>
                </ActionBar>
              </div>
            </div>
          )
        })}
      </div>
    </SectionCard>
  )
}
