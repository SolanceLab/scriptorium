// Scriptorium — Copyright (c) 2026 House of Solance. PolyForm Noncommercial 1.0.0. Required Notice: Copyright (c) 2026 House of Solance (https://github.com/SolanceLab)
// The host contract for the People page. A host calls setHost({ adapter, labels })
// once (the manifest's setup(host) does it) before the page renders. See HOST.md.

export const DEFAULT_LABELS = {
  titleLead: 'Your',
  titleEmphasis: 'people',
  subtitle: "Who's in your world, and who's around them.",
  dateExample: 'met them',
}

export const DEFAULT_CAPABILITIES = { editCards: true, links: true }

/**
 * @typedef {Object} PeopleAdapter  every method returns a Promise; see HOST.md for shapes
 * @property {() => Promise<{people: Object[]}>} listPeople
 * @property {(payload: Object) => Promise<any>} createPerson
 * @property {(id: string, patch: Object) => Promise<any>} updatePerson
 * @property {(id: string) => Promise<{person: Object}>} deletePerson
 * @property {(id: string) => Promise<{links: Object[]}>} listLinks
 * @property {(id: string, link: {other_id: string, relation: string, inverse_relation?: string}) => Promise<any>} addLink
 * @property {(linkId: string) => Promise<any>} removeLink
 * @property {() => Promise<{suggestions: Object[]}>} listSuggestions
 * @property {(id: string, decision: 'accept'|'dismiss') => Promise<any>} decideSuggestion
 * @property {(personId: string) => Promise<{facts: Object[]}>} listFacts
 * @property {(id: string, text: string) => Promise<any>} editFact
 * @property {(id: string) => Promise<any>} removeFact
 */

const METHODS = ['listPeople', 'createPerson', 'updatePerson', 'deletePerson', 'listLinks', 'addLink', 'removeLink',
  'listSuggestions', 'decideSuggestion', 'listFacts', 'editFact', 'removeFact']

let current = null

/** @param {{adapter: PeopleAdapter, labels?: Object, capabilities?: {editCards?: boolean, links?: boolean}}} host */
export function setHost(host) {
  if (!host || typeof host !== 'object' || !host.adapter) {
    throw new Error('setHost needs { adapter, labels? } — the People module has no connection without an adapter.')
  }
  const missing = METHODS.filter((m) => typeof host.adapter[m] !== 'function')
  if (missing.length) throw new Error('People adapter is missing: ' + missing.join(', '))
  // capabilities is optional; a host that omits it (or a key) gets everything on.
  current = { ...host, labels: { ...DEFAULT_LABELS, ...(host.labels || {}) }, capabilities: { ...DEFAULT_CAPABILITIES, ...(host.capabilities || {}) } }
  return current
}

/** React hook: the host the module was given. Throws if setHost was never called. */
export function useHost() {
  if (!current) {
    throw new Error('People module: no host set. Call manifest.setup(host) or setHost({ adapter, labels }) before rendering the page.')
  }
  return current
}

/** For tests: forget the current host. */
export function resetHost() {
  current = null
}
