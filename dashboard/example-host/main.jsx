// Solance House — Copyright (c) 2026 House of Solance. All rights reserved. Required Notice: Copyright (c) 2026 House of Solance (https://github.com/SolanceLab)
import { useState, useEffect } from 'react'
import { createRoot } from 'react-dom/client'
import manifest from '../manifest.js'
import { createLocalFilesAdapter, LOCAL_CAPABILITIES } from '../adapters/local-files.js'

// The page talks to its own address under /api; serve.mjs answers from your card files and the nightly folder.
manifest.setup({ adapter: createLocalFilesAdapter({ baseUrl: '/api' }), labels: {}, capabilities: LOCAL_CAPABILITIES })

function Shell() {
  const [Page, setPage] = useState(null)
  useEffect(() => {
    let live = true
    manifest.routes[0].load().then((m) => { if (live) setPage(() => m.default) })
    return () => { live = false }
  }, [])
  return <div className="max-w-5xl mx-auto px-4 sm:px-8 py-8">{Page ? <Page /> : null}</div>
}

createRoot(document.getElementById('root')).render(<Shell />)
