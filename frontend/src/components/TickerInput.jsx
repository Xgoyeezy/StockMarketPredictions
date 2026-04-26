import { useEffect, useMemo, useState } from 'react'
import { getTickerSuggestions } from '../api/client'
import { NativeInput, joinClasses } from './ControlPrimitives'

export default function TickerInput({
  value = '',
  onChange,
  placeholder = 'Tickers',
  id = 'ticker-list-input',
  inputId = '',
  ariaLabel = '',
  className = '',
  wrapperClassName = '',
  label = '',
  hint = '',
  error = '',
  required = false,
}) {
  const [suggestions, setSuggestions] = useState([])
  const [isFocused, setIsFocused] = useState(false)
  const controlId = inputId || `${id}-input`
  const hintId = hint ? `${controlId}-hint` : undefined
  const errorId = error ? `${controlId}-error` : undefined

  const activeToken = useMemo(() => {
    const tokens = String(value || '').split(',').map((item) => item.trim()).filter(Boolean)
    return tokens[tokens.length - 1] || String(value || '').trim()
  }, [value])

  useEffect(() => {
    const query = String(activeToken || '').trim()
    if (!isFocused || !query) {
      setSuggestions([])
      return
    }
    const timer = window.setTimeout(() => {
      getTickerSuggestions(query, 8)
        .then((payload) => setSuggestions(payload.results || []))
        .catch(() => setSuggestions([]))
    }, 150)
    return () => window.clearTimeout(timer)
  }, [activeToken, isFocused])

  const input = (
    <>
      <NativeInput
        id={controlId}
        className={joinClasses('ui-input', 'ui-input--ticker', error && 'ui-input--invalid', className)}
        list={id}
        aria-label={ariaLabel || undefined}
        aria-describedby={[hintId, errorId].filter(Boolean).join(' ') || undefined}
        aria-invalid={error ? 'true' : undefined}
        value={value}
        onChange={(event) => onChange(event.target.value.toUpperCase())}
        onFocus={() => setIsFocused(true)}
        onBlur={() => {
          setIsFocused(false)
          window.setTimeout(() => setSuggestions([]), 0)
        }}
        placeholder={placeholder}
        autoCapitalize="characters"
        autoCorrect="off"
        spellCheck={false}
      />
      <datalist id={id}>
        {suggestions.map((ticker) => <option key={ticker} value={ticker} />)}
      </datalist>
    </>
  )

  if (!label && !hint && !error && !required) {
    return input
  }

  return (
    <label className={joinClasses('ui-field', error && 'ui-field--invalid', wrapperClassName)} htmlFor={controlId}>
      {label ? (
        <span className="ui-field__label">
          <span>{label}</span>
          {required ? <span className="ui-field__required">Required</span> : null}
        </span>
      ) : null}
      {input}
      {hint ? <span className="ui-field__hint" id={hintId}>{hint}</span> : null}
      {error ? <span className="ui-field__error" id={errorId}>{error}</span> : null}
    </label>
  )
}
