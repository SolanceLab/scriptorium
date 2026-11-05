// Scriptorium — Copyright (c) 2026 House of Solance. PolyForm Noncommercial 1.0.0. Required Notice: Copyright (c) 2026 House of Solance (https://github.com/SolanceLab)
// A person's notes grow by prepending dated entries ("[2026-10-14] ...") at the
// top, so the newest — including corrections — sit first and the original,
// undated text sits last. For reading, show them newest first by date, and the
// original text last. Display only: the stored notes are never reordered.
const DATED = /^\s*\[\d{4}-\d{2}-\d{2}\]/

export function notesNewestFirst(notes) {
  if (!notes || !notes.trim()) return ''
  const lines = notes.split('\n')
  const head = []
  const entries = []
  let afterBlank = false
  for (const line of lines) {
    if (DATED.test(line)) { entries.push([line]); afterBlank = false }
    else if (entries.length && !afterBlank) {
      if (!line.trim()) afterBlank = true
      else entries[entries.length - 1].push(line)
    } else if (line.trim() || head.length) head.push(line)
  }
  // Same-day entries keep stored order: a prepended line sits above older ones, so it is newer.
  const ordered = entries
    .map((e, i) => ({ text: e.join('\n').trimEnd(), date: e[0].trim().slice(1, 11), i }))
    .sort((a, b) => (a.date === b.date ? a.i - b.i : a.date < b.date ? 1 : -1))
    .map(e => e.text)
  const original = head.join('\n').trim()
  return [...ordered, ...(original ? [original] : [])].join('\n\n')
}
