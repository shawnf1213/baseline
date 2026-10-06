// Where faces and crests come from.
//
// PLAYERS go through the backend's /api/player/image (public, cached): tennis
// is a verified Wikipedia photo, NFL the ESPN headshot keyed by the ESPN id the
// bot publishes, NBA the NBA.com headshot keyed by the game-log player id. It
// answers 404 when it has nothing — the avatar then shows initials, never a
// stranger's face.
//
// TEAMS come straight from ESPN's logo CDN by abbreviation, through its
// resizer so a 40-point crest is a few KB, not a 500-pixel PNG.
import { API_BASE } from './api'
import type { SportKey } from './sports'

export const playerImageUrl = (sport: SportKey, name: string | null | undefined) =>
  name ? `${API_BASE}/api/player/image?sport=${sport}&name=${encodeURIComponent(name)}` : null

// nflverse / stats.nba.com abbreviations -> ESPN's, where they differ.
const NFL_ESPN: Record<string, string> = { LA: 'lar', LAR: 'lar', WAS: 'wsh', WSH: 'wsh', JAC: 'jax' }
const NBA_ESPN: Record<string, string> = { GSW: 'gs', NYK: 'ny', SAS: 'sa', NOP: 'no', UTA: 'utah', WAS: 'wsh' }

export function teamLogoUrl(sport: SportKey, abbr: string | null | undefined, px = 96) {
  if (!abbr) return null
  const a = String(abbr).toUpperCase().trim()
  if (!a) return null
  if (sport === 'nfl') return espn('nfl', NFL_ESPN[a] || a.toLowerCase(), px)
  if (sport === 'nba') return espn('nba', NBA_ESPN[a] || a.toLowerCase(), px)
  return null
}

const espn = (league: string, code: string, px: number) =>
  `https://a.espncdn.com/combiner/i?img=/i/teamlogos/${league}/500/${code}.png&w=${px}&h=${px}`

export const initials = (name: string | null | undefined) => {
  const t = (name || '').trim().split(/\s+/)
  return ((t[0]?.[0] || '') + (t.length > 1 ? t[t.length - 1][0] : '')).toUpperCase() || '?'
}
