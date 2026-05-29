import { motion } from 'motion/react'

/**
 * Custom Last-5 bar chart — taller bars with gradients and glow,
 * grows from zero on mount, includes prop-line dashed marker.
 *
 * Replaces the recharts implementation for a more dramatic visual.
 *
 * Props:
 *   data       : Array of { label, val, isNA, won } (oldest→newest, left→right)
 *   propLine   : number (0 = no line)
 *   playerName : string
 *   maxBarHeight (default 120)
 */
export default function Last5Bars({ data, propLine = 0, playerName, maxBarHeight = 140 }) {
  if (!data || !data.length) {
    return <div style={{ color: 'var(--muted)', fontSize: 13, padding: '16px 0' }}>No recent matches</div>
  }

  // Compute max for scaling
  const numericVals = data.filter(d => !d.isNA).map(d => d.val)
  const maxVal = numericVals.length ? Math.max(...numericVals, propLine || 0) : (propLine || 1)
  const yScale = (v) => maxBarHeight * (v / (maxVal * 1.15))

  const propLineY = propLine > 0 ? yScale(propLine) : null

  return (
    <div style={{
      position: 'relative',
      display: 'flex',
      alignItems: 'flex-end',
      justifyContent: 'space-around',
      gap: 12,
      padding: '24px 8px 8px',
      minHeight: maxBarHeight + 60,
    }}>
      {/* Prop line dashed marker */}
      {propLineY != null && (
        <div style={{
          position: 'absolute',
          left: 8, right: 8,
          bottom: 50 + propLineY,
          borderTop: '2px dashed var(--amber)',
          opacity: 0.7,
          pointerEvents: 'none',
        }}>
          <span style={{
            position: 'absolute',
            right: 0,
            top: -22,
            fontSize: 10,
            fontFamily: '"Barlow Condensed", sans-serif',
            fontWeight: 800,
            letterSpacing: 1,
            color: 'var(--amber)',
            background: 'rgba(0,0,0,0.6)',
            padding: '2px 8px',
            borderRadius: 4,
            border: '1px solid rgba(255, 179, 0, 0.4)',
          }}>LINE {propLine.toFixed(1)}</span>
        </div>
      )}

      {data.map((d, i) => {
        if (d.isNA) {
          return (
            <div key={i} style={{ flex: 1, display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'flex-end' }}>
              <div style={{
                fontFamily: '"Barlow Condensed", sans-serif',
                fontWeight: 800,
                fontSize: 11,
                color: 'var(--muted)',
                marginBottom: 6,
              }}>N/A</div>
              <div style={{
                width: '70%', height: 8,
                background: 'rgba(74, 106, 80, 0.2)',
                borderRadius: 4,
                marginBottom: 12,
              }} />
              <div style={{
                fontSize: 10,
                color: 'var(--muted)',
                textAlign: 'center',
                lineHeight: 1.3,
                fontFamily: '"Barlow Condensed", sans-serif',
              }}>
                {d.label.split('\n').map((line, k) => <div key={k}>{line}</div>)}
              </div>
            </div>
          )
        }

        const h = yScale(d.val)
        const over = propLine > 0 && d.val > propLine
        const under = propLine > 0 && d.val < propLine
        const push = propLine > 0 && d.val === propLine

        const colors = over
          ? { bar: 'linear-gradient(180deg, #00FF87 0%, #00A854 100%)', glow: 'rgba(0, 230, 118, 0.45)', text: '#00FF87' }
          : under
            ? { bar: 'linear-gradient(180deg, #FF3B5C 0%, #B0223A 100%)', glow: 'rgba(255, 68, 68, 0.45)', text: '#FF3B5C' }
            : push
              ? { bar: 'linear-gradient(180deg, #888 0%, #555 100%)', glow: 'rgba(150, 150, 150, 0.25)', text: '#999' }
              : { bar: 'linear-gradient(180deg, #555 0%, #333 100%)', glow: 'rgba(150, 150, 150, 0.2)', text: '#aaa' }

        return (
          <div key={i} style={{ flex: 1, display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'flex-end' }}>
            {/* Value label */}
            <div style={{
              fontFamily: '"Barlow Condensed", sans-serif',
              fontWeight: 900,
              fontSize: 16,
              color: colors.text,
              marginBottom: 6,
              textShadow: `0 0 8px ${colors.glow}`,
            }}>{d.val}</div>

            {/* Bar */}
            <motion.div
              initial={{ height: 0 }}
              animate={{ height: h }}
              transition={{ duration: 0.7, delay: i * 0.1, ease: [0.34, 1.56, 0.64, 1] }}
              style={{
                width: '70%',
                minHeight: 6,
                background: colors.bar,
                borderRadius: '6px 6px 0 0',
                boxShadow: `0 0 14px ${colors.glow}, 0 -2px 8px ${colors.glow} inset`,
                marginBottom: 12,
              }}
            />

            {/* Date / opponent label */}
            <div style={{
              fontSize: 10,
              color: 'var(--muted)',
              textAlign: 'center',
              lineHeight: 1.3,
              fontFamily: '"Barlow Condensed", sans-serif',
              letterSpacing: 0.5,
            }}>
              {d.label.split('\n').map((line, k) => (
                <div key={k} style={{
                  color: k === 0 ? 'var(--muted)' : 'rgba(74, 106, 80, 0.7)',
                  fontWeight: k === 0 ? 700 : 600,
                }}>{line}</div>
              ))}
            </div>
          </div>
        )
      })}
    </div>
  )
}
