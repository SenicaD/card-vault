// Wear levels for raw (ungraded) cards. The short codes are what is stored, and what the card identifier
// returns; everything the user sees goes through conditionName() / CONDITIONS instead.
export interface Condition { value: string; name: string; description: string; sports: string }

export const CONDITIONS: Condition[] = [
  { value: 'NM', name: 'Near Mint', sports: 'Sports: NM-MT',
    description: 'Looks new. Sharp corners and a clean surface, at most a tiny white speck on an edge.' },
  { value: 'LP', name: 'Lightly Played', sports: 'Sports: EX to NM',
    description: 'Minor wear: light edge whitening or a few small surface scuffs. No creases.' },
  { value: 'MP', name: 'Moderately Played', sports: 'Sports: EX',
    description: 'Clear wear: noticeable whitening and scuffs, maybe a very light crease. Still flat.' },
  { value: 'HP', name: 'Heavily Played', sports: 'Sports: VG to Good',
    description: 'Heavy wear: creases, rounded corners, scratches or bends, but not torn.' },
  { value: 'DMG', name: 'Damaged', sports: 'Sports: Poor',
    description: 'Major damage: tears, water damage, deep creases, writing or missing pieces.' },
]

const BY_CODE = new Map(CONDITIONS.map((c) => [c.value, c]))

/** "NM" -> "Near Mint". Codes we don't know (older data) are shown as they were entered. */
export function conditionName(code: string | null | undefined): string {
  return code ? BY_CODE.get(code.toUpperCase())?.name ?? code : ''
}
export const conditionInfo = (code: string | null | undefined) => (code ? BY_CODE.get(code.toUpperCase()) : undefined)
