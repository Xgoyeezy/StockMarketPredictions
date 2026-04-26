import ActionBar from './ActionBar'
import { TextField } from './FormFields'

export default function DataToolbar({
  searchValue,
  onSearchChange,
  searchPlaceholder = 'Search',
  filters = null,
  actions = null,
  searchDelayLabel = '',
  searchInputId = '',
}) {
  return (
    <div className="data-toolbar">
      <div className="data-toolbar__search">
        <TextField
          id={searchInputId || undefined}
          value={searchValue}
          onChange={(e) => onSearchChange?.(e.target.value)}
          placeholder={searchPlaceholder}
          hint={searchDelayLabel}
          className="data-toolbar__field"
        />
      </div>
      <ActionBar className="data-toolbar__controls" compact>
        {filters}
        {actions}
      </ActionBar>
    </div>
  )
}
