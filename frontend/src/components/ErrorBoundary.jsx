import React from 'react'
import ErrorState from './ErrorState'

export default class ErrorBoundary extends React.Component {
  constructor(props) {
    super(props)
    this.state = { hasError: false, error: '' }
  }

  static getDerivedStateFromError(error) {
    return { hasError: true, error: error?.message || 'Unexpected frontend error.' }
  }

  componentDidCatch(error, errorInfo) {
    console.error('Frontend crash boundary:', error, errorInfo)
  }

  handleReload = () => {
    window.location.reload()
  }

  render() {
    if (this.state.hasError) {
      return (
        <div className="ui-shell__page">
          <ErrorState
            title="Something broke in the interface."
            description={this.state.error}
            eyebrow="Frontend recovery"
            actionLabel="Reload app"
            onAction={this.handleReload}
          />
        </div>
      )
    }

    return this.props.children
  }
}
