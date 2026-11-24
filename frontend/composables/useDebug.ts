export interface ApiCall { ts: number; method: string; path: string; ok: boolean; ms: number; error?: string }

/** Debug mode: shows how numbers are worked out, and records this browser's own requests. */
export function useDebug() {
  const on = useState<boolean>('debugMode', () => false)
  const calls = useState<ApiCall[]>('debugCalls', () => [])
  return { on, calls }
}

export const DECISION_LABEL: Record<string, string> = {
  junk: 'Lot, bundle or reprint',
  unknown_grade: 'No grade in the title',
  grade_not_requested: 'Another grade',
  excluded: 'You unticked it',
  other_card: 'Different card',
  other_identity: 'Different number, redemption or auto',
  not_alike: "Doesn't look like your photo",
  other_version: 'Different version',
  outlier: 'Price outlier',
  used: 'In the search median',
}
export const DECISION_COLOR: Record<string, string> = {
  junk: 'grey', unknown_grade: 'grey', grade_not_requested: 'grey', excluded: 'warning', other_card: 'warning', other_identity: 'warning', not_alike: 'warning', other_version: 'warning', outlier: 'error', used: 'success',
}
export const TRIGGER_LABEL: Record<string, string> = { manual: 'Look up', daily: 'Daily refresh', bulk: 'Bulk re-price', crop: 'Crop', scan: 'Scan', auto: 'Automatic', add: 'Added to inventory' }
export const stamp = (s: string | null | undefined) => (s ? isoDate(s).replace('T', ' ').slice(0, 16) : '')
