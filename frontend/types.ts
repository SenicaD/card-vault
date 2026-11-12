export type Category = 'sports' | 'pokemon' | 'mtg' | 'yugioh' | 'other_tcg' | 'other'
export type Status = 'in_stock' | 'listed' | 'sold'

export interface CardFields {
  category: Category
  name: string
  set_name: string | null
  year: string | null
  card_number: string | null
  variant: string | null
  grade: string | null
  condition: string | null
  quantity: number
  location_id: number | null
  slot: string | null
  notes: string | null
  search_query: string | null
  cost?: number | null // paid per copy
  batch?: string | null // scan session it was added in
}

export interface Card extends CardFields {
  id: number
  status: Status
  image_url: string | null
  id_confidence: number | null
  est_value: number | null
  priced_at: string | null
  grade_counts?: Record<string, number> | null
  grade_counts_at?: string | null
  parallel?: string | null
  reviewed?: boolean
  sold_value?: number | null // what it sold for (median of recent sales), beside the asking-based estimate
  sold_count?: number
  sold_low?: number | null
  sold_high?: number | null
  sold_at?: string | null
  value?: number | null // the value to show: sold when enough sales, else the asking estimate
  value_basis?: 'sold' | 'asking' | null
  created_at: string
  updated_at: string
}

export interface Identification {
  category: Category
  name: string
  set_name: string | null
  year: string | null
  card_number: string | null
  variant: string | null
  grade: string | null
  condition: string | null
  confidence: number
  search_query: string
}

export interface PriceQuote {
  query: string
  source: string
  sample_size: number
  low: number | null
  median: number | null
  mean: number | null
  high: number | null
  currency: string
  top_listings?: EbayItem[] | null
}

export interface ScanResult {
  image_path: string
  image_url: string
  identifier: string
  identification: Identification
  quote: PriceQuote | null
  comps: EbayItem[]
  price_error: string | null
}

export interface Location {
  id: number
  name: string
  kind: string
  notes: string | null
  card_count: number
  created_at: string
}

export interface PriceSnapshot {
  id: number
  query: string
  source: string
  sample_size: number
  low: number | null
  median: number | null
  mean: number | null
  high: number | null
  currency: string
  top_listings?: EbayItem[] | null
  price_refs?: { low?: EbayItem; median?: EbayItem; high?: EbayItem } | null
  grade?: string | null
  parallel?: string | null
  spread?: number | null
  trimmed?: number | null
  created_at: string
}

export interface MatchRow {
  item_id: string
  title: string
  price: number
  currency: string
  grade: string
  parallel: string
  condition: string | null
  url: string | null
  image_url: string | null
  excluded: boolean
}

export interface SimilarityResult {
  scores: Record<string, number>
  suggested_parallel: string | null
  votes: number
  of: number
}

export interface TrendPoint {
  date: string
  median: number
  low: number | null
  high: number | null
  samples: number
}

export type TrendRange = '7d' | '1m' | '3m' | '1y' | 'all'

export interface TrendSeries {
  grade: string
  points: TrendPoint[]
}

export interface SalePoint {
  id: number
  date: string
  price: number
  grade: string | null
}

export interface Sale {
  id: number
  card_id: number
  price: number
  grade: string | null
  note: string | null
  sold_at: string
}

// A listing that disappeared from eBay: its last asking price is our estimate of a sale.
export interface GoneListing {
  id: number
  item_id: string
  grade: string
  title: string
  price: number
  url: string | null
  image_url: string | null
  gone_at: string | null
  verdict: 'not_found' | 'out_of_stock' | null
}

export interface Trend {
  range: TrendRange
  grades: string[]
  asking: TrendSeries[]
  sold: TrendSeries[]
  sold_source: 'sold' | 'estimated' | null
  sales: SalePoint[]
  first_recorded: string | null
  grades_available: string[]
}

export interface PriceResult {
  snapshot: PriceSnapshot
  snapshots: PriceSnapshot[]
  comps: EbayItem[]
  grade_counts: Record<string, number>
}

export interface EbayItem {
  item_id: string
  title: string
  price: number
  currency: string
  condition: string | null
  url: string | null
  image_url?: string | null
  grade?: string | null
}

export interface Listing {
  id: number
  sku: string
  ebay_listing_id: string | null
  title: string
  price: number
  status: string
  url: string | null
  created_at: string
}

export interface Stats {
  total_cards: number
  total_value: number
  asking_value: number
  asking_cards: number
  sold_value: number
  sold_cards: number
  asking_value_of_sold_cards: number
  by_category: Record<string, number>
  by_status: Record<string, number>
  modes: { identifier: string; ebay: string }
}

export const CATEGORY_LABELS: Record<Category, string> = {
  sports: 'Sports',
  pokemon: 'Pokémon',
  mtg: 'Magic',
  yugioh: 'Yu-Gi-Oh!',
  other_tcg: 'Other TCG',
  other: 'Other',
}

export const STATUS_LABELS: Record<Status, string> = {
  in_stock: 'In stock',
  listed: 'Listed',
  sold: 'Sold',
}

export interface RepriceStatus {
  state: 'idle' | 'running' | 'done'
  total?: number
  done?: number
  failed?: number
  changed?: number
  unchanged?: number
  top_changes?: { id: number; name: string; before: number | null; after: number | null }[]
}

export interface Mover { id: number; name: string; grade: string; before: number; now: number; change: number; pct: number }

export interface Portfolio {
  range: string
  totals: {
    cards: number; distinct: number; value: number; unpriced: number
    sold_value: number; sold_cards: number; asking_value_of_sold_cards: number; asking_value: number; asking_cards: number
    cost: number; costed_cards: number; uncosted_cards: number; gain: number | null; gain_pct: number | null
  }
  series: { date: string; value: number }[]
  change: number
  by_location: { name: string; cards: number; value: number }[]
  by_category: { name: string; cards: number; value: number }[]
  top_cards: { id: number; name: string; grade: string; quantity: number; value: number | null; basis: 'sold' | 'asking' | null; image_url: string | null }[]
  gainers: Mover[]
  losers: Mover[]
}

export interface GradeCompare {
  grade: string
  asking_median: number | null
  asking_count: number
  sold_median: number | null
  sold_count: number
  gap: number | null
  gap_pct: number | null
  days_median: number | null
  sold_source: 'sold' | 'estimated' | null
  yours_median: number | null
  yours_count: number
}

export interface SoldRow {
  id: number
  item_id: string
  grade: string
  parallel: string
  title: string
  price: number
  currency: string
  url: string | null
  image_url: string | null
  first_seen: string | null
  gone_at: string | null
  days_listed: number | null
  verdict: 'not_found' | 'out_of_stock' | 'sold' | null
  source: 'sold' | 'estimated'
  best_offer: boolean
  vs_asking_pct: number | null
}

export interface SoldProviderInfo { configured: boolean; name: string | null; last_checked: string | null; budget: number; used: number; on_record: number; since: string | null }
export interface SoldCompare {
  days: number
  source: 'sold' | 'estimated'
  grades: GradeCompare[]
  rows: SoldRow[]
  provider: SoldProviderInfo
}

export interface TraceCall { api: string; purpose: string; detail: string; ms: number; returned: number | null; error: string | null }
export interface TraceFunnel {
  grade: string; found: number; excluded: number; other_card?: number; other_version: number; outliers: number; used: number
  low: number | null; median: number | null; high: number | null; spread: number | null
  fences: { q1: number; q3: number; lo: number; hi: number } | null
}
export type Decision = 'junk' | 'unknown_grade' | 'grade_not_requested' | 'other_card' | 'excluded' | 'other_version' | 'outlier' | 'used'
export interface TraceListing { id: string; t: string; p: number; g: string | null; v: string | null; d: Decision; r: 'low' | 'median' | 'high' | null; u: string | null }
export interface TraceSummary {
  id: number; kind: 'lookup' | 'sold'; created_at: string; trigger: string | null; mode: string | null; query: string | null; grades: string[] | null
  parallel: string | null; duration_ms: number | null; error: string | null; has_listings: boolean; has_raw: boolean
  calls: number; notes: number; medians: Record<string, number | null>
}
export interface Trace extends Omit<TraceSummary, 'calls' | 'notes' | 'medians'> {
  calls: TraceCall[] | null; funnel: TraceFunnel[] | null; summary: Record<string, any> | null
  listings: TraceListing[] | null; notes: string[] | null
}
export interface TraceCompare {
  a: { id: number; created_at: string; trigger: string | null; mode: string | null; query: string | null }
  b: { id: number; created_at: string; trigger: string | null; mode: string | null; query: string | null }
  search_changed: { query: boolean; mode: boolean; version: boolean }
  grades: { grade: string; a_median: number | null; b_median: number | null; a_used: number | null; b_used: number | null }[]
  new: TraceListing[]; gone: TraceListing[]
  price_changed: (TraceListing & { was: number })[]
  decision_changed: (TraceListing & { was_decision: Decision })[]
  unchanged: number
}
export interface IdentTraceRow {
  id: number; created_at: string; trigger: string | null; image_path: string | null; source_image: string | null; notes: string[] | null
  identity: {
    identifier: string; photo_kb: number | null; crop: { x: number; y: number; w: number; h: number } | null
    matches: { id: string; t: string; p: number; u: string | null; read: boolean; junk: boolean }[]
    matched_total: number; read_limit: number; result: Record<string, any>
  } | null
}
export interface JobRunRow { id: number; kind: 'daily' | 'bulk'; started_at: string; finished_at: string | null; total: number; refreshed: number; failed: number; errors: { card_id: number; query: string; name?: string; error: string }[] | null }
export interface EbayEvent { ts: string; api: string; purpose: string; detail: string; ms: number; returned: number | null; error: string | null; rate_limited?: boolean }

export interface PricePoints {
  grade: string
  range: string
  sales: { date: string; price: number; best_offer: boolean; title: string; url: string | null }[]
  listings: { first: string; last: string; price: number; status: string; title: string; url: string | null }[]
  asking_line: TrendPoint[]
  sold_line: TrendPoint[]
  asking_now: number | null
  sold_median: number | null
  sold_count: number
  asking_count: number
}
