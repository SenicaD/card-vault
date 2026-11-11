// Grade labels match the backend (app/services/grading.py): "Raw", "PSA 10", "BGS 9.5", ...
export const STANDARD_GRADES = ['Raw', 'PSA 10', 'PSA 9', 'PSA 8', 'BGS 9.5', 'BGS 9', 'CGC 10', 'CGC 9', 'SGC 10']

const NAMES: Record<string, string> = { PSA: 'PSA', BGS: 'BGS', BECKETT: 'BGS', CGC: 'CGC', CSG: 'CGC', SGC: 'SGC', HGA: 'HGA' }
const GRADED = /\b(PSA|BGS|BECKETT|CGC|CSG|SGC|HGA)(?![A-Za-z])\s*[#:-]?\s*(?:(?:GEM\s*(?:MT|MINT)|NM-?MT|NEAR\s*MINT|MINT|PRISTINE)\s*)?(10|9\.5|9|8\.5|8|7\.5|7|6\.5|6|5\.5|5|4\.5|4|3|2\.5|2|1\.5|1)(?![\d.]*\d)/i

/** The canonical grade label for what the user typed in the card's Grade field. Empty or unreadable is Raw. */
export function normalizeGrade(grade: string | null | undefined): string {
  const m = grade ? GRADED.exec(grade) : null
  return m ? `${NAMES[m[1].toUpperCase()]} ${m[2]}` : 'Raw'
}

/** Raw first, then graders A-Z, highest grade first. */
export function sortGrades(grades: string[]): string[] {
  const key = (g: string): [number, string, number] => {
    if (g === 'Raw') return [0, '', 0]
    const [grader, num] = g.split(' ')
    return isNaN(Number(num)) ? [2, g, 0] : [1, grader, -Number(num)]
  }
  return [...grades].sort((a, b) => {
    const x = key(a), y = key(b)
    return x[0] - y[0] || x[1].localeCompare(y[1]) || x[2] - y[2]
  })
}

// Every grade offered in the Grade dropdown, grouped by grader. Raw first; the field also accepts free text.
const SCALES: Record<string, string[]> = {
  PSA: ['10', '9', '8.5', '8', '7.5', '7', '6', '5', '4', '3', '2', '1'],
  BGS: ['10', '9.5', '9', '8.5', '8', '7.5', '7', '6', '5', '4', '3', '2', '1'],
  CGC: ['10', '9.5', '9', '8.5', '8', '7.5', '7', '6', '5', '4', '3', '2', '1'],
  SGC: ['10', '9.5', '9', '8.5', '8', '7', '6', '5', '4', '3', '2', '1'],
  HGA: ['10', '9.5', '9', '8.5', '8', '7', '6', '5', '4', '3', '2', '1'],
}
export const GRADE_OPTIONS: string[] = ['Raw', ...Object.entries(SCALES).flatMap(([grader, nums]) => nums.map((n) => `${grader} ${n}`))]
