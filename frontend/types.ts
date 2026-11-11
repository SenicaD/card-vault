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
}

export interface Card extends CardFields {
  id: number
  status: Status
  image_url: string | null
  id_confidence: number | null
  est_value: number | null
  priced_at: string | null
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
  created_at: string
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

export interface Trend {
  range: TrendRange
  grades: string[]
  asking: TrendSeries[]
  sold: TrendSeries[]
  sold_available: boolean
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
