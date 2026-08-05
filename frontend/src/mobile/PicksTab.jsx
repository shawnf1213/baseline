import { useMemo, useState } from 'react'
import { T } from './theme'
import { Card, Spinner, Empty, Segment, ResultBadge, SectionLabel } from './bits'
import { shortProp, fmt, prettyDate, etToday, derivePicks, rollingRecord, resultMeta } from './data'

const BOOKS = [
  { key: 'prizepicks', label: 'PrizePicks' },
  { key: 'underdog', label: 'Underdog' },
]

// Baseline's OWN picks, per book. This is the one surface that mirrors the bot —
// the Boards tabs are independent research views of the live market.
//
// The two books are never merged. Underdog has its own lines and its own market,
// so it earns its own record, exactly as the backend scores it.
export default function PicksTab({ record, slate, loading, error, onOpenPlayer }) {
  const [book, setBook] = useState('prizepicks')

  const { days } = useMemo(() => derivePicks(record, book, slate), [record, book, slate])
  const rolling = useMemo(() => rollingRecord(days, 30), [days])
  const today = etToday()

  return (
    <div style={{ paddingBottom: 8 }}>
      <div style={{ marginBottom: 12 }}>
        <div style={{ fontFamily: T.cond, fontWeight: 800, fontSize: 26, color: T.white, letterSpacing: 0.5, lineHeight: 1 }}>
          Baseline Picks
        </div>
        <div style={{ color: T.muted, fontSize: 12.5, marginTop: 4 }}>
          The board Baseline actually released, with how each play landed.
        </div>
      </div>

      <Segment options={BOOKS} value={book} onChange={setBook} style={{ marginBottom: 14 }} />

      {loading && <div style={{ display: 'flex', justifyContent: 'center', padding: 48 }}><Spinner size={28} /></div>}

      {!loading && error && (
        <Empty icon="⚠️" title="Couldn't load the record" hint="The pick log didn't load. Pull to retry." />
      )}

      {!loading && !error && !days.length && (
        <Empty icon="📋"
          title={`No ${book === 'underdog' ? 'Underdog' : 'PrizePicks'} picks logged`}
          hint="Boards are released in the evening. Check back after the next scan." />
      )}

      {!loading && !error && !!days.length && (
        <>
          {rolling.decided > 0 && (
            <Card style={{ padding: '12px 14px', marginBottom: 14, display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
              <div>
                <div style={{ fontFamily: T.cond, fontWeight: 800, fontSize: 11, letterSpacing: 1.6, textTransform: 'uppercase', color: T.muted2 }}>
                  Last 30 days
                </div>
                <div style={{ color: T.muted, fontSize: 12, marginTop: 3 }}>
                  {rolling.wins}W–{rolling.losses}L · pushes count as wins
                </div>
              </div>
              <div style={{ fontFamily: T.cond, fontWeight: 900, fontSize: 30, color: T.green, letterSpacing: 0.5 }}>
                {rolling.winRate != null ? `${rolling.winRate}%` : '—'}
              </div>
            </Card>
          )}

          {days.map(d => (
            <div key={d.date} style={{ marginBottom: 20 }}>
              <SectionLabel right={<DayTally day={d} />}>
                {prettyDate(d.date)}{d.date === today ? ' · Today' : ''}
              </SectionLabel>
              {d.rows.map(r => (
                <PickRow key={r.key} r={r} onOpen={() => onOpenPlayer({ name: r.player, tour: r.tour })} />
              ))}
            </div>
          ))}
        </>
      )}
    </div>
  )
}

function DayTally({ day }) {
  const txt = day.pending > 0
    ? `${day.pending} pending`
    : day.winRate != null ? `${day.wins}–${day.losses} · ${day.winRate}%` : '—'
  return (
    <span style={{
      fontFamily: T.cond, fontWeight: 700, fontSize: 11.5, letterSpacing: 0.8,
      textTransform: 'uppercase', color: day.pending > 0 ? T.amber : T.muted,
    }}>{txt}</span>
  )
}

function PickRow({ r, onOpen }) {
  const meta = resultMeta(r.result)
  const dim = meta.tone === 'void'
  return (
    <Card onClick={onOpen} style={{ padding: '12px 12px 12px 14px', marginBottom: 10, opacity: dim ? 0.55 : 1 }}>
      <div style={{ display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between', gap: 8 }}>
        <div style={{ minWidth: 0, flex: 1 }}>
          <span style={{ fontFamily: T.cond, fontWeight: 800, fontSize: 18, color: T.white, letterSpacing: 0.3 }}>
            {r.player}
          </span>
          {r.isThreeX && (
            <span style={{
              marginLeft: 6, fontFamily: T.cond, fontWeight: 800, fontSize: 10, letterSpacing: 1,
              color: T.amber, border: `1px solid ${T.amber}44`, borderRadius: 5, padding: '1px 5px',
            }}>3X</span>
          )}
          <div style={{ color: T.muted, fontSize: 12.5, marginTop: 2 }}>
            vs {r.opponent}{r.surface ? ` · ${r.surface}` : ''}
            {r.tour && !r.tourInferred ? ` · ${r.tour}` : ''}
          </div>
        </div>
        <ResultBadge tone={meta.tone} label={meta.label}
          value={meta.tone === 'pending' || meta.tone === 'void' ? null : r.resultValue} />
      </div>

      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 10, marginTop: 10 }}>
        <span style={{
          fontFamily: T.cond, fontWeight: 700, fontSize: 12.5, letterSpacing: 0.6, textTransform: 'uppercase',
          color: r.lean === 'UNDER' ? T.red : T.green,
          background: r.lean === 'UNDER' ? 'rgba(255,82,82,0.08)' : 'rgba(0,230,118,0.08)',
          border: `1px solid ${T.border}`, padding: '4px 10px', borderRadius: 8,
        }}>
          {r.lean} {fmt(r.line, Number.isInteger(r.line) ? 0 : 1)} {shortProp(r.propType)}
        </span>
        <div style={{ display: 'flex', alignItems: 'baseline', gap: 12 }}>
          <Metric label="Proj" value={fmt(r.projection)} accent />
          {r.confidence != null && <Metric label="Conf" value={`${Math.round(r.confidence)}%`} muted />}
        </div>
      </div>
    </Card>
  )
}

function Metric({ label, value, accent, muted }) {
  return (
    <div style={{ textAlign: 'center' }}>
      <div style={{ fontFamily: T.cond, fontWeight: 800, fontSize: 16, color: accent ? T.green : muted ? T.muted : T.white }}>{value}</div>
      <div style={{ fontFamily: T.cond, fontWeight: 700, fontSize: 9.5, letterSpacing: 1, textTransform: 'uppercase', color: T.muted2 }}>{label}</div>
    </div>
  )
}
