import Button from '../Button'
import EmptyState from '../EmptyState'
import ListTable, { ListCell } from '../ListTable'
import StatusBadge from '../StatusBadge'

export default function StrategyVersionPanel({
  versions = [],
  activeVersionId = '',
  onCreateVersion,
  onRollback,
}) {
  return (
    <div className="ui-stack">
      <div className="ui-action-row">
        <Button variant="solid" onClick={onCreateVersion}>Create Version</Button>
      </div>
      {versions.length ? (
        <ListTable>
          {versions.map((version) => (
            <div key={version.id} className="ui-list-row">
              <ListCell
                kicker={`Version ${version.version_number}`}
                title={version.name}
                meta={version.description || version.source_type || 'internal'}
                badges={[
                  <StatusBadge key="status" value={version.status} />,
                  activeVersionId === version.id ? <StatusBadge key="active" value="active" tone="positive" /> : null,
                ]}
              />
              <div className="ui-list-row__actions">
                <Button size="sm" disabled={activeVersionId === version.id} onClick={() => onRollback?.(version)}>Rollback</Button>
              </div>
            </div>
          ))}
        </ListTable>
      ) : (
        <EmptyState title="No locked versions recorded." description="Create a first version to freeze strategy configuration before promotion gates rely on it." eyebrow="Versions" compact />
      )}
    </div>
  )
}
