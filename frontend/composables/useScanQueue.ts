import type { Card, CardFields, EbayItem, PriceQuote, ScanResult } from '~/types'

export type ItemStatus = 'queued' | 'processing' | 'ready' | 'error' | 'saving' | 'added'

export interface ScanItem {
  key: number
  fileName: string
  file: File | null // dropped once identified, so the queue doesn't hold every photo in memory
  preview: string
  status: ItemStatus
  scan: ScanResult | null
  form: CardFields | null
  quote: PriceQuote | null
  comps: EbayItem[]
  priceError: string
  error: string
  repricing: boolean
  card: Card | null
}

const CONCURRENCY = 3
export const AUTO_ADD_THRESHOLD = 0.8

/**
 * The scan queue: every photo is identified and priced against eBay as soon as it's added,
 * a few at a time. State lives in useState so it survives navigating to a card and back.
 */
export function useScanQueue() {
  const { api } = useApi()
  const items = useState<ScanItem[]>('scanQueue', () => [])
  const defaultLocation = useState<number | null>('lastLocation', () => null)
  const autoAdd = useState<boolean>('scanAutoAdd', () => false)
  const seq = useState<number>('scanSeq', () => 0)

  const counts = computed(() => {
    const c = { total: items.value.length, pending: 0, ready: 0, error: 0, added: 0 }
    for (const i of items.value) {
      if (i.status === 'queued' || i.status === 'processing' || i.status === 'saving') c.pending++
      else if (i.status === 'ready') c.ready++
      else if (i.status === 'error') c.error++
      else if (i.status === 'added') c.added++
    }
    return c
  })
  const readyValue = computed(() =>
    items.value.filter((i) => i.status === 'ready').reduce((a, i) => a + (i.quote?.median ?? 0) * (i.form?.quantity ?? 1), 0),
  )
  const addedValue = computed(() =>
    items.value.filter((i) => i.status === 'added').reduce((a, i) => a + (i.card?.est_value ?? 0) * (i.card?.quantity ?? 1), 0),
  )

  function addFiles(files: Iterable<File>) {
    for (const file of files) {
      if (!file.type.startsWith('image/')) continue
      items.value.push({
        key: ++seq.value, fileName: file.name, file, preview: URL.createObjectURL(file), status: 'queued',
        scan: null, form: null, quote: null, comps: [], priceError: '', error: '', repricing: false, card: null,
      })
    }
    pump()
  }

  // Reactive proxy for an item, so mutations made here re-render the UI.
  const find = (key: number) => items.value.find((i) => i.key === key)

  function pump() {
    const running = items.value.filter((i) => i.status === 'processing').length
    const next = items.value.filter((i) => i.status === 'queued').slice(0, Math.max(0, CONCURRENCY - running))
    for (const i of next) process(i.key)
  }

  function applyScan(cur: ScanItem, res: ScanResult, keep?: CardFields | null) {
    const i = res.identification
    cur.scan = res
    cur.quote = res.quote
    cur.comps = res.comps
    cur.priceError = res.price_error || ''
    cur.form = {
      category: i.category, name: i.name, set_name: i.set_name, year: i.year,
      card_number: i.card_number, variant: i.variant, grade: i.grade, condition: i.condition,
      quantity: keep?.quantity ?? 1, location_id: keep ? keep.location_id : defaultLocation.value,
      slot: keep?.slot ?? null, notes: keep?.notes ?? null,
      search_query: res.quote?.query || i.search_query,
    }
  }

  // Crop the saved photo to the card, then identify and price the cropped photo.
  async function recrop(key: number, box: { x: number; y: number; w: number; h: number }) {
    const it = find(key)
    if (!it?.scan) return
    it.repricing = true
    it.priceError = ''
    try {
      const res = await api<ScanResult>('/api/scan/crop', { method: 'POST', body: { image_path: it.scan.image_path, box } })
      applyScan(it, res, it.form)
      URL.revokeObjectURL(it.preview)
      it.preview = res.image_url
    } catch (e: any) {
      it.priceError = e.message
    } finally {
      it.repricing = false
    }
  }

  async function process(key: number) {
    const it = find(key)
    if (!it?.file) return
    it.status = 'processing'
    it.error = ''
    try {
      const blob = await shrinkImage(it.file)
      const fd = new FormData()
      fd.append('file', blob, blob === it.file ? it.fileName : 'card.jpg')
      const res = await api<ScanResult>('/api/scan', { method: 'POST', body: fd, query: { price: 'true' } })
      const cur = find(key)
      if (!cur) return // removed while in flight
      applyScan(cur, res)
      cur.file = null
      cur.status = 'ready'
      if (autoAdd.value && res.identification.confidence >= AUTO_ADD_THRESHOLD && cur.quote) save(key)
    } catch (e: any) {
      const cur = find(key)
      if (cur) {
        cur.status = 'error'
        cur.error = e.message
      }
    } finally {
      pump()
    }
  }

  function retry(key: number) {
    const it = find(key)
    if (!it) return
    if (it.scan) {
      it.status = 'ready'
      it.error = ''
      return save(key) // the failure was on save; the scan itself is fine
    }
    it.status = 'queued'
    pump()
  }

  async function reprice(key: number, query?: string) {
    const it = find(key)
    if (!it?.form) return
    const q = (query ?? it.form.search_query ?? it.form.name).trim()
    if (q.length < 2) return
    it.repricing = true
    it.priceError = ''
    try {
      const res = await api<{ quote: PriceQuote; comps: EbayItem[] }>('/api/price', { method: 'POST', body: { query: q } })
      it.quote = res.quote
      it.comps = res.comps
      it.form.search_query = q
    } catch (e: any) {
      it.priceError = e.message
    } finally {
      it.repricing = false
    }
  }

  async function save(key: number) {
    const it = find(key)
    if (!it?.form || !it.scan || it.status !== 'ready' || !it.form.name) return
    it.status = 'saving'
    try {
      // Only carry the quote over if it was for the query being saved; otherwise the card prices fresh later.
      const price = it.quote && it.quote.query === it.form.search_query ? it.quote : null
      it.card = await api<Card>('/api/cards', {
        method: 'POST',
        body: { ...it.form, image_path: it.scan.image_path, id_confidence: it.scan.identification.confidence, price },
      })
      it.status = 'added'
      defaultLocation.value = it.form.location_id
    } catch (e: any) {
      it.status = 'error'
      it.error = `Couldn't save: ${e.message}`
    }
  }

  async function saveAll() {
    const keys = items.value.filter((i) => i.status === 'ready' && i.form?.name).map((i) => i.key)
    for (const k of keys) await save(k) // sequential keeps card ids in scan order
  }

  function remove(key: number) {
    const idx = items.value.findIndex((i) => i.key === key)
    if (idx < 0) return
    URL.revokeObjectURL(items.value[idx].preview)
    items.value.splice(idx, 1)
    pump()
  }

  function clearAdded() {
    for (const i of items.value.filter((i) => i.status === 'added')) remove(i.key)
  }

  // New default location applies to every card still waiting for review.
  watch(defaultLocation, (loc, prev) => {
    for (const i of items.value) if (i.form && i.status !== 'added' && i.form.location_id === prev) i.form.location_id = loc
  })

  return { items, counts, readyValue, addedValue, defaultLocation, autoAdd, addFiles, retry, reprice, recrop, save, saveAll, remove, clearAdded }
}
