import Chip from './Chip'
import { joinClasses } from './ControlPrimitives'

export default function ChecklistChip({
  done = false,
  children,
  className = '',
  tone,
  ...props
}) {
  return (
    <Chip
      {...props}
      tone={tone || (done ? 'positive' : 'neutral')}
      className={joinClasses('ui-checklist-chip', done ? 'ui-checklist-chip--done' : '', className)}
    >
      <span className="ui-checklist-chip__mark" aria-hidden="true" />
      <span>{children}</span>
    </Chip>
  )
}
