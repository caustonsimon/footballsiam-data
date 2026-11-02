// Minimal OAuth 2.1 for claude.ai custom connectors.
// claude.ai insists on an OAuth handshake. Everything here lives behind the
// secret path token, so only someone holding the connector URL can reach it;
// authorisation is approved automatically and tokens are stateless HMACs keyed
// on MCP_PATH_TOKEN (rotating that secret revokes every token).
//
// Discovery (RFC 9728 / RFC 8414, path-aware):
//   /.well-known/oauth-protected-resource/mcp/<T>      -> resource metadata
//   /.well-known/oauth-authorization-server/oauth/<T>  -> AS metadata
// Endpoints: /oauth/<T>/register, /oauth/<T>/authorize, /oauth/<T>/token

const ALLOWED_REDIRECT_HOSTS = ["claude.ai", "claude.com", "localhost", "127.0.0.1"];
const CODE_TTL = 600; // seconds
const ACCESS_TTL = 60 * 60 * 24 * 30; // 30 days

export async function handleOAuth(request, env, url) {
  const T = env.MCP_PATH_TOKEN;
  const origin = url.origin;
  const issuer = `${origin}/oauth/${T}`;
  const p = url.pathname;

  if (p === `/.well-known/oauth-protected-resource/mcp/${T}`) {
    return j(200, { resource: `${origin}/mcp/${T}`, authorization_servers: [issuer], bearer_methods_supported: ["header"] });
  }
  if (p === `/.well-known/oauth-authorization-server/oauth/${T}` || p === `/oauth/${T}/.well-known/oauth-authorization-server`) {
    return j(200, {
      issuer,
      authorization_endpoint: `${issuer}/authorize`,
      token_endpoint: `${issuer}/token`,
      registration_endpoint: `${issuer}/register`,
      response_types_supported: ["code"],
      grant_types_supported: ["authorization_code", "refresh_token"],
      code_challenge_methods_supported: ["S256"],
      token_endpoint_auth_methods_supported: ["none", "client_secret_post", "client_secret_basic"],
      scopes_supported: ["substack"],
    });
  }
  if (request.method === "OPTIONS" && p.startsWith(`/oauth/${T}/`)) return cors(new Response(null, { status: 204 }));

  if (p === `/oauth/${T}/register` && request.method === "POST") {
    let body = {};
    try { body = await request.json(); } catch {}
    const uris = Array.isArray(body.redirect_uris) ? body.redirect_uris : [];
    if (!uris.length || !uris.every(okRedirect)) return j(400, { error: "invalid_redirect_uri" });
    return j(201, {
      client_id: "fs-claude-" + (await sign(T, "client:" + uris.join(" "))).slice(0, 16),
      client_id_issued_at: Math.floor(Date.now() / 1000),
      redirect_uris: uris,
      grant_types: ["authorization_code", "refresh_token"],
      response_types: ["code"],
      token_endpoint_auth_method: body.token_endpoint_auth_method || "none",
      client_name: body.client_name || "Claude",
    });
  }

  if (p === `/oauth/${T}/authorize` && request.method === "GET") {
    const q = url.searchParams;
    const redirect = q.get("redirect_uri") || "";
    if (!okRedirect(redirect)) return new Response("Invalid redirect_uri", { status: 400 });
    if (q.get("response_type") !== "code") return back(redirect, { error: "unsupported_response_type", state: q.get("state") });
    const challenge = q.get("code_challenge");
    if (!challenge || (q.get("code_challenge_method") || "S256") !== "S256") return back(redirect, { error: "invalid_request", error_description: "PKCE S256 required", state: q.get("state") });
    const payload = b64u(JSON.stringify({ c: challenge, r: redirect, e: now() + CODE_TTL }));
    const code = `${payload}.${await sign(T, "code:" + payload)}`;
    return back(redirect, { code, state: q.get("state") });
  }

  if (p === `/oauth/${T}/token` && request.method === "POST") {
    const form = await readForm(request);
    if (form.grant_type === "authorization_code") {
      const [payload, mac] = String(form.code || "").split(".");
      if (!payload || !mac || mac !== (await sign(T, "code:" + payload))) return tokErr("invalid_grant", "bad code");
      let data; try { data = JSON.parse(unb64u(payload)); } catch { return tokErr("invalid_grant", "bad code"); }
      if (data.e < now()) return tokErr("invalid_grant", "code expired");
      if (form.redirect_uri && form.redirect_uri !== data.r) return tokErr("invalid_grant", "redirect_uri mismatch");
      if (!form.code_verifier || (await s256(form.code_verifier)) !== data.c) return tokErr("invalid_grant", "PKCE check failed");
      return tokens(T);
    }
    if (form.grant_type === "refresh_token") {
      if (!(await validRefresh(T, form.refresh_token))) return tokErr("invalid_grant", "bad refresh token");
      return tokens(T);
    }
    return tokErr("unsupported_grant_type", "");
  }
  return null; // not an OAuth route
}

export async function bearerOk(request, env) {
  const h = request.headers.get("Authorization") || "";
  if (!h) return true; // the secret path alone is enough (keeps curl tests simple)
  const m = h.match(/^Bearer\s+(\S+)$/i);
  if (!m) return false;
  const [kind, exp, mac] = m[1].split(".");
  if (kind !== "at" || !exp || !mac) return false;
  if (Number(exp) < now()) return false;
  return mac === (await sign(env.MCP_PATH_TOKEN, `at:${exp}`));
}

export function unauthorized(url, env) {
  return new Response(JSON.stringify({ error: "invalid_token" }), {
    status: 401,
    headers: {
      "Content-Type": "application/json",
      "WWW-Authenticate": `Bearer resource_metadata="${url.origin}/.well-known/oauth-protected-resource/mcp/${env.MCP_PATH_TOKEN}"`,
    },
  });
}

async function tokens(T) {
  const exp = now() + ACCESS_TTL;
  const rexp = now() + ACCESS_TTL * 12;
  return j(200, {
    access_token: `at.${exp}.${await sign(T, `at:${exp}`)}`,
    token_type: "Bearer",
    expires_in: ACCESS_TTL,
    refresh_token: `rt.${rexp}.${await sign(T, `rt:${rexp}`)}`,
    scope: "substack",
  });
}
async function validRefresh(T, tok) {
  const [kind, exp, mac] = String(tok || "").split(".");
  return kind === "rt" && Number(exp) >= now() && mac === (await sign(T, `rt:${exp}`));
}

function okRedirect(u) {
  try {
    const x = new URL(u);
    const hostOk = ALLOWED_REDIRECT_HOSTS.some((h) => x.hostname === h || x.hostname.endsWith("." + h));
    const schemeOk = x.protocol === "https:" || (x.protocol === "http:" && (x.hostname === "localhost" || x.hostname === "127.0.0.1"));
    return hostOk && schemeOk;
  } catch { return false; }
}
function back(redirect, params) {
  const u = new URL(redirect);
  for (const [k, v] of Object.entries(params)) if (v !== null && v !== undefined) u.searchParams.set(k, v);
  return new Response(null, { status: 302, headers: { Location: u.toString(), "Cache-Control": "no-store" } });
}
async function readForm(request) {
  const ct = request.headers.get("content-type") || "";
  const out = {};
  if (ct.includes("application/json")) { try { Object.assign(out, await request.json()); } catch {} }
  else { const t = await request.text(); for (const [k, v] of new URLSearchParams(t)) out[k] = v; }
  return out;
}
function tokErr(error, desc) { return j(400, { error, error_description: desc }); }
function now() { return Math.floor(Date.now() / 1000); }
function j(status, body) {
  return cors(new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json", "Cache-Control": "no-store" } }));
}
function cors(r) {
  r.headers.set("Access-Control-Allow-Origin", "*");
  r.headers.set("Access-Control-Allow-Headers", "authorization, content-type, mcp-protocol-version");
  r.headers.set("Access-Control-Allow-Methods", "GET, POST, OPTIONS");
  return r;
}
const enc = new TextEncoder();
async function sign(key, msg) {
  const k = await crypto.subtle.importKey("raw", enc.encode(key), { name: "HMAC", hash: "SHA-256" }, false, ["sign"]);
  return b64uBytes(new Uint8Array(await crypto.subtle.sign("HMAC", k, enc.encode(msg))));
}
async function s256(v) { return b64uBytes(new Uint8Array(await crypto.subtle.digest("SHA-256", enc.encode(v)))); }
function b64uBytes(bytes) { let s = ""; for (const b of bytes) s += String.fromCharCode(b); return btoa(s).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, ""); }
function b64u(str) { return b64uBytes(enc.encode(str)); }
function unb64u(s) { const b = atob(s.replace(/-/g, "+").replace(/_/g, "/")); return new TextDecoder().decode(Uint8Array.from(b, (c) => c.charCodeAt(0))); }
