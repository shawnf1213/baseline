// Where faces come from.
//
// PLAYERS go through the backend's /api/player/image (public, cached): a
// free-use photo from Wikipedia, verified to be this player, for every sport.
// It answers 404 when it has nothing — the avatar then shows initials, never
// a stranger's face. The screen carries no source label (operator,
// 2026-10-07); /api/player/image/credit still answers where a photo came from.
//
// NO TEAM CRESTS (operator, 2026-10-07: "remove and replace with free use
// images"). A club's logo is a trademark with no free-use version — the ESPN
// CDN this used to pull them from licensed nothing to us — so NFL and NBA
// teams show their abbreviation instead.
import { API_BASE } from './api'
import type { SportKey } from './sports'

export const playerImageUrl = (sport: SportKey, name: string | null | undefined) =>
  name ? `${API_BASE}/api/player/image?sport=${sport}&name=${encodeURIComponent(name)}` : null

export const initials = (name: string | null | undefined) => {
  const t = (name || '').trim().split(/\s+/)
  return ((t[0]?.[0] || '') + (t.length > 1 ? t[t.length - 1][0] : '')).toUpperCase() || '?'
}
