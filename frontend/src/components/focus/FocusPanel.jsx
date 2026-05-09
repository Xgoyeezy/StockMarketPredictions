export default function FocusPanel({ children }) {
  return (
    <section className="focus-panel" aria-label="Primary decision surface" data-testid="focus-panel">
      {children}
    </section>
  )
}
