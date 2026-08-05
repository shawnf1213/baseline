import { T, SAFE_BOTTOM } from './theme'
import { Chip } from './bits'
import { PROP_TYPES, SURFACES, TOURS } from './data'

const SORTS = [
  { key: 'edge', label: 'Model Edge' },
  { key: 'confidence', label: 'Confidence' },
  { key: 'start', label: 'Start Time' },
]

export default function FilterSheet({ open, onClose, filters, setFilters }) {
  if (!open) return null
  const set = (patch) => setFilters({ ...filters, ...patch })
  const reset = () => setFilters({ prop: 'All', tour: 'All', surface: 'All', sort: 'edge' })

  return (
    <div style={{ position: 'fixed', inset: 0, zIndex: 1400, display: 'flex', alignItems: 'flex-end' }}>
      <div onClick={onClose} style={{ position: 'absolute', inset: 0, background: 'rgba(0,0,0,0.6)', animation: 'fade-in 160ms ease' }} />
      <div style={{
        position: 'relative', width: '100%', maxHeight: '82vh', overflowY: 'auto',
        background: T.card, borderTop: `1px solid ${T.border}`,
        borderTopLeftRadius: 20, borderTopRightRadius: 20,
        padding: `14px 18px calc(20px + ${SAFE_BOTTOM})`, animation: 'sheet-up 220ms cubic-bezier(.2,.8,.2,1)',
      }} className="no-scrollbar">
        <div style={{ width: 36, height: 4, background: T.border, borderRadius: 2, margin: '0 auto 16px' }} />
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 18 }}>
          <span style={{ fontFamily: T.cond, fontWeight: 800, fontSize: 20, color: T.white, letterSpacing: 0.5 }}>Filter &amp; Sort</span>
          <button onClick={reset} style={{ background: 'transparent', border: 'none', color: T.muted, fontFamily: T.cond, fontWeight: 700, fontSize: 13, letterSpacing: 1, textTransform: 'uppercase', cursor: 'pointer' }}>Reset</button>
        </div>

        <Group label="Sort by">
          {SORTS.map(s => <Chip key={s.key} active={filters.sort === s.key} onClick={() => set({ sort: s.key })}>{s.label}</Chip>)}
        </Group>

        <Group label="Prop Type">
          <Chip active={filters.prop === 'All'} onClick={() => set({ prop: 'All' })}>All</Chip>
          {PROP_TYPES.map(p => <Chip key={p.key} active={filters.prop === p.key} onClick={() => set({ prop: p.key })}>{p.short}</Chip>)}
        </Group>

        <Group label="Tour">
          <Chip active={filters.tour === 'All'} onClick={() => set({ tour: 'All' })}>All</Chip>
          {TOURS.map(t => <Chip key={t} active={filters.tour === t} onClick={() => set({ tour: t })}>{t}</Chip>)}
        </Group>

        <Group label="Surface">
          <Chip active={filters.surface === 'All'} onClick={() => set({ surface: 'All' })}>All</Chip>
          {SURFACES.map(s => <Chip key={s} active={filters.surface === s} onClick={() => set({ surface: s })}>{s}</Chip>)}
        </Group>

        <button onClick={onClose} style={{
          marginTop: 22, width: '100%', minHeight: 52, background: T.green, color: '#000',
          border: 'none', borderRadius: 13, fontFamily: T.cond, fontWeight: 800, fontSize: 16,
          letterSpacing: 1, textTransform: 'uppercase', cursor: 'pointer',
        }}>Show Results</button>
      </div>
    </div>
  )
}

function Group({ label, children }) {
  return (
    <div style={{ marginBottom: 20 }}>
      <div style={{ fontFamily: T.cond, fontWeight: 800, fontSize: 12, letterSpacing: 2, textTransform: 'uppercase', color: T.muted, marginBottom: 10 }}>{label}</div>
      <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8 }}>{children}</div>
    </div>
  )
}
