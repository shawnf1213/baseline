import { T, SAFE_BOTTOM } from './theme'

const TABS = [
  { key: 'board',    label: 'Board',    icon: 'board' },
  { key: 'players',  label: 'Players',  icon: 'players' },
  { key: 'search',   label: 'Search',   icon: 'search' },
  { key: 'research', label: 'My Research', icon: 'bookmark' },
]

function Icon({ name, active }) {
  const c = active ? T.green : T.muted2
  const p = { width: 23, height: 23, viewBox: '0 0 24 24', fill: 'none', stroke: c, strokeWidth: 2, strokeLinecap: 'round', strokeLinejoin: 'round' }
  switch (name) {
    case 'board': return (<svg {...p}><path d="M4 5h16M4 12h16M4 19h10" /></svg>)
    case 'players': return (<svg {...p}><circle cx="9" cy="8" r="3.2" /><path d="M3.5 19a5.5 5.5 0 0 1 11 0" /><path d="M16 7.5a3 3 0 0 1 0 5.8M20.5 19a5 5 0 0 0-3.5-4.8" /></svg>)
    case 'search': return (<svg {...p}><circle cx="11" cy="11" r="7" /><path d="M21 21l-4.3-4.3" /></svg>)
    case 'bookmark': return (<svg {...p} fill={active ? T.green : 'none'}><path d="M6 3h12a1 1 0 0 1 1 1v17l-7-4-7 4V4a1 1 0 0 1 1-1z" /></svg>)
    default: return null
  }
}

export default function BottomNav({ active, onChange }) {
  return (
    <nav style={{
      position: 'fixed', left: 0, right: 0, bottom: 0, zIndex: 1000,
      background: 'rgba(10,10,10,0.96)', borderTop: `1px solid ${T.border}`,
      paddingBottom: SAFE_BOTTOM,
      display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)',
      backdropFilter: 'blur(8px)', WebkitBackdropFilter: 'blur(8px)',
    }}>
      {TABS.map(t => {
        const on = active === t.key
        return (
          <button key={t.key} onClick={() => onChange(t.key)} style={{
            display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', gap: 4,
            minHeight: 58, padding: '8px 4px 10px', background: 'transparent', border: 'none',
            cursor: 'pointer', WebkitTapHighlightColor: 'transparent',
          }}>
            <Icon name={t.icon} active={on} />
            <span style={{
              fontFamily: T.cond, fontWeight: 700, fontSize: 10.5, letterSpacing: 0.8,
              textTransform: 'uppercase', color: on ? T.green : T.muted2,
            }}>{t.label}</span>
          </button>
        )
      })}
    </nav>
  )
}
