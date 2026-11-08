# September Registry

The static origin behind `https://registry.september.wtf`. Two read-only JSON
documents plus icons. No compute, no database, no secrets - ever.

## What is served

| Path | What | Consumer |
|---|---|---|
| `/servers` | First-party hosted-MCP-server listing (`{"version", "servers": [...]}`) | engine `registry_client.py` -> `GET /assets/registry` (the `septemberai` tier) |
| `/client-metadata.json` | CIMD client identity (SEP-991) | a remote MCP server's authorization server fetches it mid-authorize; the engine uses this URL as its OAuth `client_id` |
| `/icons/*` | Logos for the catalog UI | product (bap) |

Note the listing path is `/servers` with no extension - the engine's
`RegistryClient.fetch()` appends `/servers` to `SEPTEMBER_REGISTRY_URL`.

## Deploy (Cloudflare Pages)

1. Create a Pages project from this directory (or `wrangler pages deploy .`).
2. Add the custom domain `registry.september.wtf` (one CNAME; TLS automatic).
3. The `_headers` file sets content types, caching, and CORS.

## Flip the engine on

```
SEPTEMBER_REGISTRY_URL=https://registry.september.wtf
SEPTEMBER_CLIENT_METADATA_URL=https://registry.september.wtf/client-metadata.json
```

Both are dormant when unset; a dead registry never blocks engine boot.

## Rules

- **The CIMD URL is permanent.** `/client-metadata.json` is the engine's OAuth
  client_id at every AS that supports CIMD. Never move or rename it -
  authorization servers will not follow redirects.
- **The redirect model is broker-only.** Engines are never browser-reachable
  (there are millions of them; OAuth requires exact pre-registered redirect
  URIs, so per-engine callback links are impossible by design). So
  `redirect_uris` lists ONE fixed product-broker callback: the browser lands
  there, the product resolves `state` (= connection_id) to the owning engine
  and forwards the code via the authed `POST /assets/callback`. The engine
  stays dark. Two entries are listed: the INTERIM product capture
  (`https://api.bap.wtf/api/assets/oauth/callback` - bap receives the
  redirect and forwards to the owning engine) and the future standalone
  broker (`https://connect.september.wtf/oauth/callback`).
- **What is frozen vs editable:** the DOCUMENT URL (the client_id) is
  permanent - never move `/client-metadata.json`. The CONTENTS are fetched
  live by authorization servers, so redirect_uris CAN evolve: adding an
  entry is safe; removing one breaks any flow still using it.
- The product must pass the SAME broker URL as `oauth_config.redirect_uri`
  when registering/connecting, so the authorize request and the engine's
  token exchange echo it identically (RFC 6749 requires the match).
- **Nothing secret goes on this host.** Public listing + public client
  identity only. `token_endpoint_auth_method` stays `"none"` (public client;
  PKCE is the proof).

## Adding a first-party server

Append an object to `servers` in `/servers`. Fields the engine parses
(`registry_client.parse_entry`): `name` (required, unique), `display_name`,
`description`, `category`, `transport`, `server_url`, `icon_url`,
`auth_method` (`oauth|api_key|none`), `scopes`, `oauth_authorize_url`,
`oauth_token_url`, `requires_pkce`. Malformed entries are skipped, never
fatal.

```json
{
  "name": "september-docs",
  "display_name": "September Docs",
  "description": "Search and read September product documentation.",
  "category": "knowledge",
  "transport": "http",
  "server_url": "https://mcp.september.wtf/docs",
  "icon_url": "https://registry.september.wtf/icons/september-docs.png",
  "auth_method": "none",
  "scopes": [],
  "requires_pkce": false
}
```
