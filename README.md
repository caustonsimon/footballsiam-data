# Football Siam Substack connector

A remote MCP server on a Cloudflare Worker that lets Claude read and write **drafts** on footballsiam.substack.com. Built and tested 2 November 2026.

## What it can do
- `substack_list_drafts`, `substack_list_published`, `substack_get_draft` (body returned as markdown)
- `substack_create_draft`: new newsletter draft under Simon's byline (user 266411741), never published or sent
- `substack_update_draft`: change title, subtitle, body or audience of an unpublished draft; refuses published posts
- `substack_upload_image`: copy a public image onto Substack's image host

There is deliberately **no publish, schedule, send, delete or Notes tool**. Simon presses publish in Substack.

## Body format (markdown)
`#` headings, `**bold**`, `*italic*`, `[links](url)`, `` `code` ``, `-` and `1.` lists, `>` quotes, `---` rules,
images on their own line as `![alt](https://url "Caption")` (re-hosted on Substack automatically),
and on their own line `{{subscribe}}` (subscribe box) or `{{button: Label | https://url}}` (button).

## Substack endpoints used (unofficial, verified in Simon's session 2 Nov 2026)
- `GET /api/v1/drafts?filter=&offset=0&limit=N` (drafts + published, `posts[]`)
- `GET /api/v1/post_management/published?offset=0&limit=N&order_by=post_date&order_direction=desc`
- `GET /api/v1/drafts/{id}`, `POST /api/v1/drafts`, `PUT /api/v1/drafts/{id}`
- `POST /api/v1/image` with `{image: "data:<type>;base64,..."}` returns `{url, imageWidth, imageHeight, bytes, contentType}`
- `draft_body` is a JSON **string** of a ProseMirror doc. Node types confirmed: heading, paragraph, bullet_list, ordered_list, list_item, blockquote, horizontal_rule, captionedImage > image2 + caption, button, subscribeWidget > ctaCaption; marks strong, em, link, code.
- Create needs `draft_bylines: [{id: 266411741, is_guest: false}]`, `type: "newsletter"`, `audience`.

## Configuration
Variables: `SUBSTACK_HOST` = footballsiam.substack.com, `SUBSTACK_USER_ID` = 266411741.
Secrets (Simon sets them himself, never stored in code or docs):
- `SUBSTACK_SID`: value of the `substack.sid` cookie (Chrome > DevTools > Application > Cookies > substack.com). When it expires every tool returns "Substack refused the request ... copy a fresh substack.sid cookie".
- `MCP_PATH_TOKEN`: long random string. The connector URL is `https://<worker>.workers.dev/mcp/<token>`; anything else returns 404. Treat the URL as a password.

## Files
- `src/markdown.js` markdown <-> ProseMirror, `src/index.js` MCP server + Substack client
- `build.mjs` bundles both into `dist/worker.js` (paste this into the Cloudflare editor)
- `test/run.mjs` 15 tests with a mocked Substack (`node test/run.mjs`)

## Deployment (2 Nov 2026)
- Cloudflare Worker `fs-substack-connector` on Simon's account (caustonsimon.workers.dev), deployed from the dashboard editor with the minified build (`npx esbuild dist/worker.js --minify --format=esm`).
- Secrets `SUBSTACK_SID` and `MCP_PATH_TOKEN` set by Simon in Settings > Variables and Secrets. The token is NOT recorded here; it lives in the claude.ai connector URL.
- Verified live: initialize, tools/list, list drafts, list published, create (with a footballsiam.com image re-hosted), get, update.
- Fixes found in live testing: Workers needs fetch wrapped (`(...a) => fetchImpl(...a)`, else "Illegal invocation"); `/api/v1/drafts` mixes in published posts, so drafts come from `/api/v1/post_management/drafts`.
- Dashboard editor tip: open find/replace with Cmd+Alt+F and confirm the widget is open before typing.

## Test drafts
Drafts 223111450, 223111475 and 223114396 ("CONNECTOR TEST ... safe to delete") were made while testing. Simon can delete them in Substack.

## Hosting on Render (from 2 Nov 2026)
Moved off Cloudflare because Substack throttles Cloudflare Worker IPs (HTTP 429).
- This branch (`substack-connector` of footballsiam-data) holds only the connector. `server.mjs` wraps the same fetch handler in a Node HTTP server.
- `render.yaml` is a Render Blueprint: free web service, Singapore, health check `/healthz`.
- Simon sets `MCP_PATH_TOKEN` and `SUBSTACK_SID` himself in Render > Environment. Connector URL: `https://<service>.onrender.com/mcp/<MCP_PATH_TOKEN>`.
- Free plan sleeps after 15 minutes idle; the first call after that can take about a minute.
