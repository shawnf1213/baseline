// One card per player on the board — the website's PlayerGroup
// (frontend/src/mobile/BoardTab.jsx) rebuilt for touch. Closed, it carries the
// player's best play and how many props he has; a tap drops the rest down,
// each opening its own sheet. A player with a single prop has nothing to drop
// down, so his card opens the sheet straight away.
import { LayoutAnimation, Pressable, StyleSheet, Text, View } from 'react-native'
import { LinearGradient } from 'expo-linear-gradient'
import { Icon } from './ui'
import { PlayerAvatar } from './Avatar'
import { ConfRing } from './Ring'
import { ResultBadge } from './PickCard'
import { F, T, sideTone } from '@/theme'
import type { BoardRow, PlayerGroup } from '@/lib/board'
import { PickRow, fmtLine, fmtSigned, kickoffLabel, propLabel, resultMeta, startTimeLabel } from '@/lib/picks'
import { tap } from '@/lib/haptics'

type Props = { g: PlayerGroup; open: boolean; onToggle: () => void; onOpen: (r: PickRow) => void }

const ringState = (r: BoardRow) =>
  r.state === 'pending' ? 'pending' as const : (r.state === 'nodata' || r.confidence == null) ? 'nodata' as const : undefined

export function PlayerGroupCard({ g, open, onToggle, onOpen }: Props) {
  const best = g.best
  const many = g.rows.length > 1
  const priced = best.state === 'done' || (best.state === 'started' && best.confidence != null)
  const side = sideTone(priced ? best.lean : '')
  const toneNow = best.state === 'done' ? side.tone : T.muted2
  const dim = g.started || best.state === 'nodata' || resultMeta(best.result).tone === 'void'
  const when = best.sport === 'tennis' ? startTimeLabel(best.startTs) : kickoffLabel(best.startsAt)
  const posted = g.rows.some(r => r.isPotd)

  const press = () => {
    tap()
    if (!many) { onOpen(best); return }
    LayoutAnimation.configureNext(LayoutAnimation.create(200, 'easeInEaseOut', 'opacity'))
    onToggle()
  }

  return (
    <View style={[s.card, open && { borderColor: `${side.tone}55` }, { opacity: dim ? 0.62 : 1 }]}>
      <LinearGradient colors={[`${toneNow}1E`, 'rgba(255,255,255,0.015)']} start={{ x: 0, y: 0.5 }} end={{ x: 0.75, y: 0.5 }}
                      style={StyleSheet.absoluteFill} />
      <Pressable onPress={press} accessibilityRole="button"
                 accessibilityState={many ? { expanded: open } : undefined}
                 accessibilityHint={many ? `Shows all ${g.rows.length} props` : 'Opens this prop'}
                 style={({ pressed }) => [s.head, pressed && { opacity: 0.8 }]}>
        <PlayerAvatar sport={best.sport} name={g.player} size={48} ring={best.state === 'done' ? side.tone : null} team={best.team} />
        <View style={{ flex: 1, minWidth: 0 }}>
          <View style={s.nameRow}>
            <Text style={s.name} numberOfLines={1}>{posted ? '⭐ ' : ''}{g.player}</Text>
            {many ? <View style={s.count}><Text style={s.countText}>{g.rows.length} props</Text></View> : null}
          </View>
          <View style={s.callRow}>
            <Text style={[s.side, { color: toneNow }]}>{priced ? (best.lean || '—') : 'LINE'}</Text>
            <Text style={s.line}>{fmtLine(best.line)}</Text>
            <Text style={s.prop} numberOfLines={1}>{propLabel(best.sport, best.propType)}</Text>
          </View>
          <View style={s.metaRow}>
            <Text style={s.meta} numberOfLines={1}>
              {best.team ? `${best.team} · ` : ''}vs {g.opponent}{g.started ? ' · started' : when ? ` · ${when}` : ''}
            </Text>
            {!many ? <ResultBadge r={best} /> : null}
          </View>
        </View>
        <ConfRing conf={best.confidence} size={46} state={ringState(best)} />
        {many ? (
          <View style={{ transform: [{ rotate: open ? '180deg' : '0deg' }] }}>
            <Icon name="chevron.down" size={13} color={open ? side.tone : T.muted2}
                  fallback={<Text style={{ color: T.muted2, fontSize: 12 }}>▾</Text>} />
          </View>
        ) : null}
      </Pressable>

      {open && many ? (
        <View style={s.list}>
          {g.rows.map(r => <PropLine key={r.key} r={r} onPress={() => { tap(); onOpen(r) }} />)}
        </View>
      ) : null}
    </View>
  )
}

// One prop inside an open card: which prop, the call, our number and edge,
// the confidence ring, and the result once graded.
function PropLine({ r, onPress }: { r: BoardRow; onPress: () => void }) {
  const priced = r.state === 'done' || (r.state === 'started' && r.confidence != null)
  const st = sideTone(priced ? r.lean : '')
  const tone = r.state === 'done' ? st.tone : T.muted2
  return (
    <Pressable onPress={onPress} accessibilityRole="button"
               style={({ pressed }) => [s.prow, pressed && { backgroundColor: 'rgba(255,255,255,0.04)' }]}>
      <View style={{ flex: 1, minWidth: 0 }}>
        <Text style={s.pLabel} numberOfLines={1}>{propLabel(r.sport, r.propType)}</Text>
        <View style={{ flexDirection: 'row', alignItems: 'center', gap: 8, marginTop: 3 }}>
          {priced && r.projection != null ? (
            <Text style={s.pMeta} numberOfLines={1}>
              Baseline <Text style={{ color: T.white, fontFamily: F.bodySemi }}>{r.projection.toFixed(1)}</Text>
              {r.edge != null ? <> · edge <Text style={{ color: tone, fontFamily: F.bodySemi }}>{fmtSigned(r.edge)}</Text></> : null}
            </Text>
          ) : (
            <Text style={s.pMeta}>{r.state === 'pending' ? 'Pricing…' : 'No projection for this line'}</Text>
          )}
          <ResultBadge r={r} />
        </View>
      </View>
      <View style={s.pCall}>
        <Text style={[s.pSide, { color: tone }]}>{priced ? (r.lean || '—') : 'LINE'}</Text>
        <Text style={s.pLine}>{fmtLine(r.line)}</Text>
      </View>
      <ConfRing conf={r.confidence} size={36} state={ringState(r)} />
      <Icon name="chevron.right" size={11} color={T.muted2} />
    </Pressable>
  )
}

const s = StyleSheet.create({
  card: { marginBottom: 10, borderRadius: T.r3, overflow: 'hidden', borderWidth: StyleSheet.hairlineWidth,
          borderColor: T.glassLine, backgroundColor: 'rgba(255,255,255,0.02)' },
  head: { flexDirection: 'row', alignItems: 'center', gap: 12, paddingVertical: 12, paddingHorizontal: 12 },
  nameRow: { flexDirection: 'row', alignItems: 'center', gap: 7 },
  name: { fontFamily: F.condBlack, fontSize: 18, color: T.white, letterSpacing: 0.3, flexShrink: 1 },
  count: { paddingHorizontal: 7, paddingVertical: 2, borderRadius: 999, borderWidth: 1, borderColor: T.glassLineHi,
           backgroundColor: 'rgba(255,255,255,0.05)', flexShrink: 0 },
  countText: { fontFamily: F.condBold, fontSize: 10, letterSpacing: 0.9, color: T.muted, textTransform: 'uppercase' },
  callRow: { flexDirection: 'row', alignItems: 'baseline', gap: 6, marginTop: 2 },
  side: { fontFamily: F.condHeavy, fontSize: 14, letterSpacing: 0.8 },
  line: { fontFamily: F.condHeavy, fontSize: 16, color: T.white },
  prop: { fontFamily: F.condBold, fontSize: 13.5, color: T.muted, flexShrink: 1 },
  metaRow: { flexDirection: 'row', alignItems: 'center', gap: 8, marginTop: 4 },
  meta: { fontFamily: F.body, fontSize: 12.5, color: T.muted, flexShrink: 1 },
  list: { borderTopWidth: StyleSheet.hairlineWidth, borderTopColor: T.glassLine, backgroundColor: 'rgba(0,0,0,0.18)' },
  prow: { flexDirection: 'row', alignItems: 'center', gap: 10, paddingVertical: 11, paddingLeft: 16, paddingRight: 12,
          borderBottomWidth: StyleSheet.hairlineWidth, borderBottomColor: T.glassLine },
  pLabel: { fontFamily: F.condBold, fontSize: 12.5, letterSpacing: 1, color: T.muted, textTransform: 'uppercase' },
  pMeta: { fontFamily: F.body, fontSize: 12, color: T.muted2, flexShrink: 1 },
  pCall: { flexDirection: 'row', alignItems: 'baseline', gap: 5 },
  pSide: { fontFamily: F.condHeavy, fontSize: 13.5, letterSpacing: 0.8 },
  pLine: { fontFamily: F.condHeavy, fontSize: 17, color: T.white },
})
