import { Component } from 'react'

/**
 * Catches uncaught errors in the React tree so a single broken component
 * (e.g. a third-party widget that throws on load) can't blank the whole app.
 */
export default class ErrorBoundary extends Component {
  constructor(props) {
    super(props)
    this.state = { error: null }
  }

  static getDerivedStateFromError(error) {
    return { error }
  }

  componentDidCatch(error, info) {
    // eslint-disable-next-line no-console
    console.error('[ErrorBoundary]', error, info)
  }

  render() {
    if (this.state.error) {
      // If a fallback was provided, render it. Otherwise show a minimal notice.
      if (this.props.fallback !== undefined) return this.props.fallback
      return (
        <div style={{
          padding: '24px',
          maxWidth: 600,
          margin: '40px auto',
          background: 'rgba(255, 68, 68, 0.06)',
          border: '1px solid rgba(255, 68, 68, 0.3)',
          borderRadius: 12,
          color: 'var(--red-bright)',
          fontFamily: '"Barlow Condensed", sans-serif',
        }}>
          <div style={{ fontWeight: 800, fontSize: 14, letterSpacing: 2, textTransform: 'uppercase', marginBottom: 8 }}>
            Something went wrong
          </div>
          <div style={{ fontSize: 13, color: 'rgba(255, 255, 255, 0.7)' }}>
            {String(this.state.error?.message || this.state.error)}
          </div>
        </div>
      )
    }
    return this.props.children
  }
}
