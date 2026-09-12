import { useMemo } from 'react'
import { T, SAFE_TOP } from './theme'
import { Card, SectionLabel, Empty } from './bits'

// ── NFL PLAYER SHEET ─────────────────────────────────────────────────────────
// What opens when an NFL card is tapped, and the NFL counterpart of
// PlayerDashboard — but deliberately NOT that component. PlayerDashboard is
// tennis all the way down: fetchForm, fetchStats, fetchNextMatch and
// fetchHistory are tennis endpoints, and its content is surface splits and
// serve/return numbers. Pointing NFL at it would mean four failing requests and
// a screen of empty tennis sections.
//
// WHAT THIS CAN SHOW, and why it is drawn from props rather than fetched: the
// NFL model runs in the BOT, not the web backend, so there is no
// /api/nfl/player endpoint to call. Everything here comes from the board the
// bot already published — which is enough for the question a reader actually
// has when they tap a card: what else is priced on this player, how big is the
// disagreement, and has he been posted before.

const PROP_LABEL = {
  pass_yards: 'Pass Yards',
  rush_yards: 'Rush Yards',
  receiving_yards: 'Rec Yards',
  receptions: 'Receptions',
}

const fmt = (v, nd = 1) => (typeof v === 'number' ? v.toFixed(nd) : '—')

function PropLine({ r, posted }) {
  const lean = (r.lean || '').toUpperCase()
  const over = lean === 'OVER'
  const edge = typeof r.edge === 'number' ? r.edge : null
  const hit = posted?.result
  return (
    <Card style={{ padding: 13, marginBottom: 8 }}>
      <div style={{ display: 'flex', alignItems: 'baseline', gap: 8 }}>
        <span style={{ fontFamily: T.cond, fontWeight: 800, fontSize: 13,
                       letterSpacing: 0.8, textTransform: 'uppercase',
                       color: T.muted, flex: 1 }}>
          {PROP_LABEL[r.prop_type] || r.prop_type}
        </span>
        {posted?.is_potd ? <span style={{ fontSize: 12 }}>⭐</span> : null}
        {hit === 'W' || hit === 'L' ? (
          <span style={{ fontSize: 11, fontWeight: 800,
                         color: hit === 'W' ? '#3FB950' : '#E5534B' }}>
            {hit}{typeof posted.result_value === 'number'
              ? ` · ${posted.result_value}` : ''}
          </span>
        ) : null}
      </div>
      <div style={{ display: 'flex', alignItems: 'baseline', gap: 8, marginTop: 6,
                    flexWrap: 'wrap' }}>
        <span style={{ fontSize: 13 }}>{over ? '🟢' : '🔴'}</span>
        <span style={{ fontFamily: T.cond, fontWeight: 800, fontSize: 19,
                       color: over ? T.green : '#E5534B' }}>{lean || '—'}</span>
        <span style={{ fontSize: 17, fontWeight: 800, color: T.white,
                       fontVariantNumeric: 'tabular-nums' }}>{fmt(r.line)}</span>
        <span style={{ color: T.muted, fontSize: 12.5 }}>
          · proj <b style={{ color: T.white }}>{fmt(r.model_projection)}</b>
          {edge !== null ? ` · edge ${edge > 0 ? '+' : ''}${fmt(edge)}` : ''}
        </span>
      </div>
    </Card>
  )
}

export default function NflPlayerSheet({ player, rows, posted, onClose }) {
  // Everything priced on this player for the slate that was tapped — the
  // question "what else is on him" is the first one a reader has, and the
  // board itself cannot answer it because one-prop-per-player hides the rest.
  const mine = useMemo(
    () => (rows || []).filter(r => r.player === player?.player
                                && r.slate_date === player?.slate_date)
      .sort((a, b) => (b.confidence || 0) - (a.confidence || 0)),
    [rows, player])

  const postedFor = useMemo(() => {
    const m = new Map()
    for (const p of (posted || [])) {
      if (p.player === player?.player) m.set(`${p.slate_date}|${p.prop_type}`, p)
    }
    return m
  }, [posted, player])

  // Every time this player has been POSTED, not just today — the record is the
  // only thing here that is evidence rather than estimate.
  const history = useMemo(
    () => (posted || []).filter(p => p.player === player?.player
                                  && ['W', 'L', 'PUSH'].includes(p.result))
      .sort((a, b) => String(b.slate_date).localeCompare(String(a.slate_date))),
    [posted, player])

  const rec = useMemo(() => {
    const w = history.filter(p => p.result === 'W').length
    const l = history.filter(p => p.result === 'L').length
    return { w, l }
  }, [history])

  if (!player) return null
  const head = mine[0] || {}

  return (
    <div style={{
      position: 'fixed', inset: 0, zIndex: 100, background: T.bg,
      overflowY: 'auto', WebkitOverflowScrolling: 'touch',
      paddingTop: SAFE_TOP, paddingBottom: 40,
    }}>
      <div style={{ maxWidth: 760, margin: '0 auto', padding: '12px 14px' }}>
        <button onClick={onClose} style={{
          minHeight: 44, padding: '0 16px', marginBottom: 14,
          background: T.card, color: T.white, border: `1px solid ${T.border}`,
          borderRadius: 12, fontFamily: T.cond, fontWeight: 700, fontSize: 14,
          letterSpacing: 0.8, textTransform: 'uppercase', cursor: 'pointer',
        }}>← Back</button>

        <div style={{ marginBottom: 4 }}>
          <div style={{ fontFamily: T.cond, fontWeight: 800, fontSize: 30,
                        color: T.white, letterSpacing: 0.4, lineHeight: 1.05 }}>
            {player.player}
          </div>
          <div style={{ color: T.muted, fontSize: 13, marginTop: 5 }}>
            {head.team || player.team}
            {head.opponent ? ` vs ${head.opponent}` : ''}
            {head.matchup ? ` · ${head.matchup}` : ''}
          </div>
          {rec.w + rec.l > 0 && (
            <div style={{ color: T.muted2, fontSize: 12, marginTop: 4 }}>
              Posted record: <b style={{ color: T.white }}>{rec.w}-{rec.l}</b>
            </div>
          )}
        </div>

        <SectionLabel right={`${mine.length} priced`}>Props on this game</SectionLabel>
        {mine.length
          ? mine.map(r => (
              <PropLine key={`${r.prop_type}-${r.id}`} r={r}
                        posted={postedFor.get(`${r.slate_date}|${r.prop_type}`)} />
            ))
          : <Empty icon="🏈" title="No priced props"
                   hint="Nothing on this player for that slate." />}

        {history.length > 0 && (
          <>
            <SectionLabel right={`${rec.w}-${rec.l}`}>Previously posted</SectionLabel>
            {history.map(p => (
              <Card key={p.id} style={{ padding: 11, marginBottom: 6 }}>
                <div style={{ display: 'flex', alignItems: 'baseline', gap: 8 }}>
                  <span style={{ color: T.muted2, fontSize: 11.5, minWidth: 72 }}>
                    {p.slate_date}
                  </span>
                  <span style={{ fontSize: 12.5, color: T.white, flex: 1 }}>
                    {(p.lean || '').toUpperCase()} {fmt(p.line)}{' '}
                    {PROP_LABEL[p.prop_type] || p.prop_type}
                  </span>
                  <span style={{ fontSize: 11.5, fontWeight: 800,
                                 color: p.result === 'W' ? '#3FB950'
                                   : p.result === 'L' ? '#E5534B' : T.muted2 }}>
                    {p.result}
                    {typeof p.result_value === 'number' ? ` · ${p.result_value}` : ''}
                  </span>
                </div>
              </Card>
            ))}
          </>
        )}

        <div style={{ color: T.muted2, fontSize: 11, textAlign: 'center',
                      padding: '18px 8px 4px', lineHeight: 1.5 }}>
          Edge = projection − line. Model projections, not betting advice.
        </div>
      </div>
    </div>
  )
}
