// Markdown <-> Substack ProseMirror document.
// Supports: headings, paragraphs, bold, italic, links, inline code, bullet and
// numbered lists, blockquotes, horizontal rules, images with captions, and two
// Substack widgets written on their own line: {{subscribe}} and
// {{button: Label | https://url}}.

const IMAGE_LINE = /^!\[([^\]]*)\]\(\s*(\S+?)(?:\s+"([^"]*)")?\s*\)$/;
const BUTTON_LINE = /^\{\{\s*button\s*:\s*(.+?)\s*\|\s*(\S+?)\s*\}\}$/i;
const SUBSCRIBE_LINE = /^\{\{\s*subscribe\s*\}\}$/i;

// ---------- inline ----------

export function parseInline(src) {
  const out = [];
  const push = (text, marks) => {
    if (!text) return;
    const node = { type: "text", text };
    if (marks.length) node.marks = marks.map((m) => ({ ...m }));
    const prev = out[out.length - 1];
    if (prev && sameMarks(prev.marks, node.marks)) prev.text += text;
    else out.push(node);
  };
  walk(src, [], push);
  return out;
}

function sameMarks(a = [], b = []) {
  if (a.length !== b.length) return false;
  return a.every((m, i) => JSON.stringify(m) === JSON.stringify(b[i]));
}

function walk(s, marks, push) {
  let i = 0;
  let buf = "";
  const flush = () => { push(buf, marks); buf = ""; };
  while (i < s.length) {
    const ch = s[i];
    if (ch === "\\" && i + 1 < s.length && /[\\`*_\[\]()#+\-.!{}|>]/.test(s[i + 1])) {
      buf += s[i + 1]; i += 2; continue;
    }
    if (ch === "`") {
      const end = s.indexOf("`", i + 1);
      if (end > i) { flush(); push(s.slice(i + 1, end), [...marks, { type: "code" }]); i = end + 1; continue; }
    }
    if (ch === "[") {
      const m = matchLink(s, i);
      if (m) {
        flush();
        walk(m.text, [...marks, { type: "link", attrs: { href: m.href } }], push);
        i = m.end; continue;
      }
    }
    if ((ch === "*" || ch === "_") && s[i + 1] === ch) {
      const end = s.indexOf(ch + ch, i + 2);
      if (end > i + 2) { flush(); walk(s.slice(i + 2, end), [...marks, { type: "strong" }], push); i = end + 2; continue; }
    }
    if (ch === "*" || ch === "_") {
      const prevCh = s[i - 1];
      const wordInside = ch === "_" && prevCh && /\w/.test(prevCh);
      if (!wordInside && s[i + 1] && s[i + 1] !== " ") {
        const end = findSingle(s, ch, i + 1);
        if (end > i + 1) { flush(); walk(s.slice(i + 1, end), [...marks, { type: "em" }], push); i = end + 1; continue; }
      }
    }
    buf += ch; i++;
  }
  flush();
}

function findSingle(s, ch, from) {
  for (let j = from; j < s.length; j++) {
    if (s[j] === "\\") { j++; continue; }
    if (s[j] === ch && s[j + 1] !== ch && s[j - 1] !== " ") {
      if (ch === "_" && s[j + 1] && /\w/.test(s[j + 1])) continue;
      return j;
    }
  }
  return -1;
}

function matchLink(s, i) {
  let depth = 0;
  for (let j = i; j < s.length; j++) {
    if (s[j] === "\\") { j++; continue; }
    if (s[j] === "[") depth++;
    else if (s[j] === "]") {
      depth--;
      if (depth === 0) {
        if (s[j + 1] !== "(") return null;
        const close = s.indexOf(")", j + 2);
        if (close < 0) return null;
        const href = s.slice(j + 2, close).trim().split(/\s+/)[0];
        if (!href) return null;
        return { text: s.slice(i + 1, j), href, end: close + 1 };
      }
    }
  }
  return null;
}

// ---------- blocks ----------

export function markdownToDoc(md, { images = {} } = {}) {
  const lines = String(md || "").replace(/\r\n?/g, "\n").split("\n");
  const content = parseBlocks(lines, images);
  if (!content.length) content.push({ type: "paragraph" });
  return { type: "doc", content };
}

function isBlank(l) { return /^\s*$/.test(l); }
function bulletMatch(l) { return l.match(/^(\s*)[-*+]\s+(.*)$/); }
function orderedMatch(l) { return l.match(/^(\s*)(\d+)[.)]\s+(.*)$/); }
function isHr(l) { return /^\s*([-*_])(\s*\1){2,}\s*$/.test(l); }
function isSpecial(l) {
  return /^#{1,6}\s/.test(l) || /^\s*>/.test(l) || isHr(l) || bulletMatch(l) || orderedMatch(l) ||
    IMAGE_LINE.test(l.trim()) || BUTTON_LINE.test(l.trim()) || SUBSCRIBE_LINE.test(l.trim());
}

function parseBlocks(lines, images) {
  const out = [];
  let i = 0;
  while (i < lines.length) {
    const line = lines[i];
    const t = line.trim();
    if (isBlank(line)) { i++; continue; }

    const h = line.match(/^(#{1,6})\s+(.*?)\s*#*\s*$/);
    if (h) { out.push(withContent({ type: "heading", attrs: { level: h[1].length } }, parseInline(h[2]))); i++; continue; }

    if (isHr(line)) { out.push({ type: "horizontal_rule" }); i++; continue; }

    if (SUBSCRIBE_LINE.test(t)) {
      out.push({ type: "subscribeWidget", attrs: { url: "%%checkout_url%%", text: "Subscribe", language: "en" },
        content: [{ type: "ctaCaption", content: [{ type: "text", text: "Thanks for reading! Subscribe for free to receive new posts." }] }] });
      i++; continue;
    }
    const b = t.match(BUTTON_LINE);
    if (b) { out.push({ type: "button", attrs: { url: b[2], text: b[1], action: null, class: null } }); i++; continue; }

    const img = t.match(IMAGE_LINE);
    if (img) { out.push(imageNode(img[2], img[1], img[3], images)); i++; continue; }

    if (/^\s*>/.test(line)) {
      const inner = [];
      while (i < lines.length && /^\s*>/.test(lines[i])) { inner.push(lines[i].replace(/^\s*>\s?/, "")); i++; }
      out.push({ type: "blockquote", content: ensureBlocks(parseBlocks(inner, images)) });
      continue;
    }

    if (bulletMatch(line) || orderedMatch(line)) {
      const r = parseList(lines, i, images);
      out.push(r.node); i = r.next; continue;
    }

    const para = [];
    while (i < lines.length && !isBlank(lines[i]) && (para.length === 0 || !isSpecial(lines[i]))) {
      para.push(lines[i].trim()); i++;
    }
    out.push(withContent({ type: "paragraph" }, parseInline(joinParagraph(para))));
  }
  return out;
}

function joinParagraph(lines) {
  // A line ending in two spaces or a backslash would be a hard break in markdown;
  // Substack paragraphs have no break node in this schema, so we join with a space.
  return lines.map((l) => l.replace(/\\$/, "")).join(" ");
}

function parseList(lines, start, images) {
  const firstOrdered = !!orderedMatch(lines[start]) && !bulletMatch(lines[start]);
  const baseIndent = (firstOrdered ? orderedMatch(lines[start])[1] : bulletMatch(lines[start])[1]).length;
  const items = [];
  let i = start;
  let startNum = firstOrdered ? parseInt(orderedMatch(lines[start])[2], 10) : 1;
  while (i < lines.length) {
    const line = lines[i];
    const m = firstOrdered ? orderedMatch(line) : bulletMatch(line);
    const indent = m ? m[1].length : -1;
    if (!m || indent !== baseIndent) break;
    const body = [firstOrdered ? m[3] : m[2]];
    i++;
    while (i < lines.length) {
      const l = lines[i];
      if (isBlank(l)) {
        const nxt = lines[i + 1];
        if (nxt !== undefined && /^\s+/.test(nxt) && (nxt.match(/^\s*/)[0].length > baseIndent)) { body.push(""); i++; continue; }
        break;
      }
      const lead = l.match(/^\s*/)[0].length;
      if (lead > baseIndent) { body.push(l.slice(Math.min(lead, baseIndent + 2))); i++; continue; }
      if (isSpecial(l)) break;
      body.push(l.trim()); i++; // lazy continuation
    }
    items.push({ type: "list_item", content: ensureBlocks(parseBlocks(body, images)) });
  }
  const node = firstOrdered
    ? { type: "ordered_list", attrs: { order: startNum }, content: items }
    : { type: "bullet_list", content: items };
  return { node, next: i };
}

function ensureBlocks(blocks) { return blocks.length ? blocks : [{ type: "paragraph" }]; }
function withContent(node, content) { if (content.length) node.content = content; return node; }

function imageNode(src, alt, caption, images) {
  const up = images[src] || {};
  const node = {
    type: "captionedImage",
    content: [{
      type: "image2",
      attrs: {
        src: up.url || src, srcNoWatermark: null, fullscreen: null, imageSize: null,
        height: up.imageHeight || null, width: up.imageWidth || null, resizeWidth: null,
        bytes: up.bytes || null, alt: alt || null, title: null, type: up.contentType || null,
        href: null, belowTheFold: false, topImage: false, internalRedirect: null, isProcessing: false,
      },
    }],
  };
  if (caption) node.content.push({ type: "caption", content: parseInline(caption) });
  return node;
}

export function imageSources(md) {
  const found = [];
  for (const line of String(md || "").split(/\r?\n/)) {
    const m = line.trim().match(IMAGE_LINE);
    if (m && !found.includes(m[2])) found.push(m[2]);
  }
  return found;
}

// ---------- doc -> markdown (for reading drafts back) ----------

export function docToMarkdown(doc) {
  if (typeof doc === "string") { try { doc = JSON.parse(doc); } catch { return ""; } }
  if (!doc || !Array.isArray(doc.content)) return "";
  return blocksToMd(doc.content, "").replace(/\n{3,}/g, "\n\n").trim() + "\n";
}

function blocksToMd(blocks, prefix) {
  return blocks.map((b) => blockToMd(b, prefix)).filter((s) => s !== null).join("\n\n");
}

function blockToMd(b, prefix) {
  switch (b.type) {
    case "heading": return prefix + "#".repeat(b.attrs?.level || 2) + " " + inlineToMd(b.content);
    case "paragraph": return prefix + inlineToMd(b.content);
    case "horizontal_rule": return prefix + "---";
    case "blockquote": return blocksToMd(b.content || [], "").split("\n").map((l) => prefix + "> " + l).join("\n");
    case "bullet_list":
      return (b.content || []).map((li) => listItem(li, prefix, "- ")).join("\n");
    case "ordered_list": {
      let n = b.attrs?.order || 1;
      return (b.content || []).map((li) => listItem(li, prefix, `${n++}. `)).join("\n");
    }
    case "captionedImage": {
      const img = (b.content || []).find((c) => c.type === "image2" || c.type === "image");
      const cap = (b.content || []).find((c) => c.type === "caption");
      const src = img?.attrs?.src || "";
      const alt = img?.attrs?.alt || "";
      const capText = cap ? inlineToMd(cap.content).replace(/"/g, "'") : "";
      return prefix + `![${alt}](${src}${capText ? ` "${capText}"` : ""})`;
    }
    case "button": return prefix + `{{button: ${b.attrs?.text || ""} | ${b.attrs?.url || ""}}}`;
    case "subscribeWidget": return prefix + "{{subscribe}}";
    default: {
      if (Array.isArray(b.content)) {
        const inner = b.content.every((c) => c.type === "text") ? inlineToMd(b.content) : blocksToMd(b.content, prefix);
        return inner ? `${prefix}[${b.type}] ${inner}` : `${prefix}[${b.type}]`;
      }
      return `${prefix}[${b.type}]`;
    }
  }
}

function listItem(li, prefix, bullet) {
  const pad = " ".repeat(bullet.length);
  const md = blocksToMd(li.content || [], "");
  return md.split("\n").map((l, idx) => prefix + (idx === 0 ? bullet : (l ? pad : "")) + l).join("\n");
}

function inlineToMd(nodes = []) {
  return (nodes || []).map((n) => {
    if (n.type !== "text") return n.type === "hard_break" ? "  \n" : "";
    let t = n.text;
    const marks = n.marks || [];
    if (marks.some((m) => m.type === "code")) return "`" + t + "`";
    if (marks.some((m) => m.type === "em")) t = `*${t}*`;
    if (marks.some((m) => m.type === "strong")) t = `**${t}**`;
    const link = marks.find((m) => m.type === "link");
    if (link) t = `[${t}](${link.attrs?.href || ""})`;
    return t;
  }).join("");
}
