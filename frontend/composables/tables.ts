// Shared by components/DataTable.vue and pages that need the rows a table shows (the Matching listings tick-all box).

/** A v-data-table header, plus `text`: what the cell shows, for search. */
export interface TableHeader<T = any> {
  title: string
  key: string
  value?: (item: T) => unknown
  text?: (item: T) => string
  sortable?: boolean
  sortRaw?: (a: T, b: T) => number
  align?: 'start' | 'center' | 'end'
  width?: number | string
  [k: string]: unknown
}

/** The column's raw value for a row: the header's value function, or the field named by its key. */
export function columnValue<T>(header: TableHeader<T>, item: T): unknown {
  return header.value ? header.value(item) : (item as any)[header.key]
}

/** Rows where the trimmed, lower-cased query is part of the shown text of any sortable column. */
export function searchRows<T>(items: T[], headers: TableHeader<T>[], query: string | null | undefined): T[] {
  const q = (query || '').toLowerCase().trim()
  if (!q) return items
  const cols = headers.filter((h) => h.sortable !== false)
  return items.filter((item) =>
    cols.some((h) => (h.text ? h.text(item) : String(columnValue(h, item) ?? '')).toLowerCase().includes(q)),
  )
}

/** A sort for one column: empty values last both ways, numbers by size, text without regard to case and with
 *  "PSA 9" before "PSA 10". Vuetify swaps a and b for descending, so `desc` says which way the column is sorted. */
export function columnCompare<T>(header: TableHeader<T>, desc: () => boolean) {
  return (a: T, b: T): number => {
    const x = columnValue(header, a)
    const y = columnValue(header, b)
    const xe = x == null || x === ''
    const ye = y == null || y === ''
    if (xe || ye) {
      if (xe && ye) return 0
      return xe !== desc() ? 1 : -1
    }
    if (typeof x === 'number' && typeof y === 'number') return x - y
    return String(x).localeCompare(String(y), undefined, { numeric: true, sensitivity: 'base' })
  }
}
