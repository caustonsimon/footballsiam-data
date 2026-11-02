// Node host for the Football Siam Substack connector (Render, or any Node 20+ host).
// Wraps the same fetch handler the Cloudflare Worker uses.
import http from "node:http";
import worker from "./src/index.js";

const env = {
  MCP_PATH_TOKEN: process.env.MCP_PATH_TOKEN || "",
  SUBSTACK_SID: process.env.SUBSTACK_SID || "",
  SUBSTACK_HOST: process.env.SUBSTACK_HOST || "footballsiam.substack.com",
  SUBSTACK_USER_ID: process.env.SUBSTACK_USER_ID || "266411741",
};
const port = Number(process.env.PORT || 10000);

http.createServer(async (req, res) => {
  try {
    if (req.url === "/healthz") { res.writeHead(200, { "content-type": "text/plain" }); return res.end("ok"); }
    const proto = req.headers["x-forwarded-proto"] || "http";
    const host = req.headers["x-forwarded-host"] || req.headers.host;
    const chunks = [];
    for await (const c of req) chunks.push(c);
    const body = chunks.length && !["GET", "HEAD"].includes(req.method) ? Buffer.concat(chunks) : undefined;
    const headers = new Headers();
    for (const [k, v] of Object.entries(req.headers)) headers.set(k, Array.isArray(v) ? v.join(", ") : v);
    const request = new Request(`${proto}://${host}${req.url}`, { method: req.method, headers, body });
    const out = await worker.fetch(request, env, {});
    const h = {};
    out.headers.forEach((v, k) => { h[k] = v; });
    res.writeHead(out.status, h);
    res.end(Buffer.from(await out.arrayBuffer()));
  } catch (e) {
    console.error(e);
    if (!res.headersSent) res.writeHead(500, { "content-type": "text/plain" });
    res.end("Internal error");
  }
}).listen(port, () => console.log(`fs-substack-connector listening on ${port}`));
