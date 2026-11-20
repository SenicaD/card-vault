// Reading drops that include folders. dataTransfer.files only lists the top level, and a folder in it is
// a single typeless File, so the folders are walked through the entries API instead.

/** Path order with numbers compared as numbers, so card2.jpg comes before card10.jpg. */
export const comparePaths = (a: string, b: string) => a.localeCompare(b, undefined, { numeric: true })

const fileOf = (e: FileSystemFileEntry) => new Promise<File>((resolve, reject) => e.file(resolve, reject))
const readBatch = (r: FileSystemDirectoryReader) => new Promise<FileSystemEntry[]>((resolve, reject) => r.readEntries(resolve, reject))

async function walk(dir: FileSystemDirectoryEntry, out: { path: string; file: File }[]) {
  const reader = dir.createReader()
  // Chrome returns at most 100 entries per call; the folder is done only when a call returns none.
  for (;;) {
    const batch = await readBatch(reader)
    if (!batch.length) return
    for (const e of batch) {
      if (e.isDirectory) await walk(e as FileSystemDirectoryEntry, out)
      else out.push({ path: e.fullPath, file: await fileOf(e as FileSystemFileEntry) })
    }
  }
}

/** Every file in a drop, subfolders included. Each dropped folder is in path order; top-level items keep the browser's order. */
export async function filesFromDrop(dt: DataTransfer): Promise<File[]> {
  // The browser empties dt.items once the drop handler yields, so every entry is taken before the first await.
  const dropped = [...dt.items].filter((i) => i.kind === 'file').map((i) => {
    const entry = i.webkitGetAsEntry()
    return { entry, file: entry ? null : i.getAsFile() }
  })
  const files: File[] = []
  for (const { entry, file } of dropped) {
    if (!entry) { if (file) files.push(file); continue }
    if (entry.isFile) { files.push(await fileOf(entry as FileSystemFileEntry)); continue }
    const found: { path: string; file: File }[] = []
    await walk(entry as FileSystemDirectoryEntry, found)
    found.sort((a, b) => comparePaths(a.path, b.path))
    files.push(...found.map((f) => f.file))
  }
  return files
}
