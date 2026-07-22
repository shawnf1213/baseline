import { useState, useEffect, useCallback } from 'react'
import { T, SAFE_TOP, SAFE_BOTTOM } from './theme'
import BottomNav from './BottomNav'
import BoardTab from './BoardTab'
import PlayersTab from './PlayersTab'
import SearchTab from './SearchTab'
import ResearchTab from './ResearchTab'
import PlayerDashboard from './PlayerDashboard'
import InstallPrompt from '../components/InstallPrompt'
import { fetchRecord, fetchSlate } from '../utils/api'
import { deriveBoard } from './data'

export default function MobileShell() {
  const [tab, setTab] = useState('board')
  const [openPlayer, setOpenPlayer] = useState(null)
  const [board, setBoard] = useState({ date: null, isToday: false, rows: [] })
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)

  const load = useCallback(async () => {
    setLoading(true); setError(null)
    try {
      // Record is the source of truth for props; slate only enriches start times.
      const [record, slate] = await Promise.all([
        fetchRecord(),
        fetchSlate().catch(() => null),
      ])
      setBoard(deriveBoard(record, slate))
    } catch (e) {
      setError(e?.message || 'load failed')
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => { load() }, [load])

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

      {/* Tab content */}
      <main style={{ padding: `16px 14px calc(84px + ${SAFE_BOTTOM})`, maxWidth: 640, margin: '0 auto' }}>
        {tab === 'board' && <BoardTab board={board} loading={loading} error={error} reload={load} onOpenPlayer={onOpenPlayer} />}
        {tab === 'players' && <PlayersTab board={board} loading={loading} onOpenPlayer={onOpenPlayer} />}
        {tab === 'search' && <SearchTab onOpenPlayer={onOpenPlayer} />}
        {tab === 'research' && <ResearchTab board={board} onOpenPlayer={onOpenPlayer} />}
      </main>

      <BottomNav active={tab} onChange={setTab} />
      <InstallPrompt />

      {openPlayer && (
        <PlayerDashboard
          key={openPlayer.name}
          player={openPlayer}
          board={board}
          onClose={() => setOpenPlayer(null)}
          onOpenPlayer={onOpenPlayer}
        />
      )}
    </div>
  )
}
