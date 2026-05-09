import { useState } from 'react'
import Button from '../Button'
import EmptyState from '../EmptyState'
import { TextField } from '../FormFields'
import StatusBadge from '../StatusBadge'

export default function LiveAuthorizationPanel({
  authorizations = [],
  strategyId = '',
  onCreate,
  onRevoke,
  busy = false,
}) {
  const [draft, setDraft] = useState({
    linked_account_id: '',
    max_capital_allocation: 25000,
    max_daily_loss: 500,
    max_order_notional: 2500,
    signed: true,
  })

  function submit(event) {
    event.preventDefault()
    onCreate?.({
      strategy_id: strategyId,
      linked_account_id: draft.linked_account_id,
      max_capital_allocation: Number(draft.max_capital_allocation || 0),
      max_daily_loss: Number(draft.max_daily_loss || 0),
      max_order_notional: Number(draft.max_order_notional || 0),
      authorized_mode: 'approval_required',
      authorization_type: 'supervised_live',
      signed: Boolean(draft.signed),
    })
  }

  return (
    <div className="ui-stack">
      <form className="ui-form-grid" onSubmit={submit}>
        <TextField label="Alpaca account ID" value={draft.linked_account_id} onChange={(event) => setDraft((current) => ({ ...current, linked_account_id: event.target.value }))} required />
        <TextField label="Capital allocation" type="number" min="0" value={draft.max_capital_allocation} onChange={(event) => setDraft((current) => ({ ...current, max_capital_allocation: event.target.value }))} />
        <TextField label="Daily loss cap" type="number" min="0" value={draft.max_daily_loss} onChange={(event) => setDraft((current) => ({ ...current, max_daily_loss: event.target.value }))} />
        <TextField label="Order notional cap" type="number" min="0" value={draft.max_order_notional} onChange={(event) => setDraft((current) => ({ ...current, max_order_notional: event.target.value }))} />
        <label className="ui-checkbox">
          <input type="checkbox" checked={draft.signed} onChange={(event) => setDraft((current) => ({ ...current, signed: event.target.checked }))} />
          <span>Risk acknowledgement signed</span>
        </label>
        <div className="ui-action-row">
          <Button type="submit" variant="solid" disabled={busy || !strategyId}>Create Authorization</Button>
        </div>
      </form>
      <div className="ui-list-shell">
        {authorizations.length ? (
          authorizations.map((item) => (
            <div key={item.id} className="ui-list-row">
              <span>{item.linked_account_id}</span>
              <StatusBadge value={item.status} tone={item.status === 'signed' ? 'positive' : item.status === 'revoked' ? 'negative' : 'warning'} />
              {item.status !== 'revoked' ? <Button variant="ghost" size="sm" onClick={() => onRevoke?.(item)}>Revoke</Button> : null}
            </div>
          ))
        ) : (
          <EmptyState
            title="No signed live authorization yet."
            description="Choose an Alpaca account and set capital, daily loss, and order notional limits before arming a strategy."
            eyebrow="Authorization"
            compact
          />
        )}
      </div>
    </div>
  )
}
