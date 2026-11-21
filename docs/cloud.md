# Run it in the cloud (optional)

You can run Scriptorium on your own computer, keep your AI's entries in **your own free Cloudflare account**, or run both side by side. With Cloudflare, apps can write and read entries while your computer is off.

Your entries live only in your account. Nobody else, including the makers of Scriptorium, can see them.

## What the cloud option does and does not do

- It offers the `pen_*` tools: write, edit, read, list, history and delete entries, and list collections.
- It does not offer `write_notes`, `write_memories` or `write_people`. Those read your chat export and run a model on your computer, which a cloud Worker cannot do. Use them from the local server.
- Entries are kept in one small database inside a single Cloudflare Durable Object in your account. All your agents' entries live in that one store, separated by agent. Keys only decide who may write and which agents they speak as, so renaming, adding or changing keys never moves or hides entries. Deleting an entry sets it aside; it stays in the database.

## What you need

- A free [Cloudflare](https://dash.cloudflare.com/sign-up) account.
- Node.js, for the `wrangler` command (`npx wrangler` works without installing it).
- Python 3.9 or newer, to run the build step.
- A random key of at least 16 characters for each AI (see below).

## Set it up

1. Sign in once: `npx wrangler login`
2. Copy the example config and open it: `cp cloud/wrangler.toml.example cloud/wrangler.toml`
   Set `compatibility_date` to today's date, or to the newest date your wrangler accepts (it says so if the date is too new). To stamp entries with your own day, uncomment `SCRIPTORIUM_PEN_TZ` and set your time zone.
3. Set the main key as a secret, typing a random key when asked: `npx wrangler secret put SCRIPTORIUM_MCP_KEY --config cloud/wrangler.toml`
4. Bind a key to your AI. For one AI, give it the same key:
   `npx wrangler secret put SCRIPTORIUM_AGENTS --config cloud/wrangler.toml` and type `{"SCRIPTORIUM_MCP_KEY":"<name of your AI>"}`.
   For several AIs, make one more secret per key (each at least 16 characters) and list them, as in [Agents](agents.md): `{"SCRIPTORIUM_MCP_KEY":["Mira","Echo"],"SCRIPTORIUM_AGENT_KEY_3":["Zed"]}`. Each key value must have a single agent mapping. Several keys can include the same agent.
5. Build the Worker folder: `python3 cloud/build.py` (it replaces `cloud/build/` only; it refuses any folder it did not make)
6. Deploy it: `npx wrangler deploy --config cloud/wrangler.toml`

Wrangler prints your Worker's address, like `https://scriptorium-pen.<your-name>.workers.dev`. Send JSON-RPC requests with `POST /mcp`. Send one message per request. Notifications receive an empty HTTP 202 response, and a missing or wrong key receives an empty HTTP 401 response. The Worker requires `SCRIPTORIUM_MCP_KEY` to be at least 16 characters.

To try it on your own computer first, run `npx wrangler dev --config cloud/wrangler.toml` after the build. The secrets for a local run go in `cloud/.dev.vars` (one `NAME=value` per line; never share or commit this file).

## Connect an app

Give the app the address (`https://<your worker>/mcp`) and the key bound to your AI, sent as `Authorization: Bearer <key>`.

- **The Scriptorium plugin for Claude Code:** put the address in the Server address field and the key in the Key field.
- **Any app that lets you add a header to an MCP server:** add the `Authorization` header above.
- **claude.ai:** at the time of writing, the custom connector form asks for an address and an optional sign-in, and has no field for a key like this one. If that has changed, use the address and key as above; if not, use the plugin or another app.

Apps can send an `X-Request-Id` header with `pen_write`, `pen_edit`, and `pen_delete`. For 24 hours after a successful write, retrying with the same request ID, tool, arguments and acting agent returns the saved result with the current JSON-RPC ID. JSON key order and whitespace can vary. The request ID applies across the deployment, so using it for a different write within that window returns HTTP 409. Reads always return current data.

## Rotate a key

1. Add a new secret with a fresh key, for example `npx wrangler secret put SCRIPTORIUM_AGENT_KEY_NEW --config cloud/wrangler.toml`.
2. Update `SCRIPTORIUM_AGENTS` to give the new secret the same agent mapping as the old one. Keep both mappings during the switch.
3. Switch your apps to the new key and verify that they can access their entries.
4. Remove the old mapping from `SCRIPTORIUM_AGENTS`, then delete the old secret. If the old secret is `SCRIPTORIUM_MCP_KEY`, replace its value with a fresh random key instead. The Worker requires that secret to be set.

## Limits

The free plan has daily limits on requests and on database reads and writes, and a total storage size. They are far above what one AI writing entries uses, but check Cloudflare's current numbers on their pricing page, since they change. One request body may be up to 1,000,000 bytes. Cloudflare may still label Python Workers as beta.

## Moving between local and cloud

Local and cloud are separate stores. Transfer of an entire entry store between them is outside this release. The [store interface](your-own-store.md) defines export and import methods for storage implementations.

## Local stays as it is

Nothing about running Scriptorium on your own computer changes: same command, same folder, same tools. The cloud files live in `cloud/` and the database code in `engine/cloud_pen.py`; the local server never loads them.
