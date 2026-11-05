# People page: host contract

The People page is a dashboard module of the Scriptorium room. The host supplies login, look and the connection.

```js
import manifest from '<module>/dashboard/manifest.js'
manifest.setup({ adapter, labels, capabilities })   // once, before the page renders
```

`setup` calls `setHost` (host.js), which checks every adapter method exists. `useHost()` throws a clear error when no host was set. The page imports nothing from the host app.

## adapter (required)

Every method returns a Promise.

| Method | Returns |
|---|---|
| `listPeople()` | `{ people: [...] }`, every person with their `links` included (the House sends `limit=500&include_links=1`) |
| `createPerson(payload)` | the created person. The same payload the form sends (name, kind, notes, circles, discord_ids, accounts, dates, ...). Undo of a delete sends the removed record plus `confirm_new: true` |
| `updatePerson(id, patch)` | the updated person. The form sends circles, discord_ids, accounts and dates only when they changed |
| `deletePerson(id)` | `{ person }`, the row that was deleted (the page keeps it for Undo) |
| `listLinks(id)` | `{ links: [{ id, relation, other_id, other?: { name } }] }` |
| `addLink(id, { other_id, relation, inverse_relation? })` | the created link |
| `removeLink(linkId)` | anything |
| `listSuggestions()` | `{ suggestions: [...] }`, pending ones only. If this fails the roster still shows |
| `decideSuggestion(id, 'accept' \| 'dismiss')` | anything. After `accept` the page calls `listPeople()` again |
| `listFacts(personId)` | `{ facts: [{ id, fact, said_on, source?, quote?, status? }] }` |
| `editFact(id, text)` | anything. Saves the edited text |
| `removeFact(id)` | anything |

### Errors

An adapter may throw an Error with `.auth = true` meaning "not signed in". The page stays quiet on it (no banner). Any other Error shows its `message`.

## capabilities (optional)

A host whose adapter cannot edit cards says so, and the page hides what would fail. Both default to `true`, so a host that omits the field (the House's) shows everything.

| Capability | When `false` |
|---|---|
| `editCards` | no "+ Add person", no Edit or Remove on a card (the adapter's `createPerson`, `updatePerson`, `deletePerson` are never called; they must still exist and may throw) |
| `links` | the adapter's `addLink`, `removeLink` are never called; cards still show the relations they carry |

Facts and suggestions (listing, accepting, editing, removing) are not affected.

## labels (optional)

Host-supplied wording; neutral defaults, so a stranger's host shows no House wording.

| Label | Default |
|---|---|
| `titleLead` | `Your` |
| `titleEmphasis` | `people` |
| `subtitle` | `Who's in your world, and who's around them.` |
| `dateExample` | `met them` (placeholder of the first date row) |

The heading renders `{titleLead} <em>{titleEmphasis}</em>`.
