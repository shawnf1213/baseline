import { useState, useEffect, useMemo, useCallback } from 'react'
import { motion, AnimatePresence } from 'motion/react'
import NumberFlow from '@number-flow/react'
import LoadingSpinner from '../components/LoadingSpinner'
import LeanBadge from '../components/LeanBadge'
import ConfidenceGauge from '../components/ConfidenceGauge'
import EnvironmentBanner from '../components/EnvironmentBanner'
import Last5Bars from '../components/Last5Bars'
import { scrapeBoard, analyzeBoard } from '../utils/api'
import { api } from '../utils/api'

// Prop type → badge color
const PROP_BADGE = {
  'Aces':              { bg: 'rgba(107, 159, 255, 0.18)', fg: 'var(--hard-blue)', border: 'rgba(107, 159, 255, 0.5)' },
  'Double Faults':     { bg: 'rgba(255, 107, 53, 0.18)',   fg: 'var(--clay-rust)',  border: 'rgba(255, 107, 53, 0.5)' },
  'Break Points Won':  { bg: 'rgba(0, 230, 118, 0.18)',    fg: 'var(--green-bright)', border: 'rgba(0, 230, 118, 0.5)' },
  'Total Games':       { bg: 'rgba(170, 102, 255, 0.18)',  fg: '#aa66ff',           border: 'rgba(170, 102, 255, 0.5)' },
}

const STAT_KEY_FOR_PROP = {
  'Aces':              'aces',
  'Double Faults':     'double_faults',
  'Total Games':       'total_match_games',
  'Break Points Won':  'bp_converted_count',
}

function SectionDivider({ label }) {
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 16, margin: '24px 0 14px' }}>
      <div style={{ flex: 1, height: 1, background: 'linear-gradient(90deg, transparent, rgba(0, 230, 118, 0.2), transparent)' }} />
      <span style={{
        fontFamily: '"Barlow Condensed", sans-serif', fontWeight: 800,
        fontSize: 10, letterSpacing: '0.3em', textTransform: 'uppercase',
        color: 'var(--green-mid)', whiteSpace: 'nowrap',
      }}>{label}</span>
      <div style={{ flex: 1, height: 1, background: 'linear-gradient(90deg, transparent, rgba(0, 230, 118, 0.2), transparent)' }} />
    </div>
  )
}

function formatAgo(scrapedAt) {
  if (!scrapedAt) return ''
  const secs = Math.floor(Date.now() / 1000 - scrapedAt)
  if (secs < 60) return `${secs}s ago`
  const mins = Math.floor(secs / 60)
  if (mins < 60) return `${mins} min ago`
  const hours = Math.floor(mins / 60)
  return `${hours}h ${mins % 60}m ago`
}

function buildLast5Data(matches, statKey, line) {
  if (!matches || !statKey) return []
  const last5 = matches.slice(0, 5).reverse()
  return last5.map(m => {
    let v = m[statKey] ?? null
    if (v == null && statKey === 'total_match_games' && m.score) {
      const sets = m.score.trim().split(/\s+/)
      let total = 0, ok = true
      for (const s of sets) {
        const parts = s.split('-').map(Number)
        if (parts.length !== 2 || parts.some(isNaN)) { ok = false; break }
        total += parts[0] + parts[1]
      }
      if (ok && total > 0) v = total
    }
    const isNA = v == null
    const dateStr = m.date || ''
    const opp = m.opponent_abbr || (m.opponent || '').split(' ').pop() || ''
    const label = dateStr && opp ? `${dateStr}\nvs ${opp}` : dateStr || opp || '?'
    return { label, val: isNA ? 0 : Math.round(v * 10) / 10, isNA, won: m.won }
  })
}

function PropBadge({ propType }) {
  const s = PROP_BADGE[propType] || PROP_BADGE['Aces']
  return (
    <span style={{
      padding: '4px 12px',
      borderRadius: 999,
      background: s.bg,
      color: s.fg,
      border: `1px solid ${s.border}`,
      fontFamily: '"Barlow Condensed", sans-serif',
      fontWeight: 800, fontSize: 10, letterSpacing: 1.5,
      textTransform: 'uppercase',
      whiteSpace: 'nowrap',
    }}>{propType}</span>
  )
}

function TourBadge({ tour }) {
  if (!tour) return null
  const isWta = tour === 'WTA'
  // ATP keeps the existing green-toggle palette; WTA uses pink so the two
  // tours are instantly distinguishable in a mixed board feed.
  const style = isWta
    ? { bg: 'rgba(255, 99, 178, 0.18)', fg: '#ff63b2', border: 'rgba(255, 99, 178, 0.5)' }
    : { bg: 'rgba(0, 230, 118, 0.18)', fg: 'var(--green-bright)', border: 'rgba(0, 230, 118, 0.5)' }
  return (
    <span style={{
      padding: '3px 10px',
      borderRadius: 999,
      background: style.bg,
      color: style.fg,
      border: `1px solid ${style.border}`,
      fontFamily: '"Barlow Condensed", sans-serif',
      fontWeight: 900, fontSize: 10, letterSpacing: 2,
      textTransform: 'uppercase',
      whiteSpace: 'nowrap',
    }}>{tour}</span>
  )
}

function ConfPill({ confidence }) {
  const c = confidence || 0
  const color = c >= 80 ? 'var(--green-bright)'
    : c >= 66 ? 'var(--green-mid)'
    : c >= 40 ? 'var(--amber)'
    : 'var(--red-bright)'
  return (
    <span style={{
      display: 'inline-flex', alignItems: 'center', gap: 6,
      padding: '3px 9px', borderRadius: 999,
      fontFamily: '"Barlow Condensed", sans-serif',
      fontWeight: 800, fontSize: 11, letterSpacing: 1,
      color, background: 'rgba(0,0,0,0.3)',
      border: `1px solid ${color}55`,
    }}>
      <span style={{ width: 6, height: 6, borderRadius: '50%', background: color, boxShadow: `0 0 6px ${color}` }} />
      {c}%
    </span>
  )
}

function PropCard({ item, isBestBet = false, defaultExpanded = false }) {
  const [expanded, setExpanded] = useState(defaultExpanded)
  const pp     = item.pp_prop || {}
  const player = item.player
  const oppName = item.opponent?.name || pp.opponent_name || '—'
  const lean   = item.lean
  const conf   = item.confidence || 0
  const proj   = item.model_projection
  const line   = pp.prop_line
  const edge   = item.edge
  const result = item.result
  const propType = pp.prop_type

  const cardTint = lean === 'OVER'
    ? 'rgba(0, 230, 118, 0.05)'
    : lean === 'UNDER'
      ? 'rgba(255, 68, 68, 0.05)'
      : 'rgba(14, 24, 18, 0.55)'
  const borderColor = isBestBet
    ? 'rgba(255, 214, 10, 0.5)'
    : conf >= 80
      ? 'rgba(0, 230, 118, 0.45)'
      : 'var(--card-border)'

  const playerLabel = player?.name || pp.player_name || 'Unknown player'

  return (
    <motion.div
      layout
      transition={{ layout: { type: 'spring', stiffness: 200, damping: 28 } }}
      style={{
        background: cardTint,
        border: `1px solid ${borderColor}`,
        borderRadius: 16,
        padding: '18px 20px',
        boxShadow: isBestBet
          ? '0 6px 28px rgba(255, 214, 10, 0.18), 0 0 0 1px rgba(255, 214, 10, 0.3) inset'
          : '0 4px 18px rgba(0, 0, 0, 0.35)',
        overflow: 'hidden',
      }}
    >
      {isBestBet && (
        <div style={{
          display: 'inline-flex', alignItems: 'center', gap: 6,
          fontFamily: '"Barlow Condensed", sans-serif',
          fontWeight: 900, fontSize: 10, letterSpacing: 2.5,
          color: '#000',
          background: 'linear-gradient(135deg, #FFD60A, #FFA500)',
          padding: '4px 12px', borderRadius: 999,
          marginBottom: 12,
          textTransform: 'uppercase',
          textShadow: '0 1px 0 rgba(255,255,255,0.3)',
        }}>★ Best Bet</div>
      )}

      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', gap: 12, marginBottom: 12, flexWrap: 'wrap' }}>
        <div style={{ flex: '1 1 200px', minWidth: 0 }}>
          <div style={{
            fontFamily: '"Barlow Condensed", sans-serif',
            fontWeight: 900,
            fontSize: isBestBet ? 26 : 22,
            color: '#fff', letterSpacing: 0.5, lineHeight: 1.15,
            whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis',
          }}>{playerLabel}</div>
          <div style={{
            fontSize: 12, color: 'var(--muted)', marginTop: 2,
            fontFamily: '"Barlow Condensed", sans-serif', letterSpacing: 0.5,
            whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis',
          }}>vs {oppName}</div>
          {pp.tournament && (
            <div style={{ fontSize: 10, color: 'rgba(255,255,255,0.4)', marginTop: 4, fontFamily: '"Barlow Condensed", sans-serif', letterSpacing: 1, textTransform: 'uppercase' }}>
              {pp.tournament}{item.surface && ` · ${item.surface}`}
            </div>
          )}
        </div>
        <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'flex-end', gap: 6 }}>
          <TourBadge tour={item.tour} />
          <PropBadge propType={propType} />
        </div>
      </div>

      <div style={{ display: 'grid', gridTemplateColumns: '1fr auto 1fr', alignItems: 'center', gap: 14, marginBottom: 12 }}>
        <div>
          <div style={{ fontSize: 9, color: 'var(--muted)', fontFamily: '"Barlow Condensed", sans-serif', fontWeight: 700, letterSpacing: 2, textTransform: 'uppercase' }}>PP Line</div>
          <div style={{ fontSize: 32, fontWeight: 900, color: '#fff', fontFamily: '"Barlow Condensed", sans-serif', lineHeight: 1 }}>
            {line != null ? line.toFixed(1) : '—'}
          </div>
        </div>

        <div style={{ textAlign: 'center' }}>
          {proj != null ? (
            <LeanBadge lean={lean} size="sm" />
          ) : item.match_note ? (
            <span style={{
              fontSize: 10, fontFamily: '"Barlow Condensed", sans-serif',
              fontWeight: 800, letterSpacing: 1.5, padding: '4px 10px',
              borderRadius: 999, background: 'rgba(255, 255, 255, 0.04)',
              color: 'var(--muted)', border: '1px solid rgba(255, 255, 255, 0.1)',
              whiteSpace: 'nowrap',
            }}>NO DATA</span>
          ) : null}
        </div>

        <div style={{ textAlign: 'right' }}>
          {proj != null && (
            <>
              <div style={{ fontSize: 9, color: 'var(--muted)', fontFamily: '"Barlow Condensed", sans-serif', fontWeight: 700, letterSpacing: 2, textTransform: 'uppercase' }}>Model</div>
              <div style={{
                fontSize: 30, fontWeight: 900, color: 'var(--green-bright)',
                fontFamily: '"Barlow Condensed", sans-serif', lineHeight: 1,
                textShadow: '0 0 10px rgba(0, 230, 118, 0.4)',
              }}>{proj.toFixed(1)}</div>
              {edge != null && (
                <div style={{
                  marginTop: 2,
                  fontSize: 12, fontFamily: '"Barlow Condensed", sans-serif',
                  fontWeight: 800, letterSpacing: 1,
                  color: edge >= 0 ? 'var(--green-bright)' : 'var(--red-bright)',
                }}>
                  {edge >= 0 ? '▲' : '▼'} {edge >= 0 ? '+' : ''}{edge.toFixed(1)}
                </div>
              )}
            </>
          )}
        </div>
      </div>

      {/* Confidence + match note row */}
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: 8, marginBottom: 12, flexWrap: 'wrap' }}>
        {proj != null && <ConfPill confidence={conf} />}
        {item.match_note && (
          <span style={{ fontSize: 11, color: 'var(--muted)', fontFamily: '"Barlow Condensed", sans-serif', letterSpacing: 0.3 }}>
            {item.match_note}
          </span>
        )}
      </div>

      {/* More/Less button */}
      {proj != null && (
        <button
          onClick={() => setExpanded(e => !e)}
          style={{
            width: '100%', padding: '10px 14px',
            background: 'rgba(0, 230, 118, 0.08)',
            border: '1px solid rgba(0, 230, 118, 0.25)',
            borderRadius: 10, cursor: 'pointer',
            color: 'var(--green-bright)',
            fontFamily: '"Barlow Condensed", sans-serif',
            fontWeight: 800, fontSize: 12, letterSpacing: 2,
            textTransform: 'uppercase',
            transition: 'background 200ms ease',
          }}
        >
          {expanded ? 'Less ▴' : 'More ▾'}
        </button>
      )}

      {/* Expanded detail */}
      <AnimatePresence initial={false}>
        {expanded && result && (
          <motion.div
            key="exp"
            initial={{ height: 0, opacity: 0 }}
            animate={{ height: 'auto', opacity: 1 }}
            exit={{ height: 0, opacity: 0 }}
            transition={{ duration: 0.35, ease: 'easeOut' }}
            style={{ overflow: 'hidden' }}
          >
            <ExpandedDetail item={item} />
          </motion.div>
        )}
      </AnimatePresence>
    </motion.div>
  )
}

const stagger = {
  hidden: { opacity: 0 },
  show:   { opacity: 1, transition: { staggerChildren: 0.08, delayChildren: 0.05 } },
}
const staggerItem = {
  hidden: { opacity: 0, y: 12 },
  show:   { opacity: 1, y: 0, transition: { type: 'spring', stiffness: 240, damping: 26 } },
}

function ExpandedDetail({ item }) {
  const result = item.result
  const pp     = item.pp_prop || {}
  const line   = pp.prop_line
  const propType = pp.prop_type
  const surface = item.surface

  const last5Matches = result?.sofascore_surface_log || result?.player_surface_matches || []
  const statKey = STAT_KEY_FOR_PROP[propType]
  const last5Data = useMemo(() => buildLast5Data(last5Matches, statKey, line), [last5Matches, statKey, line])

  const p1Stats = result?.player_stats || {}
  const p2Stats = result?.opponent_stats || {}

  return (
    <motion.div variants={stagger} initial="hidden" animate="show"
      style={{ paddingTop: 18, display: 'flex', flexDirection: 'column', gap: 14 }}>

      {/* Model vs Book row with edge */}
      <motion.div variants={staggerItem} style={{
        display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: 10,
        background: 'rgba(255, 255, 255, 0.02)', borderRadius: 10, padding: '14px 12px',
      }}>
        {[
          ['Model', result.model_projection?.toFixed(1), 'var(--green-bright)'],
          ['Book',  line?.toFixed(1),                    '#fff'],
          ['Edge',  item.edge != null ? `${item.edge >= 0 ? '+' : ''}${item.edge.toFixed(1)}` : '—',
            item.edge >= 0 ? 'var(--green-bright)' : 'var(--red-bright)'],
        ].map(([lbl, val, col]) => (
          <div key={lbl} style={{ textAlign: 'center' }}>
            <div style={{ fontSize: 9, color: 'var(--muted)', fontFamily: '"Barlow Condensed", sans-serif', fontWeight: 700, letterSpacing: 2, textTransform: 'uppercase' }}>{lbl}</div>
            <div style={{ fontSize: 22, fontWeight: 900, color: col, fontFamily: '"Barlow Condensed", sans-serif', lineHeight: 1, marginTop: 2 }}>{val}</div>
          </div>
        ))}
      </motion.div>

      {/* Confidence gauge */}
      <motion.div variants={staggerItem} style={{ display: 'flex', justifyContent: 'center' }}>
        <ConfidenceGauge confidence={item.confidence || 0} size={130} />
      </motion.div>

      {/* Last 5 bar chart */}
      {last5Data.length > 0 && (
        <motion.div variants={staggerItem}>
          <div style={{
            fontFamily: '"Barlow Condensed", sans-serif', fontWeight: 800, fontSize: 10,
            letterSpacing: 2, textTransform: 'uppercase', color: 'var(--green-mid)',
            marginBottom: 8,
          }}>Last 5 on {surface}</div>
          <Last5Bars data={last5Data} propLine={line} maxBarHeight={90} />
        </motion.div>
      )}

      {/* Environment banner */}
      {result.environment && (
        <motion.div variants={staggerItem}>
          <EnvironmentBanner environment={result.environment} environmentLabel={result.environment_label} />
        </motion.div>
      )}

      {/* Stat comparison */}
      <motion.div variants={staggerItem} style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 10 }}>
        {[
          [item.player?.name, p1Stats],
          [item.opponent?.name, p2Stats],
        ].map(([name, d], idx) => (
          <div key={idx} style={{
            background: 'rgba(255, 255, 255, 0.02)', borderRadius: 10, padding: '12px 14px',
            border: '1px solid rgba(0, 230, 118, 0.1)',
          }}>
            <div style={{ fontFamily: '"Barlow Condensed", sans-serif', fontWeight: 800, fontSize: 13, color: '#fff', marginBottom: 8 }}>{name}</div>
            {[
              ['Aces/M', d.aces?.toFixed(1) ?? '—'],
              ['DFs/M', d.double_faults?.toFixed(1) ?? '—'],
              ['1st Srv W', d.first_serve_pts_won?.toFixed(0) ? `${d.first_serve_pts_won.toFixed(0)}%` : '—'],
              ['Ret 1st W', d.return_first_serve_pts_won?.toFixed(0) ? `${d.return_first_serve_pts_won.toFixed(0)}%` : '—'],
              ['BP Conv',  d.bp_converted?.toFixed(0) ? `${d.bp_converted.toFixed(0)}%` : '—'],
            ].map(([k, v]) => (
              <div key={k} style={{ display: 'flex', justifyContent: 'space-between', padding: '4px 0', fontSize: 11, borderBottom: '1px solid rgba(255,255,255,0.04)' }}>
                <span style={{ color: 'var(--muted)' }}>{k}</span>
                <span style={{ color: '#fff', fontWeight: 700, fontFamily: '"Barlow Condensed", sans-serif' }}>{v}</span>
              </div>
            ))}
          </div>
        ))}
      </motion.div>

      {/* Model explanation */}
      {result.plain_english_explanation && (
        <motion.div variants={staggerItem} style={{
          borderLeft: '3px solid var(--green-bright)',
          padding: '12px 14px',
          background: 'rgba(255, 255, 255, 0.02)',
          borderRadius: '0 8px 8px 0',
        }}>
          <div style={{ fontFamily: '"Barlow Condensed", sans-serif', fontWeight: 800, fontSize: 9, letterSpacing: 2, color: 'var(--green-bright)', marginBottom: 6 }}>Model Logic</div>
          <p style={{ fontSize: 12, color: 'rgba(255,255,255,0.75)', lineHeight: 1.6, margin: 0 }}>{result.plain_english_explanation}</p>
        </motion.div>
      )}

      {/* AI scouting */}
      {result.ai_writeup && (
        <motion.div variants={staggerItem} style={{
          padding: '14px 16px', position: 'relative',
          background: 'rgba(0, 230, 118, 0.04)',
          border: '1px solid rgba(0, 230, 118, 0.2)',
          borderRadius: 10,
        }}>
          <div style={{ fontFamily: '"Barlow Condensed", sans-serif', fontWeight: 800, fontSize: 9, letterSpacing: 2, color: 'var(--green-mid)', marginBottom: 8 }}>AI Scouting Report</div>
          <p style={{ fontSize: 12, color: 'rgba(255,255,255,0.85)', lineHeight: 1.7, margin: 0 }}>{result.ai_writeup}</p>
        </motion.div>
      )}
    </motion.div>
  )
}

export default function BoardOptimizer({ tour }) {
  const [loading, setLoading]   = useState(false)
  const [stage, setStage]       = useState('idle')   // idle | scraping | analyzing | done | error
  const [error, setError]       = useState(null)
  // Never auto-load on mount — the board analyze can block the backend for
  // minutes. The user must click "Load Board" explicitly.
  const [hasLoadedOnce, setHasLoadedOnce] = useState(false)
  const [board, setBoard]       = useState(null)
  const [scrapedAt, setScrapedAt] = useState(null)
  const [analyzedByTour, setAnalyzedByTour] = useState({})
  const [metaByTour,     setMetaByTour]     = useState({})

  const analyzed    = analyzedByTour[tour] || []
  const analyzeMeta = metaByTour[tour]     || null

  // Filters
  const [filterPropTypes, setFilterPropTypes] = useState(new Set(
    ['Aces', 'Double Faults', 'Break Points Won', 'Total Games']
  ))
  const [filterConf, setFilterConf] = useState('all')   // all | good | high
  const [filterLean, setFilterLean] = useState('all')   // all | over | under
  const [sortBy,     setSortBy]     = useState('confidence')  // confidence | edge | time
  const [showFilters, setShowFilters] = useState(false)

  // ── Why we cap to 20 ─────────────────────────────────────────────────────
  // The PrizePicks board has 500+ eligible tennis props. Each projection
  // does a full Sofascore + Tennis Abstract + scouting-report pass — ~10-30
  // seconds wall time per prop with cold Sofascore caches. Analysing the
  // whole board at once would tie up the backend for the better part of an
  // hour and break every other tab while it runs. 20 props matches the user-
  // visible "top of the board" and finishes in ~60-90 seconds.
  const ANALYZE_LIMIT = 20

  const BOARD_TIMEOUT_MS = 20_000   // 20s wall-clock guard

  const refresh = useCallback(async (force = false) => {
    setLoading(true); setError(null); setStage('scraping')
    if (force) {
      setAnalyzedByTour({})
      setMetaByTour({})
    }

    // 20-second wall-clock guard — never leave the user with an infinite spinner
    const timeoutId = setTimeout(() => {
      setLoading(false)
      setStage('error')
      setError('Board data unavailable — PrizePicks may be blocking server requests. Try the refresh button.')
    }, BOARD_TIMEOUT_MS)

    try {
      // Quick connectivity test: log raw board endpoint to console so
      // the user can paste it into a report if things still don't work.
      api.get('/api/board/test')
        .then(r => console.log('[BoardOptimizer] /api/board/test response:', r.data))
        .catch(e => console.error('[BoardOptimizer] /api/board/test failed:', e?.response?.status, e?.message))

      const b = await scrapeBoard(force)
      clearTimeout(timeoutId)
      setBoard(b); setScrapedAt(b.scraped_at)
      if (!b.ok && (!b.props || b.props.length === 0)) {
        setError(b.error || 'PrizePicks board unavailable — try refreshing.')
        setStage('error'); setLoading(false); return
      }
      if (!b.props || b.props.length === 0) {
        setAnalyzedByTour({ ATP: [], WTA: [] })
        setMetaByTour({})
        setStage('done'); setLoading(false); return
      }
      setStage('analyzing')
      const a = await analyzeBoard(b.props || [], tour, ANALYZE_LIMIT)
      setAnalyzedByTour(prev => ({ ...prev, [tour]: a.analyzed || [] }))
      setMetaByTour(prev => ({ ...prev, [tour]: a }))
      setHasLoadedOnce(true)
      setStage('done')
    } catch (e) {
      clearTimeout(timeoutId)
      setError(e.response?.data?.detail || e.message || 'Board fetch failed')
      setStage('error')
    } finally {
      clearTimeout(timeoutId)
      setLoading(false)
    }
  }, [tour])

  // No auto-load on mount. When the user switches tours after the initial load
  // and we already have that tour's data cached, it shows instantly.
  // No useEffect that auto-triggers refresh on mount.
  const handleTourChange = useCallback(() => {
    // Already have this tour cached — nothing to do.
    // If not cached and user wants to see it they can hit Refresh.
  }, [])

  // ── Filter + sort the analyzed list ──────────────────────────────────────
  const filtered = useMemo(() => {
    let arr = analyzed.filter(item => {
      const pt = item.pp_prop?.prop_type
      if (!filterPropTypes.has(pt)) return false
      if (filterLean === 'over'  && item.lean !== 'OVER')  return false
      if (filterLean === 'under' && item.lean !== 'UNDER') return false
      if (filterConf === 'good'  && (item.confidence || 0) < 66) return false
      if (filterConf === 'high'  && (item.confidence || 0) < 80) return false
      return true
    })
    arr = [...arr].sort((a, b) => {
      if (sortBy === 'confidence') return (b.confidence || 0) - (a.confidence || 0)
      if (sortBy === 'edge')        return Math.abs(b.edge || 0) - Math.abs(a.edge || 0)
      if (sortBy === 'time') {
        const ta = a.pp_prop?.match_time || ''
        const tb = b.pp_prop?.match_time || ''
        return ta.localeCompare(tb)
      }
      return 0
    })
    return arr
  }, [analyzed, filterPropTypes, filterConf, filterLean, sortBy])

  // ── Best Bets: top 3 by combined edge size × confidence (matched + analyzed only) ──
  const bestBets = useMemo(() => {
    const scored = analyzed
      .filter(i => i.model_projection != null && i.edge != null)
      .map(i => ({
        ...i,
        _score: Math.abs(i.edge) * ((i.confidence || 0) / 100),
      }))
    scored.sort((a, b) => b._score - a._score)
    return scored.slice(0, 3)
  }, [analyzed])

  // ── Summary stats ────────────────────────────────────────────────────────
  const summary = useMemo(() => {
    const tot   = analyzed.length
    const over  = analyzed.filter(i => i.lean === 'OVER').length
    const under = analyzed.filter(i => i.lean === 'UNDER').length
    const high  = analyzed.filter(i => (i.confidence || 0) >= 80).length
    return { tot, over, under, high }
  }, [analyzed])

  const togglePropType = (pt) => {
    setFilterPropTypes(prev => {
      const next = new Set(prev)
      if (next.has(pt)) next.delete(pt); else next.add(pt)
      if (next.size === 0) next.add(pt) // never allow zero
      return next
    })
  }

  const ageMin = scrapedAt ? Math.floor(Date.now() / 1000 - scrapedAt) / 60 : 0
  const isStale = ageMin > 60

  return (
    <div>
      {/* Top bar: refresh + last updated */}
      <div style={{
        display: 'flex', justifyContent: 'space-between', alignItems: 'center',
        gap: 12, marginBottom: 18, flexWrap: 'wrap',
      }}>
        <div>
          <div style={{
            fontFamily: '"Barlow Condensed", sans-serif',
            fontWeight: 900, fontSize: 24, color: '#fff', letterSpacing: 1,
            display: 'inline-flex', alignItems: 'center', gap: 10,
          }}>
            Board Optimizer
            <TourBadge tour={tour} />
          </div>
          <div style={{ fontSize: 12, color: 'var(--muted)', fontFamily: '"Barlow Condensed", sans-serif', letterSpacing: 0.5 }}>
            Live PrizePicks props × Baseline projection
          </div>
        </div>
        <div style={{ display: 'flex', alignItems: 'center', gap: 14 }}>
          {scrapedAt && (
            <span style={{
              fontSize: 11, fontFamily: '"Barlow Condensed", sans-serif',
              fontWeight: 700, letterSpacing: 1,
              color: isStale ? 'var(--amber)' : 'var(--muted)',
            }}>
              {isStale && '⚠ '}Board updated {formatAgo(scrapedAt)}{isStale && ' — click refresh'}
            </span>
          )}
          <motion.button
            whileTap={{ scale: 0.94 }}
            onClick={() => refresh(true)}
            disabled={loading}
            style={{
              padding: '8px 18px', borderRadius: 999, cursor: loading ? 'wait' : 'pointer',
              background: 'var(--green-bright)', color: '#000',
              fontFamily: '"Barlow Condensed", sans-serif', fontWeight: 800,
              fontSize: 12, letterSpacing: 2, textTransform: 'uppercase',
              border: 'none',
              opacity: loading ? 0.6 : 1,
              boxShadow: '0 0 12px rgba(0, 230, 118, 0.3)',
            }}
          >{loading ? 'Refreshing…' : '↻ Refresh'}</motion.button>
        </div>
      </div>

      {/* Idle — not yet loaded */}
      {!loading && !error && stage === 'idle' && !hasLoadedOnce && (
        <div className="glass-card" style={{
          padding: '48px 24px', textAlign: 'center',
          display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 18,
        }}>
          <div style={{ fontSize: 36 }}>📋</div>
          <div style={{
            fontFamily: '"Barlow Condensed", sans-serif',
            fontWeight: 900, fontSize: 20, color: '#fff', letterSpacing: 1,
          }}>Load {tour} Props</div>
          <div style={{ fontSize: 13, color: 'var(--muted)', maxWidth: 380, lineHeight: 1.5 }}>
            Scrapes the live PrizePicks board and runs the Baseline model on the top 20 {tour} tennis props.
            Takes about 60-90 seconds on first load.
          </div>
          <motion.button
            whileTap={{ scale: 0.95 }}
            onClick={() => refresh(false)}
            style={{
              padding: '14px 36px', borderRadius: 999, cursor: 'pointer',
              background: 'var(--green-bright)', color: '#000',
              fontFamily: '"Barlow Condensed", sans-serif', fontWeight: 900,
              fontSize: 15, letterSpacing: 2.5, textTransform: 'uppercase',
              border: 'none',
              boxShadow: '0 0 20px rgba(0, 230, 118, 0.4)',
            }}
          >Load Board</motion.button>
        </div>
      )}

      {/* Loading state */}
      {loading && (
        <div style={{ marginBottom: 18 }}>
          <LoadingSpinner message={
            stage === 'scraping' ? 'Scraping PrizePicks board' :
            stage === 'analyzing' ? 'Running projections' :
            'Loading'
          } />
        </div>
      )}

      {/* Error state */}
      {error && !loading && (
        <div className="glass-card" style={{
          padding: '20px', borderColor: 'rgba(255, 68, 68, 0.4)',
          background: 'rgba(255, 68, 68, 0.05)', marginBottom: 18,
        }}>
          <div style={{
            fontFamily: '"Barlow Condensed", sans-serif',
            fontWeight: 800, fontSize: 14, color: 'var(--red-bright)',
            letterSpacing: 1, marginBottom: 6,
          }}>Unable to load board</div>
          <div style={{ fontSize: 13, color: 'rgba(255,255,255,0.75)', marginBottom: 12 }}>{error}</div>
          <button onClick={() => refresh(true)} style={{
            padding: '8px 16px', borderRadius: 10, border: '1px solid var(--card-border)',
            background: 'rgba(255, 255, 255, 0.04)', color: '#fff', cursor: 'pointer',
            fontFamily: '"Barlow Condensed", sans-serif', fontWeight: 700, letterSpacing: 1,
          }}>Try again</button>
        </div>
      )}

      {/* Summary bar */}
      {!loading && !error && analyzed.length > 0 && (
        <>
          <div style={{
            display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(120px, 1fr))',
            gap: 10, marginBottom: 12,
          }}>
            {[
              ['Analyzed',   summary.tot, '#fff'],
              ['OVER leans', summary.over, 'var(--green-bright)'],
              ['UNDER leans', summary.under, 'var(--red-bright)'],
              ['High Conf',  summary.high, 'var(--green-mid)'],
            ].map(([lbl, val, col]) => (
              <div key={lbl} className="glass-card" style={{ padding: '14px 16px', textAlign: 'center' }}>
                <div style={{ fontSize: 10, color: 'var(--muted)', fontFamily: '"Barlow Condensed", sans-serif', fontWeight: 700, letterSpacing: 2, textTransform: 'uppercase' }}>{lbl}</div>
                <div style={{ fontSize: 28, fontWeight: 900, color: col, fontFamily: '"Barlow Condensed", sans-serif', lineHeight: 1.1 }}>
                  <NumberFlow value={val} />
                </div>
              </div>
            ))}
          </div>

          {/* Truncation note — analyse cap is intentional */}
          {analyzeMeta && analyzeMeta.n_truncated > 0 && (
            <div style={{
              fontSize: 11, color: 'var(--amber)',
              fontFamily: '"Barlow Condensed", sans-serif',
              fontWeight: 700, letterSpacing: 0.5,
              padding: '8px 12px', borderRadius: 8,
              background: 'rgba(255, 179, 0, 0.06)',
              border: '1px solid rgba(255, 179, 0, 0.3)',
              marginBottom: 18,
            }}>
              Showing top {analyzeMeta.n_analyzed} of {analyzeMeta.n_candidates ?? analyzeMeta.n_total} eligible {tour} props
              ({analyzeMeta.n_truncated} more on board) — refresh after match start for updated lines.
            </div>
          )}
        </>
      )}

      {/* Best Bets section */}
      {!loading && !error && bestBets.length > 0 && (
        <>
          <div style={{
            display: 'flex', alignItems: 'center', gap: 12,
            background: 'linear-gradient(120deg, rgba(255, 214, 10, 0.12), rgba(255, 165, 0, 0.04))',
            border: '1px solid rgba(255, 214, 10, 0.3)',
            borderRadius: 12, padding: '12px 18px',
            marginBottom: 14,
          }}>
            <span style={{ fontSize: 22 }}>★</span>
            <div>
              <div style={{
                fontFamily: '"Barlow Condensed", sans-serif',
                fontWeight: 900, fontSize: 16, color: '#FFD60A',
                letterSpacing: 1.5, textTransform: 'uppercase',
              }}>Best Bets</div>
              <div style={{ fontSize: 11, color: 'rgba(255, 214, 10, 0.7)', fontFamily: '"Barlow Condensed", sans-serif', letterSpacing: 0.5 }}>
                Top {bestBets.length} props by edge × confidence
              </div>
            </div>
          </div>
          <div style={{
            display: 'grid',
            gridTemplateColumns: 'repeat(auto-fit, minmax(300px, 1fr))',
            gap: 14, marginBottom: 24,
          }}>
            {bestBets.map((item, i) => (
              <PropCard key={`bb-${item.pp_prop?.pp_projection_id || i}`} item={item} isBestBet />
            ))}
          </div>
        </>
      )}

      {/* Filters */}
      {!loading && !error && analyzed.length > 0 && (
        <>
          <SectionDivider label={`All Props (${filtered.length})`} />
          <div style={{
            display: 'flex', gap: 14, marginBottom: 16, flexWrap: 'wrap',
            alignItems: 'center',
          }}>
            <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
              {['Aces', 'Double Faults', 'Break Points Won', 'Total Games'].map(pt => {
                const active = filterPropTypes.has(pt)
                const s = PROP_BADGE[pt]
                return (
                  <button key={pt} onClick={() => togglePropType(pt)} style={{
                    padding: '6px 12px', borderRadius: 999, cursor: 'pointer',
                    background: active ? s.bg : 'rgba(255, 255, 255, 0.02)',
                    color: active ? s.fg : 'var(--muted)',
                    border: `1px solid ${active ? s.border : 'rgba(255,255,255,0.08)'}`,
                    fontFamily: '"Barlow Condensed", sans-serif',
                    fontWeight: 800, fontSize: 11, letterSpacing: 1.5,
                    textTransform: 'uppercase',
                  }}>{pt}</button>
                )
              })}
            </div>

            <div style={{ display: 'flex', gap: 6 }}>
              {[['all', 'All'], ['good', 'Good+'], ['high', 'High']].map(([k, lbl]) => (
                <button key={k} onClick={() => setFilterConf(k)} style={{
                  padding: '6px 12px', borderRadius: 999, cursor: 'pointer',
                  background: filterConf === k ? 'var(--green-bright)' : 'rgba(255, 255, 255, 0.02)',
                  color: filterConf === k ? '#000' : 'var(--muted)',
                  border: `1px solid ${filterConf === k ? 'var(--green-bright)' : 'rgba(255,255,255,0.08)'}`,
                  fontFamily: '"Barlow Condensed", sans-serif',
                  fontWeight: 800, fontSize: 11, letterSpacing: 1.5,
                  textTransform: 'uppercase',
                }}>{lbl} Conf</button>
              ))}
            </div>

            <div style={{ display: 'flex', gap: 6 }}>
              {[['all', 'Both'], ['over', 'Over'], ['under', 'Under']].map(([k, lbl]) => (
                <button key={k} onClick={() => setFilterLean(k)} style={{
                  padding: '6px 12px', borderRadius: 999, cursor: 'pointer',
                  background: filterLean === k ? 'rgba(0, 230, 118, 0.15)' : 'rgba(255, 255, 255, 0.02)',
                  color: filterLean === k ? 'var(--green-bright)' : 'var(--muted)',
                  border: `1px solid ${filterLean === k ? 'rgba(0, 230, 118, 0.4)' : 'rgba(255,255,255,0.08)'}`,
                  fontFamily: '"Barlow Condensed", sans-serif',
                  fontWeight: 800, fontSize: 11, letterSpacing: 1.5,
                  textTransform: 'uppercase',
                }}>{lbl}</button>
              ))}
            </div>

            <div style={{ display: 'flex', gap: 6, marginLeft: 'auto' }}>
              <select value={sortBy} onChange={e => setSortBy(e.target.value)} style={{
                padding: '7px 12px', borderRadius: 10,
                background: 'rgba(14, 24, 18, 0.55)',
                border: '1px solid var(--card-border)',
                color: '#fff',
                fontFamily: '"Barlow Condensed", sans-serif',
                fontSize: 12, fontWeight: 700, letterSpacing: 1,
                outline: 'none', cursor: 'pointer',
              }}>
                <option value="confidence">Sort: Confidence</option>
                <option value="edge">Sort: Edge size</option>
                <option value="time">Sort: Match time</option>
              </select>
            </div>
          </div>

          {/* Card grid */}
          <div style={{
            display: 'grid',
            gridTemplateColumns: 'repeat(auto-fit, minmax(320px, 1fr))',
            gap: 14,
          }}>
            {filtered.map((item, i) => (
              <PropCard key={item.pp_prop?.pp_projection_id || `${item.pp_prop?.player_name}-${item.pp_prop?.prop_type}-${i}`} item={item} />
            ))}
          </div>

          {filtered.length === 0 && (
            <div className="glass-card" style={{ padding: '40px 20px', textAlign: 'center', color: 'var(--muted)' }}>
              No props match the current filters.
            </div>
          )}
        </>
      )}

      {/* Empty state — tour-aware */}
      {!loading && !error && analyzed.length === 0 && board?.ok && (
        <div className="glass-card" style={{ padding: '40px 20px', textAlign: 'center', color: 'var(--muted)' }}>
          No {tour} tennis props on the board right now. Check back closer to match time.
        </div>
      )}
    </div>
  )
}
