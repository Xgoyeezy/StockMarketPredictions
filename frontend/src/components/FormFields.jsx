import { useId } from 'react'
import { NativeInput, NativeSelect, NativeTextArea, joinClasses } from './ControlPrimitives'

function joinDescribedBy(...values) {
  const ids = values.filter(Boolean)
  return ids.length ? ids.join(' ') : undefined
}

function FieldShell({
  label,
  hint,
  error = '',
  className = '',
  children,
  controlId = '',
  hintId = '',
  errorId = '',
  required = false,
}) {
  return (
    <label className={joinClasses('ui-field', error && 'ui-field--invalid', className)} htmlFor={controlId || undefined}>
      {label ? (
        <span className="ui-field__label">
          <span>{label}</span>
          {required ? <span className="ui-field__required">Required</span> : null}
        </span>
      ) : null}
      {children}
      {hint ? <span className="ui-field__hint" id={hintId}>{hint}</span> : null}
      {error ? <span className="ui-field__error" id={errorId}>{error}</span> : null}
    </label>
  )
}

export function TextField({
  label = '',
  hint = '',
  error = '',
  className = '',
  inputClassName = '',
  ariaLabel = '',
  id = '',
  ...props
}) {
  const generatedId = useId()
  const controlId = id || generatedId
  const hintId = hint ? `${controlId}-hint` : undefined
  const errorId = error ? `${controlId}-error` : undefined

  return (
    <FieldShell
      label={label}
      hint={hint}
      error={error}
      className={className}
      controlId={controlId}
      hintId={hintId}
      errorId={errorId}
      required={Boolean(props.required)}
    >
      <NativeInput
        {...props}
        id={controlId}
        aria-label={!label ? ariaLabel || undefined : undefined}
        aria-describedby={joinDescribedBy(hintId, errorId)}
        aria-invalid={error ? 'true' : undefined}
        className={joinClasses('ui-input', error && 'ui-input--invalid', 'ui-field__control', inputClassName, props.className)}
      />
    </FieldShell>
  )
}

export function SelectField({
  label = '',
  hint = '',
  error = '',
  className = '',
  inputClassName = '',
  children,
  ariaLabel = '',
  id = '',
  ...props
}) {
  const generatedId = useId()
  const controlId = id || generatedId
  const hintId = hint ? `${controlId}-hint` : undefined
  const errorId = error ? `${controlId}-error` : undefined

  return (
    <FieldShell
      label={label}
      hint={hint}
      error={error}
      className={className}
      controlId={controlId}
      hintId={hintId}
      errorId={errorId}
      required={Boolean(props.required)}
    >
      <NativeSelect
        {...props}
        id={controlId}
        aria-label={!label ? ariaLabel || undefined : undefined}
        aria-describedby={joinDescribedBy(hintId, errorId)}
        aria-invalid={error ? 'true' : undefined}
        className={joinClasses('ui-input', 'ui-input--select', error && 'ui-input--invalid', 'ui-field__control', inputClassName, props.className)}
      >
        {children}
      </NativeSelect>
    </FieldShell>
  )
}

export function TextAreaField({
  label = '',
  hint = '',
  error = '',
  className = '',
  inputClassName = '',
  ariaLabel = '',
  id = '',
  ...props
}) {
  const generatedId = useId()
  const controlId = id || generatedId
  const hintId = hint ? `${controlId}-hint` : undefined
  const errorId = error ? `${controlId}-error` : undefined

  return (
    <FieldShell
      label={label}
      hint={hint}
      error={error}
      className={className}
      controlId={controlId}
      hintId={hintId}
      errorId={errorId}
      required={Boolean(props.required)}
    >
      <NativeTextArea
        {...props}
        id={controlId}
        aria-label={!label ? ariaLabel || undefined : undefined}
        aria-describedby={joinDescribedBy(hintId, errorId)}
        aria-invalid={error ? 'true' : undefined}
        className={joinClasses('ui-input', error && 'ui-input--invalid', 'ui-field__control', inputClassName, props.className)}
      />
    </FieldShell>
  )
}

export function ToggleField({ label, hint = '', className = '', checked, onChange, id = '', ...props }) {
  const generatedId = useId()
  const controlId = id || generatedId
  const hintId = hint ? `${controlId}-hint` : undefined

  return (
    <label className={joinClasses('ui-toggle', className)} htmlFor={controlId}>
      <NativeInput
        {...props}
        id={controlId}
        type="checkbox"
        checked={checked}
        onChange={onChange}
        aria-describedby={hintId}
        className={joinClasses('ui-toggle__input', props.className)}
      />
      <span className="ui-toggle__copy">
        <span className="ui-toggle__label">{label}</span>
        {hint ? <span className="ui-toggle__hint" id={hintId}>{hint}</span> : null}
      </span>
    </label>
  )
}
