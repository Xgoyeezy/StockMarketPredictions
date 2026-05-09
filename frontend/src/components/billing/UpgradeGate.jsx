import Button from '../Button'
import StatusBadge from '../StatusBadge'

export default function UpgradeGate({
  entitlementKey = '',
  currentPlan = '',
  requiredPlan = '',
  onUpgrade,
}) {
  return (
    <div className="ui-panel ui-panel--section">
      <div className="ui-panel__header">
        <div className="ui-panel__copy">
          <div className="ui-panel__eyebrow">Entitlement gate</div>
          <h2 className="ui-panel__title">{entitlementKey || 'Premium feature'}</h2>
          <p className="ui-panel__subtitle">Current plan {currentPlan || 'unknown'} requires {requiredPlan || 'a higher tier'} for this action.</p>
        </div>
        <StatusBadge value="upgrade required" tone="warning" />
      </div>
      {onUpgrade ? (
        <div className="ui-panel__body">
          <Button variant="solid" onClick={onUpgrade}>Review Plan</Button>
        </div>
      ) : null}
    </div>
  )
}
