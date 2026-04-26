import { joinClasses } from './ControlPrimitives'

export function formatValueFlowText(fromValue = '', toValue = '', separator = ' to ') {
  return [fromValue, toValue]
    .filter((item) => item !== null && item !== undefined && item !== '')
    .join(separator)
}

export default function ValueFlow({
  fromLabel = 'From',
  fromValue = '',
  toLabel = 'To',
  toValue = '',
  className = '',
}) {
  return (
    <span className={joinClasses('ui-value-flow', className)}>
      <span className="ui-value-flow__pair">
        <span className="ui-value-flow__label">{fromLabel}</span>
        <span className="ui-value-flow__value">{fromValue}</span>
      </span>
      <span className="ui-value-flow__arrow" aria-hidden="true" />
      <span className="ui-value-flow__pair">
        <span className="ui-value-flow__label">{toLabel}</span>
        <span className="ui-value-flow__value">{toValue}</span>
      </span>
    </span>
  )
}
