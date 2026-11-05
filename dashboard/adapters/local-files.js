// Solance House — Copyright (c) 2026 House of Solance. All rights reserved. Required Notice: Copyright (c) 2026 House of Solance (https://github.com/SolanceLab)
// Browser adapter for the People page over your own files: it calls the JSON API that
// example-host/serve.mjs serves next to the page. Same method names as HOST.md.
// Cards are edited in their files in this version, so create/update/delete and links say so.

const NO_EDIT = 'Editing cards happens in your card files in this version'

export const LOCAL_CAPABILITIES = { editCards: false, links: false }

export function createLocalFilesAdapter({ baseUrl = '/api', fetchImpl } = {}) {
  const f = (...a) => (fetchImpl || globalThis.fetch)(...a)
  async function call(path, init) {
    const res = await f(baseUrl + path, init)
    let data = null
    try { data = await res.json() } catch { /* no body */ }
    if (!res.ok) throw new Error((data && data.error) || 'Request failed (' + res.status + ')')
    return data
  }
  const send = (method, path, body) => call(path, {
    method, headers: { 'content-type': 'application/json' }, body: body === undefined ? undefined : JSON.stringify(body),
  })
  const refuse = async () => { throw new Error(NO_EDIT) }
  const splitId = (id) => { const i = String(id).lastIndexOf(':'); return [String(id).slice(0, i), String(id).slice(i + 1)] }

  // Each fact's fingerprint, kept from the last listing, so a change names the fact it meant even if numbering shifted.
  const revs = new Map()
  const rev = (id) => (revs.has(id) ? { rev: revs.get(id) } : {})

  return {
    listPeople: () => call('/people'),
    createPerson: refuse,
    updatePerson: refuse,
    deletePerson: refuse,
    listLinks: async () => ({ links: [] }),
    addLink: refuse,
    removeLink: refuse,
    listSuggestions: () => call('/suggestions'),
    decideSuggestion: (id, decision) => send('POST', '/suggestions/' + encodeURIComponent(id) + '/' + (decision === 'accept' ? 'accept' : 'dismiss')),
    listFacts: async (personId) => {
      const d = await call('/facts?person=' + encodeURIComponent(personId))
      for (const k of [...revs.keys()]) if (k.startsWith(personId + ':')) revs.delete(k)
      for (const x of (d && d.facts) || []) if (x && x.id && x.rev) revs.set(x.id, x.rev)
      return d
    },
    editFact: (id, text) => { const [k, n] = splitId(id); return send('PATCH', '/facts/' + encodeURIComponent(k) + '/' + encodeURIComponent(n), { fact: text, ...rev(id) }) },
    removeFact: (id) => { const [k, n] = splitId(id); return send('PATCH', '/facts/' + encodeURIComponent(k) + '/' + encodeURIComponent(n), { remove: true, ...rev(id) }) },
  }
}
