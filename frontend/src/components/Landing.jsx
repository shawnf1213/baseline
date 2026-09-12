import { useEffect, useState } from 'react'
import { fetchPublicRecord } from '../utils/api'

// ── THE PUBLIC FRONT DOOR ────────────────────────────────────────────────────
// baselineev.com had no landing page at all: every visitor, signed in or not,
// dropped straight into a 420px gate card. There was nowhere to explain what
// the model does, nothing to earn trust with, and no reason given to pay.
//
// THE PROOF IS FETCHED, NOT TYPED. Every figure on this page comes from
// /api/results/record — the same endpoint the in-app track record reads, the
// same rows the bot graded. A hard-coded hit rate on a marketing page starts
// drifting from reality the day it ships and is a claim nobody can check; this
// one is wrong only if the record is.
//
// AND IT DOES NOT CHERRY-PICK. It shows all-time AND the trailing month, side
// by side, even when the month is worse — which right now it is. A page that
// showed only the flattering window would be lying by selection, and the first
// subscriber to compare it against the in-app record would catch it.

const GREEN = '#00E676'
const BG = '#070707'
const FONT = '"Barlow", -apple-system, BlinkMacSystemFont, sans-serif'
const COND = '"Barlow Condensed", sans-serif'

function Stat({ value, label, sub, tone = '#fff', big = false }) {
  return (
    <div style={{ minWidth: 0 }}>
      <div style={{
        fontSize: big ? 'clamp(38px, 7vw, 58px)' : 'clamp(26px, 4vw, 34px)',
        fontWeight: 800, color: tone, lineHeight: 1, letterSpacing: -1.5,
        fontVariantNumeric: 'tabular-nums',
      }}>{value}</div>
      <div style={{ fontFamily: COND, fontWeight: 700, fontSize: 11.5,
                    letterSpacing: 1.4, textTransform: 'uppercase',
                    color: '#7a7a7a', marginTop: 7 }}>{label}</div>
      {sub ? (
        <div style={{ color: '#5a5a5a', fontSize: 12, marginTop: 3 }}>{sub}</div>
      ) : null}
    </div>
  )
}

function Section({ eyebrow, title, lead, children, id }) {
  return (
    <section id={id} style={{ padding: '72px 22px', position: 'relative' }}>
      <div style={{ maxWidth: 1080, margin: '0 auto' }}>
        {eyebrow ? (
          <div style={{ fontFamily: COND, fontWeight: 800, fontSize: 12,
                        letterSpacing: 2.2, textTransform: 'uppercase',
                        color: GREEN, marginBottom: 14 }}>{eyebrow}</div>
        ) : null}
        {title ? (
          <h2 style={{ fontFamily: COND, fontWeight: 800,
                       fontSize: 'clamp(30px, 5vw, 46px)', color: '#fff',
                       margin: 0, letterSpacing: 0.2, lineHeight: 1.08 }}>
            {title}
          </h2>
        ) : null}
        {lead ? (
          <p style={{ color: '#9a9a9a', fontSize: 16, lineHeight: 1.65,
                      maxWidth: 640, margin: '16px 0 0' }}>{lead}</p>
        ) : null}
        {children}
      </div>
    </section>
  )
}

function Glass({ children, style }) {
  return (
    <div style={{
      background: 'linear-gradient(160deg, rgba(255,255,255,0.045),'
                + ' rgba(255,255,255,0.012))',
      border: '1px solid rgba(255,255,255,0.08)', borderRadius: 18,
      padding: 22, backdropFilter: 'blur(8px)', ...style,
    }}>{children}</div>
  )
}

function CTA({ onClick, children, primary, disabled, wide }) {
  return (
    <button onClick={onClick} disabled={disabled} style={{
      minHeight: 54, padding: '0 26px', borderRadius: 14, cursor: disabled ? 'default' : 'pointer',
      border: primary ? 'none' : '1px solid rgba(255,255,255,0.16)',
      background: disabled ? '#161616'
        : primary ? `linear-gradient(135deg, ${GREEN}, #00B85C)` : 'rgba(255,255,255,0.04)',
      color: disabled ? '#555' : primary ? '#04240f' : '#fff',
      fontFamily: COND, fontWeight: 800, fontSize: 17, letterSpacing: 1.1,
      textTransform: 'uppercase', width: wide ? '100%' : undefined,
      boxShadow: primary && !disabled ? `0 10px 34px ${GREEN}38` : 'none',
      WebkitTapHighlightColor: 'transparent',
    }}>{children}</button>
  )
}

export default function Landing({ onConnectDiscord, onSubscribe, onPreview,
                                  busy, invite }) {
  const [rec, setRec] = useState(null)
  const [loaded, setLoaded] = useState(false)

  useEffect(() => {
    let alive = true
    fetchPublicRecord()
      .then(d => { if (alive) setRec(d && d.ready ? d : null) })
      .catch(() => { if (alive) setRec(null) })
      .finally(() => { if (alive) setLoaded(true) })
    return () => { alive = false }
  }, [])

  const pct = (v) => (v == null ? '—' : `${v.toFixed(1)}%`)

  return (
    <div style={{ background: BG, color: '#fff', fontFamily: FONT,
                  minHeight: '100dvh', overflowX: 'hidden' }}>

      {/* ── HERO ──────────────────────────────────────────────────────────── */}
      <div style={{ position: 'relative', overflow: 'hidden' }}>
        {/* Two soft colour fields rather than one flat black. This is the whole
            difference between "a page" and "a void with text on it". */}
        <div aria-hidden style={{
          position: 'absolute', inset: 0, pointerEvents: 'none',
          background:
            `radial-gradient(58% 46% at 22% 0%, ${GREEN}1F, transparent 70%),`
          + 'radial-gradient(46% 40% at 92% 18%, rgba(66,165,245,0.13), transparent 72%)',
        }} />
        <div aria-hidden style={{
          position: 'absolute', inset: 0, pointerEvents: 'none', opacity: 0.5,
          backgroundImage:
            'linear-gradient(rgba(255,255,255,0.028) 1px, transparent 1px),'
          + 'linear-gradient(90deg, rgba(255,255,255,0.028) 1px, transparent 1px)',
          backgroundSize: '56px 56px',
          maskImage: 'radial-gradient(70% 55% at 50% 8%, #000, transparent 78%)',
          WebkitMaskImage: 'radial-gradient(70% 55% at 50% 8%, #000, transparent 78%)',
        }} />

        <div style={{ position: 'relative', maxWidth: 1080,
                      margin: '0 auto', padding: '26px 22px 0' }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
            <img src="/baseline-logo.png" alt="" style={{ height: 30 }} />
            <span style={{ fontFamily: COND, fontWeight: 800, fontSize: 21,
                           letterSpacing: 1.6 }}>BASELINE</span>
          </div>
        </div>

        <div style={{ position: 'relative', maxWidth: 1080, margin: '0 auto',
                      padding: '58px 22px 68px' }}>
          <div style={{
            display: 'inline-flex', alignItems: 'center', gap: 8,
            padding: '6px 13px', borderRadius: 999, marginBottom: 24,
            background: `${GREEN}12`, border: `1px solid ${GREEN}3D`,
          }}>
            <span style={{ width: 6, height: 6, borderRadius: 3,
                           background: GREEN, boxShadow: `0 0 8px ${GREEN}` }} />
            <span style={{ fontSize: 12.5, color: GREEN, fontWeight: 700 }}>
              Tennis and NFL · live boards
            </span>
          </div>

          <h1 style={{
            fontFamily: COND, fontWeight: 800, margin: 0,
            fontSize: 'clamp(44px, 8.5vw, 92px)', lineHeight: 0.96,
            letterSpacing: -1, color: '#fff', maxWidth: 920,
          }}>
            EVERY PROP ON THE BOARD,<br />
            <span style={{
              background: `linear-gradient(100deg, ${GREEN}, #7BE3A8 55%, #42A5F5)`,
              WebkitBackgroundClip: 'text', backgroundClip: 'text',
              WebkitTextFillColor: 'transparent',
            }}>PRICED BY A MODEL.</span>
          </h1>

          <p style={{ color: '#a4a4a4', fontSize: 'clamp(15px, 2vw, 18px)',
                      lineHeight: 1.6, maxWidth: 610, margin: '24px 0 0' }}>
            Baseline scans the full PrizePicks and Underdog boards, projects a
            number for every line it can price, and posts the ones where the
            disagreement is big enough to be worth a bet. Every pick is graded
            automatically and published — the losers too.
          </p>

          <div style={{ display: 'flex', gap: 12, flexWrap: 'wrap',
                        marginTop: 34 }}>
            <CTA primary onClick={onSubscribe && (() => onSubscribe('weekly'))}
                 disabled={busy}>Start free trial</CTA>
            {/* The free look, ON REQUEST. Its clock is server-side and starts
                the moment this is pressed, so it is a button rather than
                something that happens to a visitor while they read. */}
            {onPreview ? (
              <CTA onClick={onPreview} disabled={busy}>See today's board</CTA>
            ) : null}
          </div>
          <p style={{ color: '#616161', fontSize: 12.5, marginTop: 14 }}>
            Already premium in the Baseline Discord?{' '}
            <button onClick={onConnectDiscord} disabled={busy} style={{
              background: 'none', border: 'none', padding: 0, cursor: 'pointer',
              color: GREEN, fontWeight: 700, fontSize: 12.5, fontFamily: FONT,
            }}>Connect and you're in</button> — no second payment.
          </p>
        </div>
      </div>

      {/* ── THE RECORD, LIVE ──────────────────────────────────────────────── */}
      <div style={{ borderTop: '1px solid rgba(255,255,255,0.07)',
                    borderBottom: '1px solid rgba(255,255,255,0.07)',
                    background: 'rgba(255,255,255,0.016)' }}>
        <div style={{ maxWidth: 1080, margin: '0 auto', padding: '40px 22px' }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 9,
                        marginBottom: 26 }}>
            <span style={{ width: 7, height: 7, borderRadius: 4,
                           background: GREEN, boxShadow: `0 0 9px ${GREEN}` }} />
            <span style={{ fontFamily: COND, fontWeight: 800, fontSize: 12,
                           letterSpacing: 2, textTransform: 'uppercase',
                           color: '#8a8a8a' }}>
              Live record · graded automatically
            </span>
          </div>

          {!loaded ? (
            <div style={{ color: '#5a5a5a', fontSize: 14 }}>Loading record…</div>
          ) : !rec ? (
            <div style={{ color: '#5a5a5a', fontSize: 14 }}>
              Record unavailable right now — it's in the app, live.
            </div>
          ) : (
            <>
              <div style={{ display: 'grid', gap: 28,
                            gridTemplateColumns:
                              'repeat(auto-fit, minmax(158px, 1fr))' }}>
                <Stat big tone={GREEN} value={pct(rec.live_props.win_rate)}
                      label="Hit rate, props on the board"
                      sub={`${rec.live_props.wins}-${rec.live_props.losses}`
                           + ` · ${rec.live_props.total} graded`} />
                <Stat value={pct(rec.recent.win_rate)}
                      label={`Last ${rec.recent_days} days`}
                      sub={`${rec.recent.wins}-${rec.recent.losses}`
                           + ` · ${rec.recent.total} graded`} />
                <Stat value={String(rec.all_time.total)} label="Picks graded"
                      sub={`across ${rec.days_active} days`} />
                <Stat value={rec.nfl.total
                        ? `${rec.nfl.wins}-${rec.nfl.losses}` : 'Week 1'}
                      label="NFL" sub={rec.nfl.total
                        ? `${rec.nfl.total} graded so far` : 'just launched'} />
              </div>

              {/* THE ASTERISK, STATED RATHER THAN BURIED. The headline counts
                  only the props still on the board; two were pulled for losing.
                  Saying so is the difference between a filtered number and a
                  dishonest one — and it is checkable against the in-app log. */}
              <p style={{ color: '#6f6f6f', fontSize: 12.5, lineHeight: 1.6,
                          marginTop: 26, maxWidth: 780 }}>
                The headline covers the props currently posted.{' '}
                {(rec.retired_props || []).join(' and ').toLowerCase()} were
                dropped after measuring badly, and counting them the all-time
                record is{' '}
                <b style={{ color: '#9a9a9a' }}>
                  {rec.all_time.wins}-{rec.all_time.losses}{' '}
                  ({pct(rec.all_time.win_rate)})
                </b>
                . Both numbers, every pick and every loss are in the app — we
                don't delete a bad week.
              </p>
            </>
          )}
        </div>
      </div>

      {/* ── HOW IT WORKS ──────────────────────────────────────────────────── */}
      <Section eyebrow="How it works"
               title="A number for every line, not a tip sheet"
               lead="Baseline doesn't pick winners off a feeling. It builds a projection for each prop from the things that actually drive it, compares that to what the book is offering, and only speaks when the gap is big enough to matter.">
        <div style={{ display: 'grid', gap: 14, marginTop: 34,
                      gridTemplateColumns: 'repeat(auto-fit, minmax(264px, 1fr))' }}>
          {[
            ['01', 'Scan the whole board',
             'Every ATP, WTA and NFL line on PrizePicks and Underdog — hundreds a day, not a shortlist someone liked the look of.'],
            ['02', 'Price it from the inputs',
             'Surface splits, hold and return rates, match-length modelling for tennis; usage, role, game script and the opposing defence for NFL.'],
            ['03', 'Post only the disagreements',
             'A projection that matches the line is not a bet. What gets posted is where the model and the book disagree by enough to survive the vig.'],
          ].map(([n, t, d]) => (
            <Glass key={n}>
              <div style={{ fontFamily: COND, fontWeight: 800, fontSize: 13,
                            letterSpacing: 2, color: GREEN }}>{n}</div>
              <div style={{ fontFamily: COND, fontWeight: 800, fontSize: 21,
                            marginTop: 10, letterSpacing: 0.3 }}>{t}</div>
              <p style={{ color: '#9a9a9a', fontSize: 14, lineHeight: 1.6,
                          margin: '9px 0 0' }}>{d}</p>
            </Glass>
          ))}
        </div>
      </Section>

      {/* ── WHAT YOU GET ──────────────────────────────────────────────────── */}
      <Section eyebrow="What's included" title="Two ways in, one subscription">
        <div style={{ display: 'grid', gap: 14, marginTop: 30,
                      gridTemplateColumns: 'repeat(auto-fit, minmax(300px, 1fr))' }}>
          <Glass>
            <div style={{ fontFamily: COND, fontWeight: 800, fontSize: 22,
                          letterSpacing: 0.3 }}>The Discord</div>
            <ul style={{ color: '#9a9a9a', fontSize: 14.5, lineHeight: 1.85,
                         margin: '12px 0 0', paddingLeft: 19 }}>
              <li>Daily board scans, tennis and NFL</li>
              <li>Pick of the day, posted on a schedule</li>
              <li>Live line-move alerts when a number shifts</li>
              <li>Slash commands to price any player yourself</li>
              <li>Results recap with the running record</li>
            </ul>
          </Glass>
          <Glass>
            <div style={{ fontFamily: COND, fontWeight: 800, fontSize: 22,
                          letterSpacing: 0.3 }}>The web app</div>
            <ul style={{ color: '#9a9a9a', fontSize: 14.5, lineHeight: 1.85,
                         margin: '12px 0 0', paddingLeft: 19 }}>
              <li>The full scanned board, not just what was posted</li>
              <li>Every game against the line, charted</li>
              <li>Hit rates, form and the matchup behind each number</li>
              <li>The complete pick log, wins and losses</li>
              <li>Installs to your phone — no app store</li>
            </ul>
          </Glass>
        </div>

        <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', marginTop: 24 }}>
          {['Break points', 'Fantasy score', 'Games won', 'Double faults',
            'Pass yards', 'Rush yards', 'Receiving yards', 'Receptions']
            .map(t => (
              <span key={t} style={{
                fontSize: 12.5, color: '#b4b4b4', padding: '7px 13px',
                borderRadius: 999, background: 'rgba(255,255,255,0.04)',
                border: '1px solid rgba(255,255,255,0.09)',
              }}>{t}</span>
            ))}
        </div>
      </Section>

      {/* ── PRICING ───────────────────────────────────────────────────────── */}
      <Section id="pricing" eyebrow="Pricing" title="Try it before you pay">
        <div style={{ display: 'grid', gap: 14, marginTop: 30, maxWidth: 720,
                      gridTemplateColumns: 'repeat(auto-fit, minmax(268px, 1fr))' }}>
          <Glass style={{ border: `1px solid ${GREEN}4D`,
                          boxShadow: `0 14px 50px ${GREEN}16` }}>
            <div style={{ fontFamily: COND, fontWeight: 800, fontSize: 13,
                          letterSpacing: 2, color: GREEN }}>WEEKLY</div>
            <div style={{ fontFamily: COND, fontWeight: 800, fontSize: 30,
                          marginTop: 8 }}>Free trial</div>
            <p style={{ color: '#9a9a9a', fontSize: 14, lineHeight: 1.6,
                        margin: '8px 0 18px' }}>
              Start free and see a full week of boards before anything is
              charged. Cancel any time.
            </p>
            <CTA wide primary disabled={busy}
                 onClick={onSubscribe && (() => onSubscribe('weekly'))}>
              Start free trial
            </CTA>
          </Glass>
          <Glass>
            <div style={{ fontFamily: COND, fontWeight: 800, fontSize: 13,
                          letterSpacing: 2, color: '#8a8a8a' }}>MONTHLY</div>
            <div style={{ fontFamily: COND, fontWeight: 800, fontSize: 30,
                          marginTop: 8 }}>Full season</div>
            <p style={{ color: '#9a9a9a', fontSize: 14, lineHeight: 1.6,
                        margin: '8px 0 18px' }}>
              The same access on a monthly cycle. Both plans unlock the Discord
              and the web app together.
            </p>
            <CTA wide disabled={busy}
                 onClick={onSubscribe && (() => onSubscribe('monthly'))}>
              Go monthly
            </CTA>
          </Glass>
        </div>
        <p style={{ color: '#616161', fontSize: 12.5, marginTop: 18 }}>
          Checkout is handled by Stripe — Baseline never sees your card. You're
          signed in automatically afterwards; no Discord account needed.
          {invite ? (
            <> Already premium there? <a href={invite} target="_blank"
              rel="noreferrer" style={{ color: GREEN, fontWeight: 700 }}>
              Join the server</a> and connect instead.</>
          ) : null}
        </p>
      </Section>

      {/* ── FOOTER ────────────────────────────────────────────────────────── */}
      <footer style={{ borderTop: '1px solid rgba(255,255,255,0.07)',
                       padding: '34px 22px 46px' }}>
        <div style={{ maxWidth: 1080, margin: '0 auto' }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 9 }}>
            <img src="/baseline-logo.png" alt="" style={{ height: 22 }} />
            <span style={{ fontFamily: COND, fontWeight: 800, fontSize: 16,
                           letterSpacing: 1.4, color: '#b4b4b4' }}>BASELINE</span>
          </div>
          {/* THE DISCLAIMER IS NOT DECORATION. These are model projections on a
              market with a real house edge, and a page that implies otherwise
              is the one thing here that could actually cost someone money. */}
          <p style={{ color: '#5a5a5a', fontSize: 12, lineHeight: 1.7,
                      maxWidth: 720, marginTop: 14 }}>
            Baseline publishes model projections and research, not betting
            advice, and nothing here is a guarantee of a result. A positive
            record over one stretch does not predict the next one. Bet only what
            you can afford to lose, and only where it is legal to do so. 21+.
          </p>
        </div>
      </footer>
    </div>
  )
}
