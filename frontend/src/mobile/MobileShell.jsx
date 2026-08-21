import { useState, useEffect, useCallback } from 'react'
import { T, SAFE_TOP, SAFE_BOTTOM, useIsWide } from './theme'
import BottomNav from './BottomNav'
import { motion, AnimatePresence } from 'framer-motion'
import BoardTab from './BoardTab'
import ProjectionsTab from './ProjectionsTab'
import PicksTab from './PicksTab'
import PlayersTab from './PlayersTab'
import SearchTab from './SearchTab'
import ResearchTab from './ResearchTab'
import PlayerDashboard from './PlayerDashboard'
import InstallPrompt from '../components/InstallPrompt'
import { fetchPrizePicksBoard, fetchUnderdogBoard, fetchSlate, fetchRecord } from '../utils/api'
import { parsePrizePicksBoard, parseUnderdogBoard } from './data'

const EMPTY_BOARD = { date: null, isToday: false, rows: [] }

export default function MobileShell() {
  const [tab, setTab] = useState('board')
  const [book, setBook] = useState('prizepicks')
  const [openPlayer, setOpenPlayer] = useState(null)
  const [boards, setBoards] = useState({ prizepicks: EMPTY_BOARD, underdog: EMPTY_BOARD })
  const [record, setRecord] = useState(null)
  const [slate, setSlate] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)
  const [recordError, setRecordError] = useState(null)

  const load = useCallback(async () => {
    setLoading(true); setError(null); setRecordError(null)
    try {
      // The LIVE markets are the source of truth for the Boards tabs — the app is
      // independent of the bot here. The record is only for the Picks tab.
      // Slate enriches tour / surface / start time where names match.
      const [pp, ud, slate, rec] = await Promise.all([
        fetchPrizePicksBoard().catch(e => ({ __err: e })),
        fetchUnderdogBoard().catch(e => ({ __err: e })),
        fetchSlate().catch(() => null),
        fetchRecord().catch(e => ({ __err: e })),
      ])
      // One book failing must not blank the other.
      setBoards({
        prizepicks: pp?.__err ? EMPTY_BOARD : parsePrizePicksBoard(pp, slate),
        underdog: ud?.__err ? EMPTY_BOARD : parseUnderdogBoard(ud, slate),
      })
      setSlate(slate)
      if (pp?.__err && ud?.__err) setError(pp.__err?.message || 'load failed')
      if (rec?.__err) setRecordError(rec.__err?.message || 'record failed')
      else setRecord(rec)
    } catch (e) {
      setError(e?.message || 'load failed')
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => { load() }, [load])

  // Open at the TOP. Browsers restore the previous scroll offset on reload, and
  // in an installed PWA that persists across launches — so the app could open
  // part-way down its own header and look like content was missing above.
  useEffect(() => {
    if ('scrollRestoration' in window.history) window.history.scrollRestoration = 'manual'
    window.scrollTo(0, 0)
  }, [])

  // Switching tabs should also start at the top rather than inherit the last
  // tab's offset — a long Boards scroll otherwise carries into a short Picks list.
  useEffect(() => { window.scrollTo(0, 0) }, [tab])

  const wide = useIsWide()

  const onOpenPlayer = useCallback((p) => {
    setOpenPlayer(p)
    window.scrollTo(0, 0)
  }, [])

  return (
    <div style={{ minHeight: '100vh', background: T.bg, color: T.white, fontFamily: T.font }}>
      {/* Header */}
      <header style={{
        position: 'sticky', top: 0, zIndex: 50, background: 'rgba(10,10,10,0.95)',
        borderBottom: `1px solid ${T.border}`, paddingTop: SAFE_TOP,
        backdropFilter: 'blur(8px)', WebkitBackdropFilter: 'blur(8px)',
      }}>
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', padding: '12px 16px' }}>
          <div style={{ fontFamily: T.cond, fontWeight: 900, fontSize: 22, letterSpacing: 3, textTransform: 'uppercase' }}>
            BASE<span style={{ color: T.green }}>LINE</span>
          </div>
          <span style={{ fontFamily: T.cond, fontWeight: 700, fontSize: 10, letterSpacing: 2.5, color: T.muted2, textTransform: 'uppercase' }}>Research</span>
        </div>
      </header>

      {/* Tab content — cross-faded so switching tabs reads as a transition
          rather than an instant repaint. mode="wait" would double the delay,
          so the outgoing view is simply replaced. */}
      {/* On desktop the content sits beside the rail and is allowed to be
          genuinely wide — a 640px column centred in a 1920px window is still a
          phone layout, just with more empty space around it. `columns` lets the
          card lists flow into two tracks; break-inside keeps a card whole
          rather than splitting it across the gap. */}
      <main
        className={wide ? 'baseline-wide' : undefined}
        style={{
          padding: wide ? '22px 30px 40px' : `16px 14px calc(84px + ${SAFE_BOTTOM})`,
          maxWidth: wide ? 1180 : 640,
          margin: wide ? '0 0 0 210px' : '0 auto',
        }}>
        <AnimatePresence initial={false}>
          <motion.div
            key={tab}
            initial={{ opacity: 0, y: 8 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: 0.2, ease: [0.16, 1, 0.3, 1] }}
          >
            {tab === 'board' && <BoardTab boards={boards} book={book} setBook={setBook} loading={loading} error={error} reload={load} onOpenPlayer={onOpenPlayer} />}
            {tab === 'project' && <ProjectionsTab />}
            {tab === 'picks' && <PicksTab record={record} slate={slate} loading={loading} error={recordError} onOpenPlayer={onOpenPlayer} />}
            {tab === 'players' && <PlayersTab boards={boards} loading={loading} onOpenPlayer={onOpenPlayer} />}
            {tab === 'search' && <SearchTab onOpenPlayer={onOpenPlayer} />}
            {tab === 'research' && <ResearchTab onOpenPlayer={onOpenPlayer} />}
          </motion.div>
        </AnimatePresence>
      </main>

      <BottomNav active={tab} onChange={setTab} />
      <InstallPrompt />

      {openPlayer && (
        <PlayerDashboard
          key={openPlayer.name}
          player={openPlayer}
          boards={boards}
          onClose={() => setOpenPlayer(null)}
          onOpenPlayer={onOpenPlayer}
        />
      )}
    </div>
  )
}
