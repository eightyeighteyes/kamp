// The name written on a crate's spine (KAMP-656).
//
// Marker on a divider card, not a generated string: it names what is actually in
// this crate, in the order a person would say it. "AUG '26 — MOSTLY DUB TECHNO,
// TWO DEEP CUTS, ONE WILDCARD".
//
// Pure and deterministic. The story is explicitly skin-only — no API and no
// store-shape changes — so everything here comes from the crate snapshot the
// view already has. Nothing is random and nothing reads the clock: the month is
// the crate's own earliest first_seen_at, so a crate keeps its name forever
// rather than being relabelled every time you look at it.
import type { CrateItem } from '../api/client'

const MONTHS = ['JAN', 'FEB', 'MAR', 'APR', 'MAY', 'JUN', 'JUL', 'AUG', 'SEP', 'OCT', 'NOV', 'DEC']

// Small numbers read as handwriting; large ones read as a database. A crate is
// ten records, so this never needs to go past that.
const WORDS = ['NO', 'ONE', 'TWO', 'THREE', 'FOUR', 'FIVE', 'SIX', 'SEVEN', 'EIGHT', 'NINE', 'TEN']

const count = (n: number): string => WORDS[n] ?? String(n)

// How each criterion is described when it is a *minority* of the crate. Keyed on
// the provider's own criterion strings (kamp_daemon/discovery_criteria.py). An
// unknown key is skipped rather than guessed at — a future provider inventing a
// criterion should be silent here, not wrong.
const PHRASES: Record<string, { one: string; many: string }> = {
  older_than_ten: { one: 'ONE DEEP CUT', many: 'DEEP CUTS' },
  best_seller: { one: 'ONE WILDCARD', many: 'WILDCARDS' },
  favorite_artist: { one: 'ONE OLD FRIEND', many: 'OLD FRIENDS' },
  also_like: { one: 'ONE FILED NEXT DOOR', many: 'FILED NEXT DOOR' },
  // KAMP-658. Both are deliberately not "DEEP CUT" or "OLD FRIEND" — those are
  // already spoken for above by different criteria, and reusing them would make
  // the divider card describe the wrong pick.
  lone_album_artist: { one: 'ONE SECOND HELPING', many: 'SECOND HELPINGS' },
  purchase_anniversary: { one: 'ONE FROM A YEAR BACK', many: 'FROM A YEAR BACK' }
}

function stamp(items: CrateItem[]): string {
  const earliest = items.reduce(
    (min, item) => (item.first_seen_at > 0 && item.first_seen_at < min ? item.first_seen_at : min),
    Number.POSITIVE_INFINITY
  )
  if (!Number.isFinite(earliest)) return ''
  const when = new Date(earliest * 1000)
  return `${MONTHS[when.getMonth()]} '${String(when.getFullYear()).slice(2)}`
}

// The genre the crate leans on, from the seed hints the snapshot already carries.
// Upper-cased because the whole label is marker capitals, not because it is
// shouting.
//
// "MOSTLY" is a claim about the majority, so it has to earn it: the genre picks
// must be the biggest group in the crate and at least three of them. Run against
// eleven real crates without that guard, two of them announced "MOSTLY ROCK" off
// the back of one and two genre picks while the other eight were deep cuts —
// a label that is simply untrue, on the one surface whose whole job is that
// every pick explains itself honestly.
const MIN_LEAD = 3

function leading(tallies: Map<string, number>, hints: string[]): string {
  const genreLed = tallies.get('genre_top') ?? 0
  if (genreLed < MIN_LEAD || hints.length === 0) return ''
  const biggest = Math.max(...tallies.values())
  if (genreLed < biggest) return ''
  return `MOSTLY ${hints[0].toUpperCase()}`
}

export function crateSpineName(items: CrateItem[], hints: string[]): string {
  if (items.length === 0) return ''

  const tallies = new Map<string, number>()
  for (const item of items) {
    if (item.criterion) tallies.set(item.criterion, (tallies.get(item.criterion) ?? 0) + 1)
  }

  const parts: string[] = []
  const lead = leading(tallies, hints)
  if (lead) parts.push(lead)

  // Descending, then by key, so the same crate always reads the same way — a
  // Map preserves insertion order, which would otherwise make the name depend on
  // the order the builder happened to fill the crate.
  const rest = [...tallies.entries()]
    .filter(([key]) => key in PHRASES)
    .sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]))

  for (const [key, n] of rest) {
    const phrase = PHRASES[key]
    parts.push(n === 1 ? phrase.one : `${count(n)} ${phrase.many}`)
    // Three clauses is a divider card. Four is an inventory.
    if (parts.length >= 3) break
  }

  const when = stamp(items)
  const body = parts.join(', ')
  if (!body) return when
  return when ? `${when} — ${body}` : body
}
