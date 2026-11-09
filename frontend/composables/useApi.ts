export function useApi() {
  async function api<T>(path: string, opts: Parameters<typeof $fetch>[1] = {}): Promise<T> {
    try {
      return (await $fetch(path, opts as any)) as T
    } catch (e: any) {
      const detail = e?.data?.detail
      throw new Error(
        typeof detail === 'string' ? detail : detail ? JSON.stringify(detail) : e?.message || 'Request failed',
      )
    }
  }
  return { api }
}

/** ISO 8601, UTC, second precision: 2026-11-07T14:03:00Z */
export function isoDate(s: string | null | undefined): string {
  if (!s) return ''
  const d = new Date(s)
  return isNaN(d.getTime()) ? s : d.toISOString().replace(/\.\d{3}Z$/, 'Z')
}

export function money(v: number | null | undefined): string {
  return v == null ? '—' : v.toLocaleString('en-US', { style: 'currency', currency: 'USD' })
}

/** Downscale a phone photo before upload: smaller payload and fewer vision tokens. */
export async function shrinkImage(file: File, maxEdge = 1600): Promise<Blob> {
  const bitmap = await createImageBitmap(file).catch(() => null)
  if (!bitmap) return file
  const scale = Math.min(1, maxEdge / Math.max(bitmap.width, bitmap.height))
  if (scale === 1 && file.size < 2_000_000) return file
  const canvas = document.createElement('canvas')
  canvas.width = Math.round(bitmap.width * scale)
  canvas.height = Math.round(bitmap.height * scale)
  canvas.getContext('2d')!.drawImage(bitmap, 0, 0, canvas.width, canvas.height)
  return await new Promise((res) => canvas.toBlob((b) => res(b || file), 'image/jpeg', 0.88))
}
