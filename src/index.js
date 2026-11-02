// Football Siam Substack connector: a remote MCP server on a Cloudflare Worker.
// It reads and writes DRAFTS only. There is deliberately no publish, schedule,
// send or delete tool: Simon presses publish in Substack himself.
//
// Secrets (set in the Cloudflare dashboard, never in this file):
//   SUBSTACK_SID    the value of Simon's substack.sid cookie
//   MCP_PATH_TOKEN  a long random string; the connector URL is /mcp/<token>
// Plain variables (wrangler.toml):
//   SUBSTACK_HOST   footballsiam.substack.com
//   SUBSTACK_USER_ID  266411741 (Simon's byline)

import { markdownToDoc, docToMarkdown, imageSources, parseInline } from "./markdown.js";
import { handleOAuth, bearerOk, unauthorized } from "./oauth.js";

const PROTOCOL_VERSIONS = ["2025-06-18", "2025-03-26", "2024-11-05"];
const SERVER_INFO = { name: "football-siam-substack", version: "1.0.0" };
const UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Safari/537.36";
const AUDIENCES = ["everyone", "only_free", "only_paid", "founding"];
const MAX_IMAGE_BYTES = 10 * 1024 * 1024;

export const TOOLS = [
  {
    name: "substack_list_drafts",
    description: "List unpublished drafts on the Football Siam Substack, newest first: id, title, last updated and the editor link.",
    inputSchema: { type: "object", properties: { limit: { type: "integer", minimum: 1, maximum: 50, default: 10 } }, additionalProperties: false },
    annotations: { readOnlyHint: true },
  },
  {
    name: "substack_list_published",
    description: "List published posts on the Football Siam Substack, newest first: id, title, date, audience and public link.",
    inputSchema: { type: "object", properties: { limit: { type: "integer", minimum: 1, maximum: 50, default: 10 } }, additionalProperties: false },
    annotations: { readOnlyHint: true },
  },
  {
    name: "substack_get_draft",
    description: "Read one post or draft by id, with its body converted to markdown in the same dialect substack_create_draft accepts.",
    inputSchema: { type: "object", properties: { id: { type: "integer" } }, required: ["id"], additionalProperties: false },
    annotations: { readOnlyHint: true },
  },
  {
    name: "substack_create_draft",
    description:
      "Create a new UNPUBLISHED newsletter draft under Simon's byline. Nothing is sent or published; Simon reviews and publishes in Substack. " +
      "body_markdown supports # headings, **bold**, *italic*, [links](url), `code`, - bullet and 1. numbered lists, > quotes, --- rules, " +
      "images on their own line as ![alt](https://url \"Caption\") (use the public footballsiam.com image URL; Substack copies it onto its own host), and on their own line " +
      "{{subscribe}} for a subscribe box or {{button: Label | https://url}} for a button.",
    inputSchema: {
      type: "object",
      properties: {
        title: { type: "string", minLength: 1 },
        subtitle: { type: "string" },
        body_markdown: { type: "string", minLength: 1 },
        audience: { type: "string", enum: AUDIENCES, default: "everyone" },
        rehost_images: { type: "boolean", default: false, description: "Upload images to Substack now. Not needed: Substack copies linked images itself when the draft is opened." },
      },
      required: ["title", "body_markdown"],
      additionalProperties: false,
    },
  },
  {
    name: "substack_update_draft",
    description:
      "Change an existing UNPUBLISHED draft: title, subtitle, body (replaces the whole body; same markdown dialect as substack_create_draft) or audience. " +
      "Refuses to touch a post that is already published.",
    inputSchema: {
      type: "object",
      properties: {
        id: { type: "integer" },
        title: { type: "string", minLength: 1 },
        subtitle: { type: "string" },
        body_markdown: { type: "string", minLength: 1 },
        audience: { type: "string", enum: AUDIENCES },
        rehost_images: { type: "boolean", default: false },
      },
      required: ["id"],
      additionalProperties: false,
    },
  },
  {
    name: "substack_upload_image",
    description: "Copy a public image (by URL) onto Substack's image host and return the Substack URL, width and height.",
    inputSchema: { type: "object", properties: { url: { type: "string" } }, required: ["url"], additionalProperties: false },
  },
  {
    name: "substack_post_note",
    description:
      "PUBLISH a Substack Note on Simon's profile straight away (Notes have no draft stage). Use it for Football Siam's social posts. " +
      "text: plain lines, each line becomes a paragraph; **bold**, *italic* and [links](url) work. " +
      "Attach EITHER up to 4 image_urls (public images, e.g. the 1080x1350 social card) OR one link_url (Substack builds a preview card from the page), not both. " +
      "Refuses to post if a note with the same text already went out in the last 7 days.",
    inputSchema: {
      type: "object",
      properties: {
        text: { type: "string", minLength: 1, maxLength: 3000 },
        image_urls: { type: "array", items: { type: "string" }, maxItems: 4 },
        link_url: { type: "string" },
      },
      required: ["text"],
      additionalProperties: false,
    },
  },
  {
    name: "substack_list_notes",
    description: "List Simon's most recent Substack Notes: id, date, text and public link. Use it to check what has already been posted.",
    inputSchema: { type: "object", properties: { limit: { type: "integer", minimum: 1, maximum: 20, default: 10 } }, additionalProperties: false },
    annotations: { readOnlyHint: true },
  },
];

export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    const token = env.MCP_PATH_TOKEN || "";
    if (!token || token.length < 24) return text(500, "Connector not configured: MCP_PATH_TOKEN is missing or too short.");
    const oauth = await handleOAuth(request, env, url);
    if (oauth) return oauth;
    if (url.pathname !== `/mcp/${token}`) return text(404, "Not found");
    if (!(await bearerOk(request, env))) return unauthorized(url, env);
    if (request.method === "GET") return text(405, "This MCP server answers POST requests only.", { Allow: "POST" });
    if (request.method === "DELETE") return new Response(null, { status: 204 });
    if (request.method !== "POST") return text(405, "Method not allowed", { Allow: "POST" });

    let payload;
    try { payload = await request.json(); } catch { return json(400, rpcError(null, -32700, "Parse error")); }

    const client = new SubstackClient(env);
    if (Array.isArray(payload)) {
      const results = (await Promise.all(payload.map((m) => handleMessage(m, client)))).filter(Boolean);
      return results.length ? json(200, results) : new Response(null, { status: 202 });
    }
    const result = await handleMessage(payload, client);
    return result ? json(200, result) : new Response(null, { status: 202 });
  },
};

export async function handleMessage(msg, client) {
  if (!msg || msg.jsonrpc !== "2.0" || typeof msg.method !== "string") {
    return rpcError(msg?.id ?? null, -32600, "Invalid request");
  }
  const isNotification = msg.id === undefined || msg.id === null;
  try {
    switch (msg.method) {
      case "initialize": {
        const asked = msg.params?.protocolVersion;
        const protocolVersion = PROTOCOL_VERSIONS.includes(asked) ? asked : PROTOCOL_VERSIONS[0];
        return rpcResult(msg.id, {
          protocolVersion,
          capabilities: { tools: { listChanged: false } },
          serverInfo: SERVER_INFO,
          instructions:
            "Football Siam's Substack (footballsiam.substack.com). Newsletters are drafts only: create and edit them; Simon publishes them himself. " +
            "The one exception is substack_post_note, which publishes a Note immediately (Simon wants every Football Siam social post on Notes automatically). " +
            "House style: UK English, club names without sponsors or FC (Pathum United, Port, Bangkok, Prachuap).",
        });
      }
      case "ping": return isNotification ? null : rpcResult(msg.id, {});
      case "tools/list": return rpcResult(msg.id, { tools: TOOLS });
      case "tools/call": {
        const { name, arguments: args = {} } = msg.params || {};
        const tool = TOOLS.find((t) => t.name === name);
        if (!tool) return rpcError(msg.id, -32602, `Unknown tool: ${name}`);
        try {
          const out = await runTool(name, args, client);
          return rpcResult(msg.id, { content: [{ type: "text", text: JSON.stringify(out, null, 2) }], structuredContent: out });
        } catch (e) {
          return rpcResult(msg.id, { content: [{ type: "text", text: e.message || String(e) }], isError: true });
        }
      }
      default:
        if (isNotification) return null;
        return rpcError(msg.id, -32601, `Method not found: ${msg.method}`);
    }
  } catch (e) {
    return isNotification ? null : rpcError(msg.id, -32603, e.message || "Internal error");
  }
}

export async function runTool(name, a, client) {
  const limit = clampInt(a.limit, 1, 50, 10);
  switch (name) {
    case "substack_list_drafts": {
      // /api/v1/drafts mixes in published posts; post_management/drafts is drafts only.
      const r = await client.get(`/api/v1/post_management/drafts?order_by=draft_updated_at&order_direction=desc&offset=0&limit=${limit}`);
      const posts = (r.posts || []).filter((p) => !p.is_published);
      return { drafts: posts.map((p) => summary(p, client)) };
    }
    case "substack_list_published": {
      const r = await client.get(`/api/v1/post_management/published?offset=0&limit=${limit}&order_by=post_date&order_direction=desc`);
      return { posts: (r.posts || []).map((p) => summary(p, client)), total: r.total ?? null };
    }
    case "substack_get_draft": {
      const id = requireId(a.id);
      const d = await client.get(`/api/v1/drafts/${id}`);
      return { ...summary(d, client), subtitle: d.draft_subtitle ?? d.subtitle ?? "", body_markdown: docToMarkdown(d.draft_body) };
    }
    case "substack_create_draft": {
      requireText(a.title, "title");
      requireText(a.body_markdown, "body_markdown");
      const audience = checkAudience(a.audience || "everyone");
      const images = a.rehost_images ? await client.uploadImages(imageSources(a.body_markdown)) : {};
      const body = {
        draft_title: a.title.trim(),
        draft_subtitle: (a.subtitle || "").trim(),
        draft_body: JSON.stringify(markdownToDoc(a.body_markdown, { images })),
        draft_bylines: [{ id: client.userId, is_guest: false }],
        audience,
        type: "newsletter",
      };
      const d = await client.send("POST", "/api/v1/drafts", body);
      if (!d || !d.id) throw new Error("Substack did not return a draft id; nothing was created.");
      return { created: true, published: false, ...summary(d, client), images_rehosted: Object.keys(images).length };
    }
    case "substack_update_draft": {
      const id = requireId(a.id);
      const current = await client.get(`/api/v1/drafts/${id}`);
      if (current.is_published) throw new Error(`Post ${id} is already published. This connector only edits unpublished drafts.`);
      const body = {};
      if (a.title !== undefined) { requireText(a.title, "title"); body.draft_title = a.title.trim(); }
      if (a.subtitle !== undefined) body.draft_subtitle = String(a.subtitle).trim();
      if (a.audience !== undefined) body.audience = checkAudience(a.audience);
      if (a.body_markdown !== undefined) {
        requireText(a.body_markdown, "body_markdown");
        const images = a.rehost_images ? await client.uploadImages(imageSources(a.body_markdown)) : {};
        body.draft_body = JSON.stringify(markdownToDoc(a.body_markdown, { images }));
      }
      if (!Object.keys(body).length) throw new Error("Nothing to change: pass title, subtitle, body_markdown or audience.");
      await client.send("PUT", `/api/v1/drafts/${id}`, body);
      const after = await client.get(`/api/v1/drafts/${id}`);
      return { changed: Object.keys(body), published: !!after.is_published, ...summary(after, client) };
    }
    case "substack_upload_image": {
      const [img] = Object.values(await client.uploadImages([a.url]));
      return img;
    }
    case "substack_list_notes": {
      const notes = await client.recentNotes();
      return { notes: notes.slice(0, clampInt(a.limit, 1, 20, 10)) };
    }
    case "substack_post_note": {
      requireText(a.text, "text");
      const images = Array.isArray(a.image_urls) ? a.image_urls.filter(Boolean) : [];
      if (images.length && a.link_url) throw new Error("Attach either image_urls or link_url, not both. Put the link in the text instead.");
      if (images.length > 4) throw new Error("A note can carry at most 4 images.");
      for (const u of [...images, ...(a.link_url ? [a.link_url] : [])]) {
        if (!/^https:\/\//i.test(u)) throw new Error(`${u} must be a public https:// address.`);
      }
      const doc = noteDoc(a.text);
      const fingerprint = noteFingerprint(a.text);
      const recent = await client.recentNotes();
      const weekAgo = Date.now() - 7 * 24 * 3600 * 1000;
      const dupe = recent.find((n) => n.fingerprint === fingerprint && Date.parse(n.date) > weekAgo);
      if (dupe) throw new Error(`Not posted: the same note already went out on ${dupe.date} (${dupe.url}).`);
      const attachmentIds = [];
      for (const u of images) attachmentIds.push(await client.noteAttachment(u, "image"));
      if (a.link_url) attachmentIds.push(await client.noteAttachment(a.link_url, "link"));
      const body = { bodyJson: doc, tabId: "for-you", surface: "feed", replyMinimumRole: "everyone" };
      if (attachmentIds.length) body.attachmentIds = attachmentIds;
      const r = await client.request("POST", "/api/v1/comment/feed", body, "substack.com");
      const id = r.id ?? r.comment?.id;
      if (!id) throw new Error("Substack did not return a note id; check Simon's Notes before retrying.");
      return { posted: true, id, url: noteUrl(id), attachments: attachmentIds.length };
    }
    default:
      throw new Error(`Unknown tool: ${name}`);
  }
}

export class SubstackClient {
  constructor(env, fetchImpl = fetch) {
    this.host = env.SUBSTACK_HOST || "footballsiam.substack.com";
    this.userId = Number(env.SUBSTACK_USER_ID || 266411741);
    this.sid = env.SUBSTACK_SID || "";
    // Wrapped so the Workers runtime's fetch keeps its binding (calling it as a
    // method of this object throws "Illegal invocation" on Cloudflare).
    this.fetch = (...args) => fetchImpl(...args);
  }
  editUrl(id) { return `https://${this.host}/publish/post/${id}`; }
  headers(extra = {}) {
    if (!this.sid) throw new Error("Connector not configured: the SUBSTACK_SID secret is missing.");
    return { Cookie: `substack.sid=${this.sid}`, "User-Agent": UA, Accept: "application/json", Origin: `https://${this.host}`, Referer: `https://${this.host}/publish/home`, ...extra };
  }
  async get(path) { return this.request("GET", path); }
  async send(method, path, body) { return this.request(method, path, body); }
  async recentNotes() {
    const r = await this.request("GET", `/api/v1/reader/feed/profile/${this.userId}?types%5B%5D=note`, undefined, "substack.com");
    return (r.items || []).filter((i) => i.comment && i.comment.user_id === this.userId).map((i) => ({
      id: i.comment.id,
      date: i.comment.date,
      text: i.comment.body || "",
      url: noteUrl(i.comment.id),
      fingerprint: noteFingerprint(i.comment.body || ""),
    }));
  }
  async noteAttachment(url, type) {
    const r = await this.request("POST", "/api/v1/comment/attachment", { url, type }, "substack.com");
    if (!r.id) throw new Error(`Substack could not attach ${url}.`);
    return r.id;
  }
  async request(method, path, body, host = this.host) {
    const init = { method, headers: { ...this.headers(body !== undefined ? { "Content-Type": "application/json" } : {}), Origin: `https://${host}`, Referer: `https://${host}/` } };
    if (body !== undefined) init.body = JSON.stringify(body);
    let r;
    for (let attempt = 0; ; attempt++) {
      r = await this.fetch(`https://${host}${path}`, init);
      if (r.status !== 429 || attempt >= 2) break;
      const wait = Math.min(8, Number(r.headers.get("retry-after")) || 2 * (attempt + 1));
      await new Promise((res) => setTimeout(res, wait * 1000));
    }
    const raw = await r.text();
    if (r.status === 429) throw new Error("Substack is rate-limiting requests right now (HTTP 429). Wait a minute and try again.");
    if (r.status === 401 || r.status === 403) {
      throw new Error(`Substack refused the request (HTTP ${r.status}). The saved login has probably expired: copy a fresh substack.sid cookie into the SUBSTACK_SID secret.`);
    }
    if (!r.ok) throw new Error(`Substack returned HTTP ${r.status} for ${method} ${path.split("?")[0]}: ${raw.slice(0, 200)}`);
    try { return raw ? JSON.parse(raw) : {}; } catch { throw new Error(`Substack returned something that is not JSON for ${path.split("?")[0]}.`); }
  }
  async uploadImages(urls) {
    const out = {};
    let first = true;
    for (const src of urls) {
      if (!first) await new Promise((res) => setTimeout(res, 1500)); // Substack throttles bursts of uploads
      first = false;
      if (/^https:\/\/substack-post-media\.s3\.amazonaws\.com\//.test(src) || /^https:\/\/substackcdn\.com\//.test(src)) {
        out[src] = { url: src }; continue;
      }
      if (!/^https?:\/\//i.test(src)) throw new Error(`Image ${src} is not a web address; images must be public http(s) URLs.`);
      const r = await this.fetch(src, { headers: { "User-Agent": UA, Accept: "image/*" } });
      if (!r.ok) throw new Error(`Could not fetch image ${src} (HTTP ${r.status}).`);
      const type = (r.headers.get("content-type") || "").split(";")[0].trim();
      if (!/^image\//.test(type)) throw new Error(`${src} is not an image (content-type ${type || "unknown"}).`);
      const buf = await r.arrayBuffer();
      if (buf.byteLength > MAX_IMAGE_BYTES) throw new Error(`${src} is larger than 10 MB.`);
      const up = await this.send("POST", "/api/v1/image", { image: `data:${type};base64,${toBase64(buf)}` });
      if (!up.url) throw new Error(`Substack did not accept the image ${src}.`);
      out[src] = { url: up.url, imageWidth: up.imageWidth, imageHeight: up.imageHeight, bytes: up.bytes, contentType: up.contentType || type, source: src };
    }
    return out;
  }
}

function summary(p, client) {
  return {
    id: p.id,
    title: p.draft_title || p.title || "",
    published: !!p.is_published,
    audience: p.audience || null,
    updated: p.draft_updated_at || p.updated_at || null,
    post_date: p.post_date || null,
    edit_url: client.editUrl(p.id),
    public_url: p.is_published && p.slug ? `https://${client.host}/p/${p.slug}` : null,
  };
}

export function noteDoc(text) {
  const paragraphs = String(text).replace(/\r\n?/g, "\n").split("\n").map((l) => l.trim()).filter(Boolean);
  return {
    type: "doc",
    attrs: { schemaVersion: "v1" },
    content: paragraphs.map((p) => {
      const inline = parseInline(p).map((n) => n.marks
        ? { ...n, marks: n.marks.filter((m) => m.type !== "code").map((m) => (m.type === "strong" ? { type: "bold" } : m.type === "em" ? { type: "italic" } : m)) }
        : n).map((n) => (n.marks && !n.marks.length ? { type: "text", text: n.text } : n));
      return inline.length ? { type: "paragraph", content: inline } : { type: "paragraph" };
    }),
  };
}
export function noteFingerprint(text) {
  return String(text).replace(/\[([^\]]*)\]\([^)]*\)/g, "$1").replace(/[*_`]/g, "").replace(/https?:\/\/\S+/g, "").replace(/\s+/g, " ").trim().toLowerCase().slice(0, 200);
}
function noteUrl(id) { return `https://substack.com/@footballsiam/note/c-${id}`; }

function toBase64(buf) {
  const bytes = new Uint8Array(buf);
  let s = "";
  for (let i = 0; i < bytes.length; i += 0x8000) s += String.fromCharCode.apply(null, bytes.subarray(i, i + 0x8000));
  return btoa(s);
}
function clampInt(v, lo, hi, dflt) { const n = Number.isInteger(v) ? v : dflt; return Math.min(hi, Math.max(lo, n)); }
function requireId(id) { if (!Number.isInteger(id) || id <= 0) throw new Error("id must be a positive whole number."); return id; }
function requireText(v, name) { if (typeof v !== "string" || !v.trim()) throw new Error(`${name} must not be empty.`); }
function checkAudience(v) { if (!AUDIENCES.includes(v)) throw new Error(`audience must be one of ${AUDIENCES.join(", ")}.`); return v; }
function rpcResult(id, result) { return { jsonrpc: "2.0", id, result }; }
function rpcError(id, code, message) { return { jsonrpc: "2.0", id, error: { code, message } }; }
function json(status, body) { return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } }); }
function text(status, body, headers = {}) { return new Response(body, { status, headers: { "Content-Type": "text/plain", ...headers } }); }
