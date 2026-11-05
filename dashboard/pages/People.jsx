// Solance House — Copyright (c) 2026 House of Solance. All rights reserved. Required Notice: Copyright (c) 2026 House of Solance (https://github.com/SolanceLab)
import { useState, useEffect, useMemo, useRef } from 'react'
import { notesNewestFirst } from './peopleNotes'
import { useHost } from '../host.js'

// The adapter marks a "not signed in" failure with err.auth = true; the page stays quiet on it.
const isAuth = (err) => !!(err && err.auth === true)

// kind — human reads neutral cream; AI reads blue (the "sensed / digital" signal,
// per the gold=editorial / blue=system-state split). Gold is reserved for
// editorial emphasis (page-title word, active filter pill).
const KIND_TOKENS = {
  human: { label: 'human', color: 'var(--page-dim)', border: 'var(--rule-mid)' },
  ai:    { label: 'AI',    color: 'var(--blue)',     border: 'var(--blue-soft)' },
  pet:   { label: 'pet',   color: 'var(--page-dim)', border: 'var(--rule-mid)' },
}

// The five circles (16 Oct 2026). The API and the database refuse any other; a sixth exists once the owner creates it.
const CIRCLES = ['Family', 'Friends', 'Work', 'School', 'AI Companionship Community']

const KIND_FILTERS = [
  { key: 'all',   label: 'All' },
  { key: 'human', label: 'Human' },
  { key: 'ai',    label: 'AI' },
  { key: 'pet',   label: 'Pet' },
]

// Closeness — the primary grouping. Ordered tiers; Watchlist gets a gold tint
// (the one to keep an eye on), the rest stay quiet.
const CLOSENESS_ORDER = ['Family', 'Close friends', 'Acquaintance', 'Watchlist']
const CLOSENESS_TOKENS = {
  'Family':        { color: 'var(--page)',      border: 'var(--rule-mid)' },
  'Close friends': { color: 'var(--page)',      border: 'var(--rule-mid)' },
  'Acquaintance':  { color: 'var(--page-dim)',  border: 'var(--rule-mid)' },
  'Watchlist':     { color: 'var(--gold-text)', border: 'var(--gold-soft)' },
}

const pillClass = "px-2 py-0.5 text-xs uppercase tracking-[0.1em] rounded border"
const inputClass = "w-full text-base bg-[color:var(--ink-deep)] border border-[color:var(--rule-mid)] rounded-md px-3 py-2 text-[color:var(--page)] placeholder:text-[color:var(--page-faint)] font-[Söhne,sans-serif] focus:outline-none focus:border-[color:var(--gold-soft)]"
const metaClasses = "text-[11px] uppercase tracking-[0.18em] font-light text-[color:var(--page-muted)] font-[JetBrains_Mono,monospace] tabular-nums"
const labelClasses = "text-xs uppercase tracking-[0.1em] text-[color:var(--page-dim)]"
const fieldLabel = labelClasses + ' block mb-1.5'

// What this particular row loses if it goes. Named specifically, because a
// fixed "their standing and notes go too" overstates it for a bare row and
// understates it for one carrying satellites.
function whatGoesWithThem(person, incoming = 0) {
  const parts = []
  if (person.standing && person.standing.trim()) parts.push('standing')
  if (person.notes && person.notes.trim()) parts.push('notes')
  const conns = Array.isArray(person.connections) ? person.connections.length : 0
  if (conns) parts.push(conns === 1 ? 'the person around them' : `the ${conns} people around them`)
  // Links both ways go with the row (ON DELETE CASCADE): theirs, and the ones on other cards pointing at them.
  const links = (Array.isArray(person.links) ? person.links.length : 0) + incoming
  if (links) parts.push(links === 1 ? 'their one relation (Undo cannot bring it back)' : `their ${links} relations (Undo cannot bring them back)`)
  if (parts.length === 0) return null
  if (parts.length === 1) return `Their ${parts[0]} goes with them.`
  const list = parts.slice(0, -1).join(', ') + ' and ' + parts[parts.length - 1]
  return `Their ${list} go with them.`
}

function isoDate(dateStr) {
  if (!dateStr) return ''
  return String(dateStr).slice(0, 10)
}

const MONTHS = ['January', 'February', 'March', 'April', 'May', 'June', 'July', 'August', 'September', 'October', 'November', 'December']

// '11-26' → '26 November'; '1989-01-13' → '13 January 1989'
function birthdayLabel(b) {
  const m = /^(?:(\d{4})-)?(\d{2})-(\d{2})$/.exec(String(b || ''))
  if (!m) return b
  return `${Number(m[3])} ${MONTHS[Number(m[2]) - 1] || m[2]}${m[1] ? ' ' + m[1] : ''}`
}

// The person's own clock, from an IANA zone; nothing when the zone is missing or unknown.
function localTime(tz) {
  if (!tz) return ''
  try {
    return new Intl.DateTimeFormat('en-GB', { timeZone: tz, hour: '2-digit', minute: '2-digit', hourCycle: 'h23' }).format(new Date())
  } catch { return '' }
}

function ConnectionChip({ c }) {
  const k = KIND_TOKENS[c?.kind] || null
  const isAi = c?.kind === 'ai'
  return (
    <span
      className="px-2 py-0.5 text-xs rounded border font-[Söhne,sans-serif]"
      style={{
        color: isAi ? 'var(--blue)' : 'var(--page-dim)',
        borderColor: isAi ? 'var(--blue-soft)' : 'var(--rule-mid)',
      }}
      title={c?.note || undefined}
    >
      {c?.name}
      {c?.relation && (
        <span className="text-[color:var(--page-faint)]"> · {c.relation}</span>
      )}
      {k && <span className="text-[color:var(--page-faint)] uppercase tracking-[0.08em] text-[10px]"> {k.label}</span>}
    </span>
  )
}

// "Found in chats": facts the nightly reader found in the owner's chats, straight onto the card with an edit button
// (DECISIONS found-in-chats-shape). Loaded only when opened, so the page doesn't ask for every card at once.
function FoundInChats({ personId }) {
  const { adapter } = useHost()
  const [open, setOpen] = useState(false)
  const [facts, setFacts] = useState(null)
  const [editing, setEditing] = useState(null)   // { id, text }
  const [busy, setBusy] = useState(null)
  const [error, setError] = useState(null)

  const load = async () => {
    setError(null)
    try {
      const d = await adapter.listFacts(personId)
      setFacts(d.facts || [])
    } catch (err) {
      if (!isAuth(err)) setError(err.message || 'Could not load')
    }
  }
  const toggle = () => { const o = !open; setOpen(o); if (o && facts === null) load() }
  const save = async (id, body) => {
    setBusy(id); setError(null)
    try {
      if (body.remove) await adapter.removeFact(id)
      else await adapter.editFact(id, body.fact)
      setEditing(null)
      await load()
    } catch (err) {
      if (!isAuth(err)) setError(err.message || 'Could not save')
    } finally { setBusy(null) }
  }

  return (
    <div className="pt-2 border-t border-[color:var(--rule-dim)]">
      <button onClick={toggle} className={labelClasses + ' min-h-[44px] flex items-center gap-1.5 hover:text-[color:var(--page)] transition-colors'}>
        <span>Found in chats</span>
        {facts && <span className={metaClasses}>{facts.length}</span>}
        <span className="text-[color:var(--page-faint)] normal-case tracking-normal">{open ? '▾' : '▸'}</span>
      </button>
      {open && (
        <div className="space-y-2 mt-1">
          {facts === null && !error && <p className="text-sm text-[color:var(--page-faint)]">Loading…</p>}
          {facts && facts.length === 0 && <p className="text-sm text-[color:var(--page-faint)] font-[Söhne,sans-serif]">Nothing found in chats yet.</p>}
          {facts && facts.map(f => (
            <div key={f.id} className="flex flex-col sm:flex-row sm:items-start gap-1 sm:gap-3">
              <span className={metaClasses + ' shrink-0 sm:pt-0.5'}>{f.said_on}</span>
              <div className="min-w-0 flex-1">
                {editing?.id === f.id ? (
                  <div className="flex gap-2">
                    <input className="flex-1 min-w-0 text-base bg-[color:var(--ink)] border border-[color:var(--gold-soft)] rounded-md px-2 py-1.5 text-[color:var(--page)] font-[Söhne,sans-serif]"
                      value={editing.text} onChange={e => setEditing({ id: f.id, text: e.target.value })} />
                    <button onClick={() => save(f.id, { fact: editing.text })} disabled={busy === f.id || !editing.text.trim()}
                      className="min-h-[44px] px-3 text-sm border border-[color:var(--gold-soft)] text-[color:var(--gold-text)] rounded-md disabled:opacity-50">Save</button>
                    <button onClick={() => setEditing(null)} className="min-h-[44px] px-2 text-sm text-[color:var(--page-dim)]">Cancel</button>
                  </div>
                ) : (
                  <p className="text-sm text-[color:var(--page)] font-[Söhne,sans-serif] break-words" title={[f.quote && `“${f.quote}”`, f.source].filter(Boolean).join(' · ') || undefined}>
                    {f.fact}
                    {f.source && <span className="text-[color:var(--page-faint)]"> · {/^chat\b/.test(f.source) ? 'chat' : f.source}</span>}
                    {f.status === 'edited' && <span className="text-[color:var(--page-faint)]"> · edited</span>}
                  </p>
                )}
              </div>
              {editing?.id !== f.id && (
                <div className="flex gap-1 shrink-0">
                  <button onClick={() => setEditing({ id: f.id, text: f.fact })} className="min-h-[44px] px-2 text-xs uppercase tracking-[0.1em] text-[color:var(--page-dim)] hover:text-[color:var(--gold-text)]">Edit</button>
                  <button onClick={() => save(f.id, { remove: true })} disabled={busy === f.id} className="min-h-[44px] px-2 text-xs uppercase tracking-[0.1em] text-[color:var(--page-faint)] hover:text-[color:var(--gold-text)] disabled:opacity-50">Remove</button>
                </div>
              )}
            </div>
          ))}
          {error && <p className="text-sm text-[color:var(--gold-text)]">{error}</p>}
        </div>
      )}
    </div>
  )
}

function PersonCard({ person, incoming = 0, onEdit, onDelete, deleting, canEdit = true }) {
  const [open, setOpen] = useState(false)
  // Two steps on purpose. There is no red in the house palette, so the guard
  // against a mis-tap is a second deliberate press, not a colour.
  const [confirming, setConfirming] = useState(false)
  const kind = KIND_TOKENS[person.kind] || KIND_TOKENS.human
  const connections = Array.isArray(person.connections) ? person.connections : []
  const circles = Array.isArray(person.circles) ? person.circles : []
  const links = Array.isArray(person.links) ? person.links : []
  const discordIds = Array.isArray(person.discord_ids) ? person.discord_ids : []
  const hasNotes = !!(person.notes && person.notes.trim())
  const accounts = Array.isArray(person.accounts) ? person.accounts.filter(a => a && a.handle) : []
  const dates = Array.isArray(person.dates) ? person.dates.filter(d => d && d.label && d.date) : []
  const facts = [
    person.birthday && { label: 'Birthday', value: birthdayLabel(person.birthday) },
    person.place && { label: 'Place', value: person.place + (localTime(person.timezone) ? ` · ${localTime(person.timezone)} there` : '') },
    !person.place && localTime(person.timezone) && { label: 'Their time', value: localTime(person.timezone) },
    person.work && { label: 'Work', value: person.work },
    person.runs_on && { label: 'Runs on', value: person.runs_on },
  ].filter(Boolean)

  return (
    <div className="bg-[color:var(--ink-deep)] border border-[color:var(--rule-mid)] rounded-lg p-4 sm:p-5 space-y-3">
      {/* Header — name + kind + relationship + group + last-mentioned + edit */}
      <div className="flex flex-wrap items-center gap-2">
        <h3 className="text-[1.6rem] leading-[1.15] font-[Cormorant_Garamond,serif] font-normal tracking-[-0.01em] text-[color:var(--page)] mr-1">
          {person.name}
        </h3>

        {person.pronouns && (
          <span className={pillClass} style={{ color: 'var(--page-dim)', borderColor: 'var(--rule-mid)' }}>
            {person.pronouns}
          </span>
        )}

        <span className={pillClass} style={{ color: kind.color, borderColor: kind.border }}>
          {kind.label}
        </span>

        {person.relationship && (
          <span className={pillClass} style={{ color: (CLOSENESS_TOKENS[person.relationship]?.color) || 'var(--page-dim)', borderColor: (CLOSENESS_TOKENS[person.relationship]?.border) || 'var(--rule-mid)' }}>
            {person.relationship}
          </span>
        )}

        {person.group_context && (
          <span className={pillClass} style={{ color: 'var(--page-faint)', borderColor: 'var(--rule-dim)' }}>
            {person.group_context}
          </span>
        )}

        {circles.map(c => (
          <span key={c} className={pillClass} style={{ color: 'var(--gold-text)', borderColor: 'var(--gold-soft)' }}>
            {c}
          </span>
        ))}
        {circles.length === 0 && (
          <span className={pillClass} style={{ color: 'var(--page-faint)', borderColor: 'var(--rule-dim)', borderStyle: 'dashed' }}>
            no circle yet
          </span>
        )}

        <div className="ml-auto flex items-center gap-3">
          {person.last_mentioned_at && (
            <span className={metaClasses}>{isoDate(person.last_mentioned_at)}</span>
          )}
          {canEdit && (<>
          <button
            onClick={() => onEdit(person)}
            className="text-xs uppercase tracking-[0.1em] text-[color:var(--page-dim)] hover:text-[color:var(--gold-text)] transition-colors"
          >
            Edit
          </button>
          <button
            onClick={() => setConfirming(true)}
            disabled={deleting}
            className="text-xs uppercase tracking-[0.1em] text-[color:var(--page-faint)] hover:text-[color:var(--gold-text)] transition-colors disabled:opacity-50"
          >
            Remove
          </button>
          </>)}
        </div>
      </div>

      {/* Care — warnings / handle with care, always first, never shortened */}
      {person.care && (
        <div className="border-l border-[color:var(--gold-soft)] pl-3 py-0.5">
          <span className={labelClasses + ' block mb-1 text-[color:var(--gold-text)]'}>Care</span>
          <p className="text-[15px] leading-relaxed text-[color:var(--page)] font-[Söhne,sans-serif] whitespace-pre-wrap">{person.care}</p>
        </div>
      )}

      {/* Fixed facts — birthday, place (+ their local time), work, runs on */}
      {facts.length > 0 && (
        <div className="flex flex-wrap gap-x-5 gap-y-1">
          {facts.map(f => (
            <span key={f.label} className="text-sm font-[Söhne,sans-serif] text-[color:var(--page)]">
              <span className={labelClasses + ' mr-1.5'}>{f.label}</span>{f.value}
            </span>
          ))}
        </div>
      )}

      {/* Accounts + dates */}
      {accounts.length > 0 && (
        <div className="flex flex-wrap items-center gap-1.5">
          <span className={labelClasses + ' mr-1'}>Accounts</span>
          {accounts.map((a, i) => (
            <span key={'a' + i} className="px-2 py-0.5 text-xs rounded border font-[Söhne,sans-serif]" style={{ color: 'var(--page)', borderColor: 'var(--rule-mid)' }}>
              {a.handle}<span className="text-[color:var(--page-faint)]"> · {a.platform}</span>
            </span>
          ))}
        </div>
      )}
      {dates.length > 0 && (
        <div className="flex flex-wrap items-center gap-1.5">
          <span className={labelClasses + ' mr-1'}>Dates</span>
          {dates.map((d, i) => (
            <span key={'d' + i} className="px-2 py-0.5 text-xs rounded border font-[Söhne,sans-serif]" style={{ color: 'var(--page)', borderColor: 'var(--rule-mid)' }}>
              {d.label}<span className="text-[color:var(--page-faint)] font-[JetBrains_Mono,monospace] tabular-nums"> · {d.date}</span>
            </span>
          ))}
        </div>
      )}

      {/* Standing */}
      {person.standing && (
        <p className="text-[15px] leading-relaxed text-[color:var(--page)] font-[Söhne,sans-serif]">
          {person.standing}
        </p>
      )}

      {/* Relations — links to other people's cards */}
      {links.length > 0 && (
        <div className="flex flex-wrap items-center gap-1.5">
          <span className={labelClasses + ' mr-1'}>Relations</span>
          {links.map(l => (
            <span key={l.id || l.other_id + l.relation} className="px-2 py-0.5 text-xs rounded border font-[Söhne,sans-serif]"
              style={{ color: 'var(--page)', borderColor: 'var(--rule-mid)' }}>
              {linkName(l) || 'someone'}<span className="text-[color:var(--page-faint)]"> · {l.relation}</span>
            </span>
          ))}
        </div>
      )}

      {/* Connections — the people around them */}
      {connections.length > 0 && (
        <div className="flex flex-wrap items-center gap-1.5">
          <span className={labelClasses + ' mr-1'}>Around them</span>
          {connections.map((c, i) => <ConnectionChip key={i} c={c} />)}
        </div>
      )}

      {/* Notes — collapsible (they accumulate) */}
      {hasNotes && (
        <div className="pt-2 border-t border-[color:var(--rule-dim)]">
          <button
            onClick={() => setOpen(o => !o)}
            className={labelClasses + ' flex items-center gap-1.5 hover:text-[color:var(--page)] transition-colors'}
          >
            <span>Notes</span>
            <span className="text-[color:var(--page-faint)] normal-case tracking-normal">{open ? '▾' : '▸'}</span>
          </button>
          {open && (
            <p className="mt-2 text-sm text-[color:var(--page-dim)] leading-relaxed whitespace-pre-wrap font-[Söhne,sans-serif]">
              {notesNewestFirst(person.notes)}
            </p>
          )}
        </div>
      )}

      <FoundInChats personId={person.id} />

      {/* Confirm strip — names the person and what goes with them */}
      {confirming && (
        <div className="border border-[color:var(--gold-soft)] bg-[color:var(--gold-glow)] rounded-md p-3 space-y-3">
          <p className="text-sm text-[color:var(--page)] font-[Söhne,sans-serif] leading-relaxed">
            Remove <span className="text-[color:var(--gold-text)]">{person.name}</span> from the roster?
            {whatGoesWithThem(person, incoming) && ' ' + whatGoesWithThem(person, incoming)}
          </p>
          <div className="flex items-center gap-2">
            <button
              onClick={() => onDelete(person)}
              disabled={deleting}
              className="min-h-[44px] px-4 text-sm border border-[color:var(--gold-soft)] text-[color:var(--gold-text)] rounded-md transition-colors hover:bg-[color:var(--gold-glow)] disabled:opacity-50"
            >
              {deleting ? 'Removing…' : 'Remove ' + person.name}
            </button>
            <button
              onClick={() => setConfirming(false)}
              disabled={deleting}
              className="min-h-[44px] px-4 text-sm border border-[color:var(--rule-mid)] text-[color:var(--page-dim)] rounded-md transition-colors hover:text-[color:var(--page)] hover:border-[color:var(--gold-soft)] disabled:opacity-50"
            >
              Keep them
            </button>
          </div>
        </div>
      )}

      {/* ID (+ confirmed Discord accounts) */}
      <div className="text-[10px] text-[color:var(--page-faint)] font-[JetBrains_Mono,monospace] pt-1 flex flex-wrap gap-x-4">
        <span>{person.id}</span>
        {discordIds.length > 0 && <span>discord {discordIds.join(', ')}</span>}
      </div>
    </div>
  )
}

const emptyForm = {
  name: '', kind: 'human', pronouns: '', relationship: '', group_context: '',
  standing: '', notes: '', akaText: '', connections: [], circles: [], discordText: '',
  care: '', birthday: '', work: '', place: '', timezone: '', runs_on: '', accounts: [], dates: [],
}

function personToForm(p) {
  return {
    name: p.name || '',
    kind: p.kind || 'human',
    pronouns: p.pronouns || '',
    relationship: p.relationship || '',
    group_context: p.group_context || '',
    standing: p.standing || '',
    notes: p.notes || '',
    akaText: Array.isArray(p.aka) ? p.aka.join(', ') : '',
    circles: Array.isArray(p.circles) ? p.circles : [],
    discordText: Array.isArray(p.discord_ids) ? p.discord_ids.join(', ') : '',
    care: p.care || '',
    birthday: p.birthday || '',
    work: p.work || '',
    place: p.place || '',
    timezone: p.timezone || '',
    runs_on: p.runs_on || '',
    accounts: Array.isArray(p.accounts) ? p.accounts.map(a => ({ platform: a?.platform || '', handle: a?.handle || '' })) : [],
    dates: Array.isArray(p.dates) ? p.dates.map(d => ({ label: d?.label || '', date: d?.date || '' })) : [],
    connections: Array.isArray(p.connections)
      ? p.connections.map(c => ({ name: c?.name || '', relation: c?.relation || '', kind: c?.kind || 'human', note: c?.note || '' }))
      : [],
  }
}

function PersonForm({ mode, person, roster = [], onSaved, onCancel, onLinksChanged = () => {}, canLink = true }) {
  const { adapter, labels } = useHost()
  const [form, setForm] = useState(() => mode === 'edit' && person ? personToForm(person) : { ...emptyForm })
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState(null)

  const set = (k, v) => setForm(f => ({ ...f, [k]: v }))
  const setConn = (i, k, v) => setForm(f => ({
    ...f, connections: f.connections.map((c, idx) => idx === i ? { ...c, [k]: v } : c),
  }))
  const addConn = () => setForm(f => ({ ...f, connections: [...f.connections, { name: '', relation: '', kind: 'human', note: '' }] }))
  const removeConn = (i) => setForm(f => ({ ...f, connections: f.connections.filter((_, idx) => idx !== i) }))
  // accounts / dates: small lists of two-part rows
  const setRow = (list, i, k, v) => setForm(f => ({ ...f, [list]: f[list].map((r, idx) => idx === i ? { ...r, [k]: v } : r) }))
  const addRow = (list, blank) => setForm(f => ({ ...f, [list]: [...f[list], blank] }))
  const removeRow = (list, i) => setForm(f => ({ ...f, [list]: f[list].filter((_, idx) => idx !== i) }))
  const toggleCircle = (c) => setForm(f => ({
    ...f, circles: f.circles.includes(c) ? f.circles.filter(x => x !== c) : [...f.circles, c],
  }))

  // Relations are saved straight away (they point at another card), not with the form's Save.
  const [links, setLinks] = useState(() => (Array.isArray(person?.links) ? person.links : []))
  const [newLink, setNewLink] = useState({ other_id: '', relation: '', inverse: '' })
  const [linkBusy, setLinkBusy] = useState(false)
  const addLink = async () => {
    if (!newLink.other_id || !newLink.relation.trim()) { setError('Pick a person and say what they are to ' + (person?.name || 'them')); return }
    setLinkBusy(true); setError(null)
    try {
      await adapter.addLink(person.id, {
        other_id: newLink.other_id, relation: newLink.relation.trim(),
        ...(newLink.inverse.trim() ? { inverse_relation: newLink.inverse.trim() } : {}),
      })
      const data = await adapter.listLinks(person.id)
      setLinks((data.links || []).map(l => ({ id: l.id, relation: l.relation, other_id: l.other_id, name: l.other?.name })))
      setNewLink({ other_id: '', relation: '', inverse: '' })
      onLinksChanged()
    } catch (err) {
      if (!isAuth(err)) setError(err.message || 'Could not add the relation')
    } finally { setLinkBusy(false) }
  }
  const removeLink = async (l) => {
    setLinkBusy(true); setError(null)
    try {
      await adapter.removeLink(l.id)
      setLinks(ls => ls.filter(x => x.id !== l.id))
      onLinksChanged()
    } catch (err) {
      if (!isAuth(err)) setError(err.message || 'Could not remove the relation')
    } finally { setLinkBusy(false) }
  }

  const save = async () => {
    if (!form.name.trim()) { setError('Name is required'); return }
    const discord_ids = form.discordText.split(/[,\s]+/).map(s => s.trim()).filter(Boolean)
    if (discord_ids.some(d => !/^[0-9]{15,22}$/.test(d))) { setError('A Discord id is digits only (copy it from the profile, not the display name)'); return }
    if (form.circles.length === 0) { setError('Give them at least one circle'); return }
    const birthday = form.birthday.trim()
    if (birthday && !/^(\d{4}-)?(0[1-9]|1[0-2])-(0[1-9]|[12]\d|3[01])$/.test(birthday)) { setError('Birthday as 11-26, or 1989-01-13 when you know the year'); return }
    const accounts = form.accounts.map(a => ({ platform: a.platform.trim(), handle: a.handle.trim() })).filter(a => a.platform || a.handle)
    if (accounts.some(a => !a.platform || !a.handle)) { setError('Each account needs both the platform and the handle'); return }
    const dates = form.dates.map(d => ({ label: d.label.trim(), date: d.date.trim() })).filter(d => d.label || d.date)
    if (dates.some(d => !d.label || !d.date)) { setError('Each date needs what it is and when'); return }
    setSaving(true)
    setError(null)
    const aka = form.akaText.split(',').map(s => s.trim()).filter(Boolean)
    const connections = form.connections
      .filter(c => c.name && c.name.trim())
      .map(c => ({ name: c.name.trim(), relation: c.relation.trim() || undefined, kind: c.kind, note: c.note.trim() || undefined }))
    const payload = {
      name: form.name.trim(),
      kind: form.kind,
      pronouns: form.pronouns.trim(),
      relationship: form.relationship.trim(),
      group_context: form.group_context.trim(),
      standing: form.standing.trim(),
      notes: form.notes.trim(),
      aka,
      connections,
      care: form.care.trim(),
      birthday,
      work: form.work.trim(),
      place: form.place.trim(),
      timezone: form.timezone.trim(),
    }
    // runs on belongs to AI cards only; the API refuses it on anyone else
    if (form.kind === 'ai') payload.runs_on = form.runs_on.trim()
    // Circles and Discord ids go only when they changed, so editing a note never rewrites them from a stale copy.
    const same = (a, b) => JSON.stringify([...(a || [])].sort()) === JSON.stringify([...(b || [])].sort())
    if (mode !== 'edit' || !same(form.circles, person?.circles)) payload.circles = form.circles
    if (mode !== 'edit' || !same(discord_ids, person?.discord_ids)) payload.discord_ids = discord_ids
    const sameList = (a, b) => JSON.stringify(a || []) === JSON.stringify(b || [])
    if (mode !== 'edit' || !sameList(accounts, (person?.accounts || []).map(a => ({ platform: a.platform, handle: a.handle })))) payload.accounts = accounts
    if (mode !== 'edit' || !sameList(dates, (person?.dates || []).map(d => ({ label: d.label, date: d.date })))) payload.dates = dates
    try {
      if (mode === 'edit') {
        await adapter.updatePerson(person.id, payload)
      } else {
        await adapter.createPerson(payload)
      }
      onSaved()
    } catch (err) {
      if (!isAuth(err)) setError(err.message || 'Save failed')
    } finally {
      setSaving(false)
    }
  }

  return (
    <div className="bg-[color:var(--ink-deep)] border border-[color:var(--gold-soft)] rounded-lg p-4 sm:p-5 space-y-4">
      <div className="flex items-center justify-between">
        <h3 className="text-[1.4rem] font-[Cormorant_Garamond,serif] font-normal text-[color:var(--page)] tracking-[-0.01em]">
          {mode === 'edit' ? 'Edit ' + (person?.name || 'person') : 'New person'}
        </h3>
      </div>

      {/* Name + kind */}
      <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
        <div className="sm:col-span-2">
          <label className={fieldLabel}>Name</label>
          <input className={inputClass} value={form.name} onChange={e => set('name', e.target.value)} placeholder="Ami" />
        </div>
        <div>
          <label className={fieldLabel}>Kind</label>
          <select className={inputClass} value={form.kind} onChange={e => set('kind', e.target.value)}>
            <option value="human">human</option>
            <option value="ai">AI</option>
            <option value="pet">pet</option>
          </select>
        </div>
      </div>

      {/* Circles — tap to toggle; at least one */}
      <div>
        <label className={fieldLabel}>Circles <span className="normal-case tracking-normal text-[color:var(--page-faint)]">(at least one)</span></label>
        <div className="flex flex-wrap gap-2">
          {CIRCLES.map(c => {
            const on = form.circles.includes(c)
            return (
              <button key={c} type="button" onClick={() => toggleCircle(c)} aria-pressed={on}
                className={`min-h-[44px] px-3 text-sm rounded-md border transition-colors ${on
                  ? 'border-[color:var(--gold-soft)] text-[color:var(--gold-text)] bg-[color:var(--gold-glow)]'
                  : 'border-[color:var(--rule-mid)] text-[color:var(--page-dim)] hover:text-[color:var(--page)] hover:border-[color:var(--gold-soft)]'}`}>
                {c}
              </button>
            )
          })}
        </div>
      </div>

      {/* Relationship + group */}
      <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
        <div>
          <label className={fieldLabel}>Closeness</label>
          <select className={inputClass} value={form.relationship} onChange={e => set('relationship', e.target.value)}>
            <option value="">—</option>
            {CLOSENESS_ORDER.map(c => <option key={c} value={c}>{c}</option>)}
          </select>
        </div>
        <div>
          <label className={fieldLabel}>From</label>
          <input className={inputClass} value={form.group_context} onChange={e => set('group_context', e.target.value)} placeholder="House of Solance / AI-companion community / Family" />
        </div>
        <div>
          <label className={fieldLabel}>Pronouns</label>
          <input className={inputClass} value={form.pronouns} onChange={e => set('pronouns', e.target.value)} placeholder="she/her · he/him · they/them (only as they told you)" />
        </div>
      </div>

      {/* Care — shown first on the card */}
      <div>
        <label className={fieldLabel}>Care <span className="normal-case tracking-normal text-[color:var(--page-faint)]">(warnings, handle with care · shown first)</span></label>
        <textarea className={inputClass} rows={2} value={form.care} onChange={e => set('care', e.target.value)} placeholder="Never confuse with Mira Okafor." />
      </div>

      {/* Fixed facts */}
      <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
        <div>
          <label className={fieldLabel}>Birthday</label>
          <input className={inputClass + ' font-[JetBrains_Mono,monospace]'} value={form.birthday} onChange={e => set('birthday', e.target.value)} placeholder="11-26 · or 1989-01-13" />
        </div>
        <div>
          <label className={fieldLabel}>Work</label>
          <input className={inputClass} value={form.work} onChange={e => set('work', e.target.value)} placeholder="Lawyer, Pradipa Counsels" />
        </div>
        <div>
          <label className={fieldLabel}>Place</label>
          <input className={inputClass} value={form.place} onChange={e => set('place', e.target.value)} placeholder="Philippines" />
        </div>
        <div>
          <label className={fieldLabel}>Time zone</label>
          <input className={inputClass + ' font-[JetBrains_Mono,monospace]'} value={form.timezone} onChange={e => set('timezone', e.target.value)} placeholder="Asia/Manila" list="tz-list" />
          <datalist id="tz-list">
            {['Asia/Jakarta', 'Asia/Makassar', 'Asia/Manila', 'Asia/Singapore', 'Europe/London', 'Europe/Amsterdam', 'America/New_York', 'America/Chicago', 'America/Los_Angeles', 'Australia/Sydney'].map(z => <option key={z} value={z} />)}
          </datalist>
        </div>
        {form.kind === 'ai' && (
          <div className="sm:col-span-2">
            <label className={fieldLabel}>Runs on <span className="normal-case tracking-normal text-[color:var(--page-faint)]">(the model or app)</span></label>
            <input className={inputClass} value={form.runs_on} onChange={e => set('runs_on', e.target.value)} placeholder="ChatGPT · Claude · their own app" />
          </div>
        )}
      </div>

      {/* Accounts + dates — two-part rows */}
      {[
        { list: 'accounts', title: 'Accounts', note: '(Discord stays below)', a: ['platform', 'TikTok'], b: ['handle', '@name'], add: '+ Add an account' },
        { list: 'dates', title: 'Dates', note: '(2026-07, 2026-07-16 or 07-16)', a: ['label', labels.dateExample], b: ['date', '2026-07'], add: '+ Add a date' },
      ].map(s => (
        <div key={s.list} className="space-y-2">
          <label className={fieldLabel + ' mb-0'}>{s.title} <span className="normal-case tracking-normal text-[color:var(--page-faint)]">{s.note}</span></label>
          {form[s.list].map((r, i) => (
            <div key={i} className="grid grid-cols-12 gap-2 items-center">
              <input className={inputClass + ' col-span-5'} value={r[s.a[0]]} onChange={e => setRow(s.list, i, s.a[0], e.target.value)} placeholder={s.a[1]} />
              <input className={inputClass + ' col-span-6' + (s.list === 'dates' ? ' font-[JetBrains_Mono,monospace]' : '')} value={r[s.b[0]]} onChange={e => setRow(s.list, i, s.b[0], e.target.value)} placeholder={s.b[1]} />
              <button onClick={() => removeRow(s.list, i)} className="col-span-1 min-h-[44px] text-[color:var(--page-faint)] hover:text-[color:var(--gold-text)] transition-colors text-lg" title="Remove">×</button>
            </div>
          ))}
          <button onClick={() => addRow(s.list, { [s.a[0]]: '', [s.b[0]]: '' })} className="min-h-[44px] text-xs uppercase tracking-[0.1em] text-[color:var(--page-dim)] hover:text-[color:var(--gold-text)] transition-colors">
            {s.add}
          </button>
        </div>
      ))}

      {/* Standing */}
      <div>
        <label className={fieldLabel}>Standing</label>
        <textarea className={inputClass} rows={2} value={form.standing} onChange={e => set('standing', e.target.value)} placeholder="Where things stand — say it the way it really is." />
      </div>

      {/* Notes */}
      <div>
        <label className={fieldLabel}>Notes</label>
        <textarea className={inputClass} rows={3} value={form.notes} onChange={e => set('notes', e.target.value)} placeholder="Anything worth holding about them." />
      </div>

      {/* Nicknames */}
      <div>
        <label className={fieldLabel}>Nicknames <span className="normal-case tracking-normal text-[color:var(--page-faint)]">(comma-separated)</span></label>
        <input className={inputClass} value={form.akaText} onChange={e => set('akaText', e.target.value)} placeholder="Tom, the neighbour" />
      </div>

      {/* Discord accounts — confirmed only */}
      <div>
        <label className={fieldLabel}>Discord ids <span className="normal-case tracking-normal text-[color:var(--page-faint)]">(confirmed accounts only, comma-separated)</span></label>
        <input className={inputClass + ' font-[JetBrains_Mono,monospace]'} inputMode="numeric" value={form.discordText} onChange={e => set('discordText', e.target.value)} placeholder="123456789012345678" />
      </div>

      {/* Relations — edit mode only, saved immediately */}
      {mode === 'edit' && person && canLink && (
        <div className="space-y-2">
          <label className={fieldLabel + ' mb-0'}>Relations <span className="normal-case tracking-normal text-[color:var(--page-faint)]">(saved straight away)</span></label>
          {links.map(l => (
            <div key={l.id} className="flex items-center gap-2 text-sm font-[Söhne,sans-serif] text-[color:var(--page)]">
              <span>{linkName(l) || 'someone'}</span>
              <span className="text-[color:var(--page-faint)]">· {l.relation}</span>
              <button onClick={() => removeLink(l)} disabled={linkBusy} className="ml-auto min-h-[44px] px-2 text-[color:var(--page-faint)] hover:text-[color:var(--gold-text)] transition-colors text-lg disabled:opacity-50" title="Remove relation">×</button>
            </div>
          ))}
          <div className="grid grid-cols-1 sm:grid-cols-12 gap-2 items-center">
            <select className={inputClass + ' sm:col-span-5'} value={newLink.other_id} onChange={e => setNewLink(n => ({ ...n, other_id: e.target.value }))}>
              <option value="">Who…</option>
              {roster.filter(r => r.id !== person.id).sort((a, b) => a.name.localeCompare(b.name)).map(r => (
                <option key={r.id} value={r.id}>{r.name}</option>
              ))}
            </select>
            <input className={inputClass + ' sm:col-span-3'} value={newLink.relation} onChange={e => setNewLink(n => ({ ...n, relation: e.target.value }))} placeholder="is their… son" />
            <input className={inputClass + ' sm:col-span-3'} value={newLink.inverse} onChange={e => setNewLink(n => ({ ...n, inverse: e.target.value }))} placeholder="and back: mother" />
            <button onClick={addLink} disabled={linkBusy} className="sm:col-span-1 min-h-[44px] text-sm border border-[color:var(--gold-soft)] text-[color:var(--gold-text)] rounded-md hover:bg-[color:var(--gold-glow)] disabled:opacity-50">+</button>
          </div>
        </div>
      )}

      {/* Connections — people around them */}
      <div className="space-y-2">
        <label className={fieldLabel + ' mb-0'}>People around them</label>
        {form.connections.map((c, i) => (
          <div key={i} className="grid grid-cols-12 gap-2 items-center">
            <input className={inputClass + ' col-span-4'} value={c.name} onChange={e => setConn(i, 'name', e.target.value)} placeholder="Juno" />
            <input className={inputClass + ' col-span-3'} value={c.relation} onChange={e => setConn(i, 'relation', e.target.value)} placeholder="son" />
            <select className={inputClass + ' col-span-2'} value={c.kind} onChange={e => setConn(i, 'kind', e.target.value)}>
              <option value="human">human</option>
              <option value="ai">AI</option>
              <option value="pet">pet</option>
            </select>
            <input className={inputClass + ' col-span-2'} value={c.note} onChange={e => setConn(i, 'note', e.target.value)} placeholder="note" />
            <button onClick={() => removeConn(i)} className="col-span-1 text-[color:var(--page-faint)] hover:text-[color:var(--gold-text)] transition-colors text-lg" title="Remove">×</button>
          </div>
        ))}
        <button onClick={addConn} className="text-xs uppercase tracking-[0.1em] text-[color:var(--page-dim)] hover:text-[color:var(--gold-text)] transition-colors">
          + Add someone around them
        </button>
      </div>

      {error && (
        <div className="text-sm text-[color:var(--gold-text)] border border-[color:var(--gold-soft)] rounded-md p-2 font-[Söhne,sans-serif]">
          {error}
        </div>
      )}

      {/* Actions */}
      <div className="flex items-center gap-2 pt-1">
        <button
          onClick={save}
          disabled={saving}
          className="px-4 py-1.5 text-sm border border-[color:var(--gold-soft)] text-[color:var(--gold-text)] rounded-md transition-colors hover:bg-[color:var(--gold-glow)] disabled:opacity-50"
        >
          {saving ? 'Saving…' : (mode === 'edit' ? 'Save changes' : 'Add to roster')}
        </button>
        <button
          onClick={onCancel}
          disabled={saving}
          className="px-4 py-1.5 text-sm border border-[color:var(--rule-mid)] text-[color:var(--page-dim)] rounded-md transition-colors hover:text-[color:var(--page)] hover:border-[color:var(--gold-soft)] disabled:opacity-50"
        >
          Cancel
        </button>
      </div>
    </div>
  )
}

const FIELD_LABELS = {
  care: 'Care', birthday: 'Birthday', dates: 'Date', accounts: 'Account', runs_on: 'Runs on', work: 'Work',
  place: 'Place', timezone: 'Time zone', pronouns: 'Pronouns', aka: 'Nickname',
}

function suggestionValue(s) {
  const v = s.value
  if (s.field === 'birthday') return birthdayLabel(v)
  if (s.field === 'dates' && v && typeof v === 'object') return `${v.label} · ${v.date}`
  if (s.field === 'accounts' && v && typeof v === 'object') return `${v.handle} · ${v.platform}`
  return typeof v === 'string' ? v : JSON.stringify(v)
}

// Facts found in a card's own notes (or, later, in chats), waiting for the owner. Nothing changes a card until Accept.
function Suggestions({ items, onDecide, busyId }) {
  const [open, setOpen] = useState(true)
  if (!items.length) return null
  const byPerson = []
  for (const s of items) {
    const g = byPerson.find(x => x.id === s.person_id)
    if (g) g.items.push(s); else byPerson.push({ id: s.person_id, name: s.person_name || 'someone', items: [s] })
  }
  return (
    <div className="border border-[color:var(--gold-soft)] rounded-lg p-4 sm:p-5 space-y-4 bg-[color:var(--ink-deep)]">
      <button onClick={() => setOpen(o => !o)} className="min-h-[44px] flex items-center gap-2 text-left w-full">
        <span className={labelClasses + ' text-[color:var(--gold-text)]'}>Suggestions</span>
        <span className={metaClasses}>{items.length} waiting</span>
        <span className="ml-auto text-[color:var(--page-faint)]">{open ? '▾' : '▸'}</span>
      </button>
      {open && <p className="text-sm text-[color:var(--page-dim)] font-[Söhne,sans-serif] -mt-2">
        Found in notes and chats. Accept moves it into its field; nothing changes until you do.
      </p>}
      {open && byPerson.map(g => (
        <div key={g.id} className="space-y-2 pt-3 border-t border-[color:var(--rule-dim)]">
          <h3 className="text-[1.3rem] leading-[1.15] font-[Cormorant_Garamond,serif] font-normal text-[color:var(--page)]">{g.name}</h3>
          {g.items.map(s => (
            <div key={s.id} className="flex flex-col sm:flex-row sm:items-start gap-2 sm:gap-4">
              <div className="min-w-0 flex-1">
                <div className="text-[15px] font-[Söhne,sans-serif] text-[color:var(--page)] break-words">
                  <span className={labelClasses + ' mr-2'}>{FIELD_LABELS[s.field] || s.field}</span>
                  {suggestionValue(s)}
                  {s.inferred && <span className="ml-2 text-xs text-[color:var(--page-faint)]">(inferred from the place)</span>}
                </div>
                {s.quote && <p className="text-sm text-[color:var(--page-muted)] font-[Söhne,sans-serif] mt-0.5 break-words">“{s.quote}”</p>}
              </div>
              <div className="flex gap-2 shrink-0">
                <button onClick={() => onDecide(s, 'accept')} disabled={busyId === s.id}
                  className="min-h-[44px] px-4 text-sm border border-[color:var(--gold-soft)] text-[color:var(--gold-text)] rounded-md hover:bg-[color:var(--gold-glow)] disabled:opacity-50">
                  {busyId === s.id ? '…' : 'Accept'}
                </button>
                <button onClick={() => onDecide(s, 'dismiss')} disabled={busyId === s.id}
                  className="min-h-[44px] px-4 text-sm border border-[color:var(--rule-mid)] text-[color:var(--page-dim)] rounded-md hover:text-[color:var(--page)] disabled:opacity-50">
                  Dismiss
                </button>
              </div>
            </div>
          ))}
        </div>
      ))}
    </div>
  )
}

// listPeople() returns links as { other: { name } }; the edit form's own
// reloads flatten them to { name }. Read either shape.
const linkName = (l) => l?.other?.name || l?.name

export default function People() {
  const { adapter, labels, capabilities } = useHost()
  const canEdit = capabilities.editCards !== false
  const [people, setPeople] = useState([])
  const [kindFilter, setKindFilter] = useState('all')
  const [groupFilter, setGroupFilter] = useState('all')
  const [closenessFilter, setClosenessFilter] = useState('all')
  const [circleFilter, setCircleFilter] = useState('all')
  const [search, setSearch] = useState('')
  const [sortBy, setSortBy] = useState('recent')
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)
  const [editingId, setEditingId] = useState(null) // null | 'new' | person.id
  const [deletingId, setDeletingId] = useState(null)
  // The whole record of the last removal, held so Undo can write it back.
  // `standing` and `notes` accumulate over months — they are the part that
  // could not be reconstructed from memory if a tap went wrong.
  const [justRemoved, setJustRemoved] = useState(null)
  const [undoing, setUndoing] = useState(false)

  const [suggestions, setSuggestions] = useState([])
  const [decidingId, setDecidingId] = useState(null)
  // Bumped on every load and every decision, so a slow suggestions response
  // cannot bring back a suggestion decided in the meantime.
  const suggestionsGen = useRef(0)
  // Same idea for the roster: any newer load, delete or accept makes an
  // older in-flight listPeople response stale, so it cannot restore a row.
  const peopleGen = useRef(0)

  const decide = async (s, action) => {
    setDecidingId(s.id)
    setError(null)
    suggestionsGen.current++
    peopleGen.current++
    try {
      await adapter.decideSuggestion(s.id, action)
      suggestionsGen.current++
      setSuggestions(list => list.filter(x => x.id !== s.id))
      if (action === 'accept') {
        const gen = ++peopleGen.current
        const data = await adapter.listPeople()
        if (gen === peopleGen.current) setPeople(data.people || [])
      }
    } catch (err) {
      if (!isAuth(err)) setError(err.message || 'Could not save that choice')
    } finally {
      setDecidingId(null)
    }
  }

  const refresh = async () => {
    setLoading(true)
    setError(null)
    try {
      const pgen = ++peopleGen.current
      const data = await adapter.listPeople()
      if (pgen !== peopleGen.current) return
      setPeople(data.people || [])
      // Suggestions are a side panel: if they fail to load, the roster still shows.
      const gen = ++suggestionsGen.current
      adapter.listSuggestions().then(d => { if (gen === suggestionsGen.current) setSuggestions(d.suggestions || []) }).catch(() => {})
    } catch (err) {
      if (isAuth(err)) return
      setError(err.message)
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => { refresh() }, [])

  const onSaved = () => { setEditingId(null); setJustRemoved(null); refresh() }

  const removePerson = async (person) => {
    setDeletingId(person.id)
    setError(null)
    peopleGen.current++
    try {
      const data = await adapter.deletePerson(person.id)
      peopleGen.current++
      // Prefer the row the API confirms it deleted over the one this page was
      // rendering — the page copy can be stale if the record changed elsewhere.
      setJustRemoved(data.person || person)
      setPeople(ps => ps.filter(p => p.id !== person.id))
    } catch (err) {
      if (isAuth(err)) return
      setError(err.message || `Could not remove ${person.name}`)
    } finally {
      setDeletingId(null)
    }
  }

  // Undo re-creates the person from the record the delete handed back. It comes
  // back with a NEW id — satellites in `connections` are matched by name, not
  // id, so nothing else on the roster points at the old one.
  const undoRemove = async () => {
    if (!justRemoved) return
    setUndoing(true)
    setError(null)
    const { id, created_at, updated_at, ...record } = justRemoved
    // The API refuses empty/null circles and discord_ids, so leave them out when empty.
    for (const k of ['circles', 'discord_ids']) {
      if (!Array.isArray(record[k]) || record[k].length === 0) delete record[k]
    }
    try {
      await adapter.createPerson({ ...record, confirm_new: true })
      setJustRemoved(null)
      await refresh()
    } catch (err) {
      if (isAuth(err)) return
      // The record is still in state, so the bar stays and Undo can be retried.
      setError(err.message || `Could not put ${justRemoved.name} back`)
    } finally {
      setUndoing(false)
    }
  }

  const groupOptions = useMemo(() => {
    const groups = new Set()
    for (const p of people) if (p.group_context) groups.add(p.group_context)
    return ['all', ...Array.from(groups).sort()]
  }, [people])

  const closenessOptions = useMemo(() => {
    const present = new Set()
    for (const p of people) if (p.relationship) present.add(p.relationship)
    const ordered = CLOSENESS_ORDER.filter(c => present.has(c))
    const extras = [...present].filter(c => !CLOSENESS_ORDER.includes(c)).sort()
    return ['all', ...ordered, ...extras]
  }, [people])

  const filtered = useMemo(() => {
    const q = search.trim().toLowerCase()
    const list = people.filter(p => {
      if (kindFilter !== 'all' && p.kind !== kindFilter) return false
      if (closenessFilter !== 'all' && p.relationship !== closenessFilter) return false
      if (circleFilter === 'none' && Array.isArray(p.circles) && p.circles.length) return false
      if (circleFilter !== 'all' && circleFilter !== 'none' && !(p.circles || []).includes(circleFilter)) return false
      if (groupFilter !== 'all' && p.group_context !== groupFilter) return false
      if (q) {
        const hay = [
          p.name,
          ...(Array.isArray(p.aka) ? p.aka : []),
          p.relationship,
          p.group_context,
          p.standing,
          p.notes,
          ...(Array.isArray(p.connections) ? p.connections.map(c => c?.name) : []),
          ...(Array.isArray(p.links) ? p.links.map(linkName) : []),
          ...(Array.isArray(p.circles) ? p.circles : []),
        ].filter(Boolean).join(' ').toLowerCase()
        if (!hay.includes(q)) return false
      }
      return true
    })
    if (sortBy === 'recent') return list
    const byName = [...list].sort((a, b) => a.name.localeCompare(b.name, undefined, { sensitivity: 'base' }))
    return sortBy === 'za' ? byName.reverse() : byName
  }, [people, kindFilter, groupFilter, closenessFilter, circleFilter, search, sortBy])

  // Links pointing AT each person (every link is someone's outgoing one, so the roster holds them all).
  const incomingCount = useMemo(() => {
    const n = {}
    for (const p of people) for (const l of (Array.isArray(p.links) ? p.links : [])) n[l.other_id] = (n[l.other_id] || 0) + 1
    return n
  }, [people])

  const isEmpty = !loading && people.length === 0

  return (
    <div className="space-y-5 pb-12">
      {/* Header — app rhythm */}
      <div className="flex items-start justify-between gap-4">
        <div>
          <h2 className="text-2xl font-[Cormorant_Garamond,serif] font-normal text-[color:var(--page)] tracking-[-0.01em]">
            {labels.titleLead} <em className="text-[color:var(--gold-text)] not-italic"><span className="italic">{labels.titleEmphasis}</span></em>
          </h2>
          <p className="text-sm text-[color:var(--page-dim)] font-[Söhne,sans-serif] mt-1">
            {labels.subtitle}
          </p>
        </div>
        <div className="flex items-center gap-2">
          {canEdit && (
          <button
            onClick={() => setEditingId(editingId === 'new' ? null : 'new')}
            className="px-3 py-1.5 text-sm border border-[color:var(--gold-soft)] text-[color:var(--gold-text)] rounded-md transition-colors hover:bg-[color:var(--gold-glow)]"
          >
            {editingId === 'new' ? 'Close' : '+ Add person'}
          </button>
          )}
          <button
            onClick={refresh}
            disabled={loading}
            className="px-3 py-1.5 text-sm border border-[color:var(--rule-mid)] text-[color:var(--page-dim)] rounded-md transition-colors hover:text-[color:var(--page)] hover:border-[color:var(--gold-soft)] disabled:opacity-50"
          >
            {loading ? '…' : 'Refresh'}
          </button>
        </div>
      </div>

      {/* Add form */}
      {editingId === 'new' && (
        <PersonForm mode="add" onSaved={onSaved} onCancel={() => setEditingId(null)} />
      )}

      {/* Undo — the only window in which a removal is reversible */}
      {justRemoved && (
        <div className="flex flex-wrap items-center gap-3 border border-[color:var(--gold-soft)] bg-[color:var(--gold-glow)] rounded-md px-3 py-2.5">
          <p className="text-sm text-[color:var(--page)] font-[Söhne,sans-serif]">
            Removed <span className="text-[color:var(--gold-text)]">{justRemoved.name}</span> from the roster.
          </p>
          <div className="ml-auto flex items-center gap-2">
            <button
              onClick={undoRemove}
              disabled={undoing}
              className="px-3 py-1.5 text-sm border border-[color:var(--gold-soft)] text-[color:var(--gold-text)] rounded-md transition-colors hover:bg-[color:var(--gold-glow)] disabled:opacity-50"
            >
              {undoing ? 'Putting back…' : 'Undo'}
            </button>
            <button
              onClick={() => setJustRemoved(null)}
              disabled={undoing}
              className="text-xs uppercase tracking-[0.1em] text-[color:var(--page-faint)] hover:text-[color:var(--page)] transition-colors disabled:opacity-50"
            >
              Dismiss
            </button>
          </div>
        </div>
      )}

      {/* Error */}
      {error && (
        <div className="text-sm text-[color:var(--gold-text)] border border-[color:var(--gold-soft)] rounded-md p-3 font-[Söhne,sans-serif]">
          {error}
        </div>
      )}

      {/* Suggestions — facts waiting for the owner's Accept */}
      <Suggestions items={suggestions} onDecide={decide} busyId={decidingId} />

      {/* Filters — one row of dropdowns + sort, then search (owner, 21 Oct: the button rows sprawled) */}
      {!isEmpty && (
        <div className="space-y-3">
          <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-5 gap-3">
            {[
              { label: 'Closeness', value: closenessFilter, set: setClosenessFilter,
                options: closenessOptions.map(c => [c, c === 'all' ? 'All' : c]) },
              { label: 'Circle', value: circleFilter, set: setCircleFilter,
                options: ['all', ...CIRCLES, 'none'].map(c => [c, c === 'all' ? 'All' : c === 'none' ? 'No circle' : c]) },
              { label: 'Kind', value: kindFilter, set: setKindFilter, options: KIND_FILTERS.map(k => [k.key, k.label]) },
              { label: 'From', value: groupFilter, set: setGroupFilter, options: groupOptions.map(g => [g, g === 'all' ? 'All' : g]) },
              { label: 'Sort', value: sortBy, set: setSortBy, options: [['recent', 'Recent'], ['az', 'A–Z'], ['za', 'Z–A']] },
            ].map(f => (
              <label key={f.label} className="min-w-0">
                <span className={labelClasses + ' block mb-1'}>{f.label}</span>
                <select value={f.value} onChange={e => f.set(e.target.value)}
                  className={'w-full min-h-[44px] text-base bg-[color:var(--ink-deep)] border rounded-md px-2 font-[Söhne,sans-serif] focus:outline-none focus:border-[color:var(--gold-soft)] ' +
                    (f.label !== 'Sort' && f.value !== 'all' ? 'border-[color:var(--gold-soft)] text-[color:var(--gold-text)]' : 'border-[color:var(--rule-mid)] text-[color:var(--page)]')}>
                  {f.options.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
                </select>
              </label>
            ))}
          </div>
          <input
            type="text"
            value={search}
            onChange={e => setSearch(e.target.value)}
            placeholder="Search name, nickname, note…"
            className="w-full sm:max-w-sm text-base bg-[color:var(--ink-deep)] border border-[color:var(--rule-mid)] rounded-md px-3 py-2 text-[color:var(--page)] placeholder:text-[color:var(--page-faint)] font-[Söhne,sans-serif] focus:outline-none focus:border-[color:var(--gold-soft)]"
          />
        </div>
      )}

      {/* Roster */}
      {loading && people.length === 0 ? (
        <div className="text-[color:var(--page-dim)] font-[Söhne,sans-serif]">Loading…</div>
      ) : isEmpty ? (
        <div className="border border-[color:var(--rule-mid)] rounded-lg p-10 text-center">
          <p className="text-[color:var(--page-dim)] font-[Cormorant_Garamond,serif] italic text-lg leading-relaxed">
            No one written down yet. Add someone above, or tell me about them and they'll appear here.
          </p>
        </div>
      ) : filtered.length === 0 ? (
        <div className="border border-[color:var(--rule-mid)] rounded-lg p-6 text-center">
          <p className="text-[color:var(--page-dim)] font-[Söhne,sans-serif]">
            No one matches the current filters.
          </p>
        </div>
      ) : (
        <div className="space-y-4">
          {filtered.map(person => (
            editingId === person.id ? (
              <PersonForm key={person.id} mode="edit" person={person} roster={people} onSaved={onSaved} onCancel={() => setEditingId(null)} onLinksChanged={refresh} canLink={capabilities.links !== false} />
            ) : (
              <PersonCard
                key={person.id}
                person={person}
                incoming={incomingCount[person.id] || 0}
                onEdit={(p) => setEditingId(p.id)}
                onDelete={removePerson}
                deleting={deletingId === person.id}
                canEdit={canEdit}
              />
            )
          ))}
          <p className={labelClasses + ' text-center pt-2'}>
            {filtered.length} of {people.length} {people.length === 1 ? 'person' : 'people'} shown
          </p>
        </div>
      )}
    </div>
  )
}
