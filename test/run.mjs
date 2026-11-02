import assert from "node:assert/strict";
import { markdownToDoc, docToMarkdown, imageSources, parseInline } from "../src/markdown.js";
import worker, { handleMessage, SubstackClient, TOOLS } from "../src/index.js";

let passed = 0;
const t = async (name, fn) => { await fn(); passed++; console.log("ok -", name); };
const types = (doc) => { const s = []; (function w(n) { s.push(n.type); (n.content || []).forEach(w); })(doc); return s; };

await t("inline marks", () => {
  const r = parseInline("**Port** beat *Bangkok* 2-1, [report](https://footballsiam.com/x/) and `code`");
  assert.deepEqual(r.map((n) => [n.text, (n.marks || []).map((m) => m.type).join("+")]), [
    ["Port", "strong"], [" beat ", ""], ["Bangkok", "em"], [" 2-1, ", ""], ["report", "link"], [" and ", ""], ["code", "code"],
  ]);
  assert.equal(r[4].marks[0].attrs.href, "https://footballsiam.com/x/");
});

await t("underscores inside words stay literal", () => {
  const r = parseInline("snake_case_name and _italic_");
  assert.equal(r[0].text, "snake_case_name and ");
  assert.equal(r[1].marks[0].type, "em");
});

await t("bold link and link with bold", () => {
  const r = parseInline("**[Read it](https://a.b/)** then [**this**](https://c.d/)");
  assert.deepEqual(r[0].marks.map((m) => m.type), ["strong", "link"]);
  assert.deepEqual(r[2].marks.map((m) => m.type), ["link", "strong"]);
});

const sample = `# The Weekend Review

**Port** stay top. Read the [full round-up](https://footballsiam.com/round-up/).
Second line of the same paragraph.

![Port celebrate](https://footballsiam.com/wp-content/uploads/a.jpg "Picture: Port.")

## Three things

- First point
- Second point with *italic*
  carried on

1. One
2. Two

> A quote from the coach.

---

{{button: Read the round-up | https://footballsiam.com/round-up/}}

{{subscribe}}`;

await t("block structure", () => {
  const doc = markdownToDoc(sample);
  const top = doc.content.map((n) => n.type);
  assert.deepEqual(top, ["heading", "paragraph", "captionedImage", "heading", "bullet_list", "ordered_list", "blockquote", "horizontal_rule", "button", "subscribeWidget"]);
  assert.equal(doc.content[0].attrs.level, 1);
  assert.match(doc.content[1].content.map((n) => n.text).join(""), /round-up\. Second line/);
  assert.equal(doc.content[4].content.length, 2);
  assert.match(JSON.stringify(doc.content[4].content[1]), /carried on/);
  assert.equal(doc.content[5].attrs.order, 1);
  assert.equal(doc.content[2].content[1].type, "caption");
  assert.equal(doc.content[8].attrs.url, "https://footballsiam.com/round-up/");
  for (const ty of types(doc)) assert.ok(["doc","heading","text","paragraph","captionedImage","image2","caption","bullet_list","list_item","ordered_list","blockquote","horizontal_rule","button","subscribeWidget","ctaCaption"].includes(ty), ty);
});

await t("image sources and rehost mapping", () => {
  assert.deepEqual(imageSources(sample), ["https://footballsiam.com/wp-content/uploads/a.jpg"]);
  const doc = markdownToDoc(sample, { images: { "https://footballsiam.com/wp-content/uploads/a.jpg": { url: "https://substack-post-media.s3.amazonaws.com/x.jpg", imageWidth: 1080, imageHeight: 1350, bytes: 5, contentType: "image/jpeg" } } });
  const img = doc.content[2].content[0].attrs;
  assert.equal(img.src, "https://substack-post-media.s3.amazonaws.com/x.jpg");
  assert.equal(img.width, 1080);
  assert.equal(img.alt, "Port celebrate");
});

await t("round trip markdown -> doc -> markdown -> doc is stable", () => {
  const d1 = markdownToDoc(sample);
  const md2 = docToMarkdown(d1);
  const d2 = markdownToDoc(md2);
  assert.deepEqual(d2, d1);
});

await t("empty body gives one empty paragraph", () => {
  assert.deepEqual(markdownToDoc("  \n"), { type: "doc", content: [{ type: "paragraph" }] });
});

// ---------- worker with mocked Substack ----------
function mockFetch(log, { published = false, status = 200 } = {}) {
  return async (url, init = {}) => {
    log.push({ url, method: init.method || "GET", body: init.body, headers: init.headers });
    const u = new URL(url);
    const ok = (obj) => new Response(JSON.stringify(obj), { status, headers: { "content-type": "application/json" } });
    if (u.host === "footballsiam.com") return new Response(new Uint8Array([137, 80, 78, 71]), { status: 200, headers: { "content-type": "image/png" } });
    if (u.pathname === "/api/v1/image") return ok({ id: 1, url: "https://substack-post-media.s3.amazonaws.com/public/images/abc.png", imageWidth: 10, imageHeight: 20, bytes: 4, contentType: "image/png" });
    if (u.pathname === "/api/v1/drafts" && init.method === "POST") return ok({ id: 555, draft_title: JSON.parse(init.body).draft_title, is_published: false, audience: "everyone" });
    if (u.pathname.startsWith("/api/v1/drafts/") && init.method === "PUT") return ok({ id: 555 });
    if (u.pathname.startsWith("/api/v1/drafts/")) return ok({ id: 555, draft_title: "T", draft_subtitle: "S", is_published: published, audience: "everyone", draft_body: JSON.stringify(markdownToDoc("Hello **world**")) });
    if (u.pathname === "/api/v1/post_management/drafts") return ok({ posts: [{ id: 1, draft_title: "A", is_published: false }, { id: 2, draft_title: "B", is_published: true }], total: 2 });
    if (u.pathname === "/api/v1/post_management/published") return ok({ posts: [{ id: 2, title: "B", is_published: true, slug: "b", post_date: "2026-10-01" }], total: 1 });
    return new Response("nope", { status: 404 });
  };
}
const env = { SUBSTACK_SID: "test-sid", MCP_PATH_TOKEN: "x".repeat(32), SUBSTACK_HOST: "footballsiam.substack.com", SUBSTACK_USER_ID: "266411741" };
const call = (client, name, args) => handleMessage({ jsonrpc: "2.0", id: 7, method: "tools/call", params: { name, arguments: args } }, client);

await t("initialize and tools/list", async () => {
  const c = new SubstackClient(env, mockFetch([]));
  const init = await handleMessage({ jsonrpc: "2.0", id: 1, method: "initialize", params: { protocolVersion: "2025-06-18" } }, c);
  assert.equal(init.result.protocolVersion, "2025-06-18");
  const list = await handleMessage({ jsonrpc: "2.0", id: 2, method: "tools/list" }, c);
  assert.deepEqual(list.result.tools.map((x) => x.name), TOOLS.map((x) => x.name));
  assert.ok(!list.result.tools.some((x) => /publish(?!ed)|send|delete|schedule/.test(x.name)), "no publish/send/delete tools");
  assert.equal(await handleMessage({ jsonrpc: "2.0", method: "notifications/initialized" }, c), null);
});

await t("create draft uploads images, sets byline, never publishes", async () => {
  const log = [];
  const c = new SubstackClient(env, mockFetch(log));
  const r = await call(c, "substack_create_draft", { title: "Weekend review", subtitle: "Sub", rehost_images: true, body_markdown: "Hi\n\n![a](https://footballsiam.com/wp-content/uploads/a.png \"Cap\")" });
  assert.ok(!r.result.isError, r.result.content[0].text);
  const out = r.result.structuredContent;
  assert.equal(out.id, 555); assert.equal(out.published, false); assert.equal(out.images_rehosted, 1);
  assert.equal(out.edit_url, "https://footballsiam.substack.com/publish/post/555");
  const post = log.find((l) => l.method === "POST" && l.url.endsWith("/api/v1/drafts"));
  const body = JSON.parse(post.body);
  assert.deepEqual(body.draft_bylines, [{ id: 266411741, is_guest: false }]);
  assert.equal(body.type, "newsletter");
  assert.match(body.draft_body, /substack-post-media/);
  assert.equal(post.headers.Cookie, "substack.sid=test-sid");
  assert.ok(!log.some((l) => /publish|send|schedule/.test(new URL(l.url).pathname)), "no publish endpoint touched");
});

await t("create draft by default links images without uploading them", async () => {
  const log = [];
  const c = new SubstackClient(env, mockFetch(log));
  const src = "https://footballsiam.com/wp-content/uploads/a.png";
  const r = await call(c, "substack_create_draft", { title: "T", body_markdown: `![a](${src})` });
  assert.ok(!r.result.isError);
  assert.equal(r.result.structuredContent.images_rehosted, 0);
  assert.ok(!log.some((l) => l.url.endsWith("/api/v1/image") || l.url.startsWith("https://footballsiam.com")), "no fetch or upload");
  const body = JSON.parse(log.find((l) => l.method === "POST").body);
  assert.ok(body.draft_body.includes(src));
});

await t("update refuses a published post", async () => {
  const log = [];
  const c = new SubstackClient(env, mockFetch(log, { published: true }));
  const r = await call(c, "substack_update_draft", { id: 555, title: "x" });
  assert.equal(r.result.isError, true);
  assert.match(r.result.content[0].text, /already published/);
  assert.ok(!log.some((l) => l.method === "PUT"));
});

await t("update draft sends only the changed fields", async () => {
  const log = [];
  const c = new SubstackClient(env, mockFetch(log));
  const r = await call(c, "substack_update_draft", { id: 555, subtitle: "New" });
  assert.ok(!r.result.isError);
  const put = log.find((l) => l.method === "PUT");
  assert.deepEqual(JSON.parse(put.body), { draft_subtitle: "New" });
});

await t("get draft returns markdown", async () => {
  const c = new SubstackClient(env, mockFetch([]));
  const r = await call(c, "substack_get_draft", { id: 555 });
  assert.equal(r.result.structuredContent.body_markdown, "Hello **world**\n");
});

await t("list drafts hides published, list published gives links", async () => {
  const c = new SubstackClient(env, mockFetch([]));
  const d = await call(c, "substack_list_drafts", {});
  assert.deepEqual(d.result.structuredContent.drafts.map((x) => x.id), [1]);
  const p = await call(c, "substack_list_published", { limit: 3 });
  assert.equal(p.result.structuredContent.posts[0].public_url, "https://footballsiam.substack.com/p/b");
});

await t("expired login gives a clear message", async () => {
  const c = new SubstackClient(env, mockFetch([], { status: 403 }));
  const r = await call(c, "substack_list_drafts", {});
  assert.equal(r.result.isError, true);
  assert.match(r.result.content[0].text, /substack\.sid/);
});

await t("HTTP layer: wrong path 404, right path works, GET 405", async () => {
  const good = `https://w.example/mcp/${env.MCP_PATH_TOKEN}`;
  const bad = await worker.fetch(new Request("https://w.example/mcp/wrong", { method: "POST", body: "{}" }), env);
  assert.equal(bad.status, 404);
  const get = await worker.fetch(new Request(good), env);
  assert.equal(get.status, 405);
  const ok = await worker.fetch(new Request(good, { method: "POST", body: JSON.stringify({ jsonrpc: "2.0", id: 1, method: "tools/list" }) }), env);
  assert.equal(ok.status, 200);
  assert.equal((await ok.json()).result.tools.length, TOOLS.length);
  const note = await worker.fetch(new Request(good, { method: "POST", body: JSON.stringify({ jsonrpc: "2.0", method: "notifications/initialized" }) }), env);
  assert.equal(note.status, 202);
  const unconf = await worker.fetch(new Request(good, { method: "POST", body: "{}" }), { ...env, MCP_PATH_TOKEN: "" });
  assert.equal(unconf.status, 500);
});

await t("OAuth: discovery, register, authorize, token, then MCP with the bearer", async () => {
  const T = env.MCP_PATH_TOKEN, base = "https://w.example";
  const req = (path, init) => worker.fetch(new Request(base + path, init), env);
  const prm = await (await req(`/.well-known/oauth-protected-resource/mcp/${T}`)).json();
  assert.equal(prm.resource, `${base}/mcp/${T}`);
  const issuer = prm.authorization_servers[0];
  const as = await (await req(new URL(`/.well-known/oauth-authorization-server${new URL(issuer).pathname}`, base).pathname)).json();
  assert.equal(as.issuer, issuer);
  assert.equal((await req("/.well-known/oauth-authorization-server")).status, 404, "nothing at the root");
  assert.equal((await req("/register", { method: "POST", body: "{}" })).status, 404, "no root register");

  const redirect = "https://claude.ai/api/mcp/auth_callback";
  const bad = await req(new URL(as.registration_endpoint).pathname, { method: "POST", body: JSON.stringify({ redirect_uris: ["https://evil.example/cb"] }) });
  assert.equal(bad.status, 400);
  const reg = await req(new URL(as.registration_endpoint).pathname, { method: "POST", body: JSON.stringify({ redirect_uris: [redirect], client_name: "Claude" }) });
  assert.equal(reg.status, 201);
  const client = await reg.json();

  const verifier = "v".repeat(50);
  const digest = new Uint8Array(await crypto.subtle.digest("SHA-256", new TextEncoder().encode(verifier)));
  const challenge = btoa(String.fromCharCode(...digest)).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
  const authPath = new URL(as.authorization_endpoint).pathname + `?response_type=code&client_id=${client.client_id}&redirect_uri=${encodeURIComponent(redirect)}&code_challenge=${challenge}&code_challenge_method=S256&state=xyz`;
  const auth = await req(authPath, { redirect: "manual" });
  assert.equal(auth.status, 302);
  const loc = new URL(auth.headers.get("Location"));
  assert.equal(loc.origin + loc.pathname, redirect);
  assert.equal(loc.searchParams.get("state"), "xyz");
  const code = loc.searchParams.get("code");

  const tokPath = new URL(as.token_endpoint).pathname;
  const wrong = await req(tokPath, { method: "POST", headers: { "Content-Type": "application/x-www-form-urlencoded" }, body: new URLSearchParams({ grant_type: "authorization_code", code, code_verifier: "wrong", redirect_uri: redirect }) });
  assert.equal(wrong.status, 400);
  const tok = await (await req(tokPath, { method: "POST", headers: { "Content-Type": "application/x-www-form-urlencoded" }, body: new URLSearchParams({ grant_type: "authorization_code", code, code_verifier: verifier, redirect_uri: redirect, client_id: client.client_id }) })).json();
  assert.equal(tok.token_type, "Bearer");

  const list = (auth) => req(`/mcp/${T}`, { method: "POST", headers: { Authorization: auth, "Content-Type": "application/json" }, body: JSON.stringify({ jsonrpc: "2.0", id: 1, method: "tools/list" }) });
  assert.equal((await list(`Bearer ${tok.access_token}`)).status, 200);
  const denied = await list("Bearer at.9999999999.forged");
  assert.equal(denied.status, 401);
  assert.match(denied.headers.get("WWW-Authenticate"), /resource_metadata=/);

  const refreshed = await (await req(tokPath, { method: "POST", headers: { "Content-Type": "application/x-www-form-urlencoded" }, body: new URLSearchParams({ grant_type: "refresh_token", refresh_token: tok.refresh_token }) })).json();
  assert.equal((await list(`Bearer ${refreshed.access_token}`)).status, 200);
  const evil = await req(new URL(as.authorization_endpoint).pathname + `?response_type=code&redirect_uri=${encodeURIComponent("https://evil.example/cb")}&code_challenge=${challenge}`, { redirect: "manual" });
  assert.equal(evil.status, 400);
  // a different path token never reaches the OAuth routes
  assert.equal((await req(`/.well-known/oauth-protected-resource/mcp/wrong`)).status, 404);
});

// ---------- Notes ----------
import { noteDoc, noteFingerprint } from "../src/index.js";
function notesFetch(log, { existing = [], rateLimitFirst = 0 } = {}) {
  let limited = rateLimitFirst;
  return async (url, init = {}) => {
    log.push({ url, method: init.method || "GET", body: init.body, headers: init.headers });
    const u = new URL(url);
    const ok = (o) => new Response(JSON.stringify(o), { status: 200, headers: { "content-type": "application/json" } });
    if (limited > 0) { limited--; return new Response("slow down", { status: 429, headers: { "retry-after": "0" } }); }
    if (u.host !== "substack.com") return new Response("wrong host", { status: 404 });
    if (u.pathname.startsWith("/api/v1/reader/feed/profile/")) return ok({ items: existing.map((e) => ({ comment: { id: e.id, date: e.date, body: e.body, user_id: 266411741 } })) });
    if (u.pathname === "/api/v1/comment/attachment") return ok({ id: "att-" + JSON.parse(init.body).type });
    if (u.pathname === "/api/v1/comment/feed") return ok({ id: 999 });
    return new Response("nope", { status: 404 });
  };
}

await t("note doc: lines become paragraphs, marks use Notes names", () => {
  const d = noteDoc("**Port** stay top.\n\nRead *more*: [round-up](https://footballsiam.com/x/)\n#ThaiLeague1");
  assert.deepEqual(d.attrs, { schemaVersion: "v1" });
  assert.equal(d.content.length, 3);
  assert.deepEqual(d.content[0].content[0].marks, [{ type: "bold" }]);
  assert.deepEqual(d.content[1].content[1].marks, [{ type: "italic" }]);
  assert.equal(d.content[1].content[3].marks[0].type, "link");
  assert.equal(d.content[2].content[0].text, "#ThaiLeague1");
});

await t("post note: attaches the image, posts to substack.com, returns the link", async () => {
  const log = [];
  const c = new SubstackClient(env, notesFetch(log));
  const r = await call(c, "substack_post_note", { text: "Six goals in the rain.\nhttps://footballsiam.com/x/", image_urls: ["https://footballsiam.com/wp-content/uploads/a.jpg"] });
  assert.ok(!r.result.isError, r.result.content[0].text);
  assert.equal(r.result.structuredContent.url, "https://substack.com/@footballsiam/note/c-999");
  const feed = log.find((l) => l.url.endsWith("/api/v1/comment/feed"));
  const body = JSON.parse(feed.body);
  assert.deepEqual(body.attachmentIds, ["att-image"]);
  assert.equal(body.bodyJson.content.length, 2);
  assert.equal(feed.headers.Origin, "https://substack.com");
});

await t("post note: refuses a duplicate from the last 7 days and never calls the feed", async () => {
  const log = [];
  const existing = [{ id: 5, date: new Date().toISOString(), body: "Six goals in the rain. https://footballsiam.com/x/" }];
  const c = new SubstackClient(env, notesFetch(log, { existing }));
  const r = await call(c, "substack_post_note", { text: "Six goals in the **rain**.\nhttps://footballsiam.com/y/" });
  assert.equal(r.result.isError, true);
  assert.match(r.result.content[0].text, /already went out/);
  assert.ok(!log.some((l) => l.url.endsWith("/api/v1/comment/feed")));
});

await t("post note: images and link together are refused", async () => {
  const c = new SubstackClient(env, notesFetch([]));
  const r = await call(c, "substack_post_note", { text: "x", image_urls: ["https://a/b.jpg"], link_url: "https://c/" });
  assert.equal(r.result.isError, true);
});

await t("429 is retried, then succeeds", async () => {
  const log = [];
  const c = new SubstackClient(env, notesFetch(log, { rateLimitFirst: 2 }));
  const r = await call(c, "substack_list_notes", {});
  assert.ok(!r.result.isError, r.result.content[0].text);
  assert.equal(log.length, 3);
});

await t("fingerprint ignores links and formatting", () => {
  assert.equal(noteFingerprint("**A** [b](https://x) https://y c"), noteFingerprint("A b c"));
});

console.log(`\n${passed} tests passed`);
