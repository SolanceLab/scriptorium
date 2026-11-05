// Scriptorium — Copyright (c) 2026 House of Solance. PolyForm Noncommercial 1.0.0. Required Notice: Copyright (c) 2026 House of Solance (https://github.com/SolanceLab)
import { setHost } from './host.js'

export default {
  id: 'people',
  label: 'People',
  description: 'The people in your world: cards, relations, and facts found in chats.',
  nav: { items: [{ path: 'people', label: 'People' }] },
  routes: [
    { path: 'people', load: () => import('./pages/People.jsx') },
  ],
  defaultEnabled: true,
  // The host calls this once with { adapter, labels } before rendering the page.
  setup(host) {
    return setHost(host)
  },
}
