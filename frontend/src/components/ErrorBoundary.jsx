import { Component } from 'react'

/**
 * Catches uncaught errors in the React tree. A single broken tab or
 * component can't blank the whole app. Two usage modes:
 *
 *   1. Global wrapper around the whole app (catches catastrophic failures)
 *   2. Per-tab wrapper: if Board Optimizer crashes, other tabs keep working
 *
 * Props:
 *   label    – name shown in the error card (e.g. "Board Optimizer")
 *   fallback – optional custom fallback element
 *   reset    – optional callback: if provided, renders a "Try again" button
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
    console.error('[ErrorBoundary]', this.props.label || 'unknown', error, info?.componentStack?.slice(0, 400))
  }

  render() {
    if (this.state.error) {
      if (this.props.fallback !== undefined) return this.props.fallback
      const label = this.props.label || 'component'
      return (
        <div style={{
          padding: '20px 22px',
          margin: '20px 0',
          background: 'rgba(255, 68, 68, 0.06)',
          border: '1px solid rgba(255, 68, 68, 0.3)',
          borderRadius: 12,
          color: 'var(--red-bright)',
          fontFamily: '"Barlow Condensed", sans-serif',
        }}>
          <div style={{ fontWeight: 800, fontSize: 13, letterSpacing: 2, textTransform: 'uppercase', marginBottom: 6 }}>
            {label} crashed
          </div>
          <div style={{ fontSize: 12, color: 'rgba(255, 255, 255, 0.65)', marginBottom: 12 }}>
            {String(this.state.error?.message || this.state.error)}
          </div>
          {/* Always offer a way to reset — prevents a stuck tab */}
          <button
            onClick={() => this.setState({ error: null })}
            style={{
              padding: '7px 16px', borderRadius: 8, cursor: 'pointer',
              background: 'rgba(255, 68, 68, 0.12)',
              border: '1px solid rgba(255, 68, 68, 0.4)',
              color: 'var(--red-bright)',
              fontFamily: '"Barlow Condensed", sans-serif',
              fontWeight: 800, fontSize: 11, letterSpacing: 1.5,
              textTransform: 'uppercase',
            }}
          >Retry</button>
        </div>
      )
    }
    return this.props.children
  }
}
