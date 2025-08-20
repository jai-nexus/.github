#!/usr/bin/env node
// Node 20+ (global fetch). Produces out/tasks.json
import fs from "node:fs/promises";
import path from "node:path";

const ORG          = process.env.ORG || "jai-nexus";
const SUBSET_RAW   = (process.env.SUBSET || "").trim();
const SUBSET       = SUBSET_RAW ? new Set(SUBSET_RAW.split(",").map(s => s.trim())) : null;

const GH_TOKEN     = process.env.GH_TOKEN;                  // required (installation token)
const NOTION_TOKEN = process.env.NOTION_TOKEN || "";        // optional
const NOTION_DB    = process.env.NOTION_TASKS_DB || "";     // optional
const NOTION_VER   = "2022-06-28";

function ensure(val, name) {
  if (!val) { console.error(`Missing ${name}`); process.exit(2); }
}
ensure(GH_TOKEN, "GH_TOKEN");

// --------------------------- GitHub -----------------------------------------
const GH = "https://api.github.com";

async function gh_get(url) {
  const r = await fetch(url, {
    headers: {
      "Authorization": `token ${GH_TOKEN}`,
      "Accept": "application/vnd.github+json",
      "User-Agent": "jai-org-tasks"
    }
  });
  if (!r.ok) throw new Error(`GitHub ${r.status} ${url}: ${await r.text()}`);
  return r.json();
}

function parsePoints(labels = []) {
  // Look for p:3, points:5, "5 points"
  for (const l of labels) {
    const s = (typeof l === "string" ? l : l.name || "").toLowerCase();
    let m = s.match(/\bp[:= -]?(\d+)\b/);
    if (!m) m = s.match(/\bpoints?[:= -]?(\d+)\b/);
    if (!m) m = s.match(/\b(\d+)\s*points?\b/);
    if (m) return Math.max(1, parseInt(m[1], 10));
  }
  return 1;
}

function guessType(labels = [], fallback) {
  const names = labels.map(l => (typeof l === "string" ? l : l.name || "")?.toLowerCase());
  if (names.some(n => /bug|fix|defect/.test(n))) return "bug";
  if (names.some(n => /doc|docs/.test(n))) return "docs";
  if (names.some(n => /feat|feature|enhancement/.test(n))) return "feature";
  if (names.some(n => /infra|ops|devops/.test(n))) return "infra";
  if (names.some(n => /task|chore/.test(n))) return "task";
  return fallback || "task";
}

function takeTags(labels = []) {
  // Keep short, friendly label names (skip points/type-ish)
  return labels
    .map(l => (typeof l === "string" ? l : l.name || ""))
    .filter(Boolean)
    .filter(n => !/\bp[:= -]?\d+\b/.test(n.toLowerCase()))
    .filter(n => !/\bpoints?[:= -]?\d+\b/.test(n.toLowerCase()))
    .slice(0, 6);
}

function repoFromUrl(repository_url) {
  // https://api.github.com/repos/<owner>/<repo> -> repo
  const parts = repository_url.split("/");
  return parts.slice(-1)[0];
}

async function fetchGithubTasks() {
  // One search for open issues, one for open PRs (both across the org).
  // We cap at 200 each via pagination.
  const all = [];

  async function search(q) {
    const per_page = 100;
    for (let page = 1; page <= 2; page++) {
      const data = await gh_get(`${GH}/search/issues?q=${encodeURIComponent(q)}&per_page=${per_page}&page=${page}`);
      if (!data.items?.length) break;
      all.push(...data.items);
      if (data.items.length < per_page) break;
    }
  }

  await search(`org:${ORG} is:issue state:open archived:false`);
  await search(`org:${ORG} is:pr    state:open archived:false`);

  const tasks = all.map(it => {
    const repo = repoFromUrl(it.repository_url);
    const labels = it.labels || [];
    const isPR = !!it.pull_request;
    const type = guessType(labels, isPR ? "pr" : "issue");
    const points = parsePoints(labels);
    const status = it.state === "closed" ? "done" : "open";
    const tags = takeTags(labels);
    return {
      source: "github",
      type,
      repo,
      number: it.number,
      title: it.title,
      url: it.html_url,
      points,
      tags,
      status,
      progress: status === "done" ? 1 : 0
    };
  });

  // Optional filter by repo subset
  return SUBSET ? tasks.filter(t => SUBSET.has(t.repo)) : tasks;
}

// --------------------------- Notion -----------------------------------------
async function notion_post(url, body) {
  const r = await fetch(url, {
    method: "POST",
    headers: {
      "Authorization": `Bearer ${NOTION_TOKEN}`,
      "Notion-Version": NOTION_VER,
      "Content-Type": "application/json"
    },
    body: JSON.stringify(body || {})
  });
  if (!r.ok) throw new Error(`Notion ${r.status} ${url}: ${await r.text()}`);
  return r.json();
}

function prop(p, name) {
  const v = p?.[name];
  if (!v || !v.type) return undefined;
  switch (v.type) {
    case "title":       return v.title.map(t => t.plain_text).join("").trim();
    case "rich_text":   return v.rich_text.map(t => t.plain_text).join("").trim();
    case "url":         return v.url || undefined;
    case "select":      return v.select?.name;
    case "status":      return v.status?.name;
    case "multi_select":return v.multi_select?.map(o => o.name) || [];
    case "number":      return v.number ?? undefined;
    case "checkbox":    return !!v.checkbox;
    default:            return undefined;
  }
}

function normRepo(val) {
  if (!val) return undefined;
  if (Array.isArray(val)) return val[0];
  return String(val);
}

function normProgress(v) {
  if (v == null) return undefined;
  if (v > 1.001) return Math.max(0, Math.min(1, v / 100)); // percent -> 0..1
  return Math.max(0, Math.min(1, v));
}

async function fetchNotionTasks() {
  if (!NOTION_TOKEN || !NOTION_DB) return [];

  const tasks = [];
  let cursor = undefined;

  while (true) {
    const body = { page_size: 100, start_cursor: cursor };
    const data = await notion_post(`https://api.notion.com/v1/databases/${NOTION_DB}/query`, body);
    for (const page of (data.results || [])) {
      const p = page.properties || {};
      const title   = prop(p, "Name") || "Untitled";
      const statusN = (prop(p, "Status") || prop(p, "status") || "").toString().toLowerCase();
      const done    = ["done", "shipped", "complete", "closed", "✅"].includes(statusN);
      const repo    = normRepo(prop(p, "Repo") || prop(p, "Repository"));
      const number  = prop(p, "Number") || undefined;
      const url     = prop(p, "URL") || prop(p, "Link") || page.url;
      const type    = (prop(p, "Type") || "task").toString().toLowerCase();
      const tags    = prop(p, "Tags") || [];
      const points  = prop(p, "Points") || prop(p, "pts") || 1;
      const prog    = normProgress(prop(p, "Progress"));

      tasks.push({
        source: "notion",
        type,
        repo,
        number,
        title,
        url,
        points: Math.max(1, Number(points) || 1),
        tags: Array.isArray(tags) ? tags.slice(0, 6) : [],
        status: done ? "done" : "open",
        progress: prog ?? (done ? 1 : 0)
      });
    }
    if (!data.has_more) break;
    cursor = data.next_cursor;
  }

  // Optional filter by repo subset; if Notion tasks have no repo, keep them.
  return SUBSET ? tasks.filter(t => !t.repo || SUBSET.has(t.repo)) : tasks;
}

// --------------------------- Main -------------------------------------------
(async () => {
  const [gh, notion] = await Promise.all([
    fetchGithubTasks().catch(e => { console.warn("GitHub fetch failed:", e.message); return []; }),
    fetchNotionTasks().catch(e => { console.warn("Notion fetch failed:", e.message); return []; })
  ]);

  const tasks = [...gh, ...notion];

  const totals = {
    open: tasks.filter(t => t.status !== "done").length,
    done: tasks.filter(t => t.status === "done").length,
    xp:   tasks.filter(t => t.status === "done").reduce((s, t) => s + (t.points || 0), 0)
  };

  await fs.mkdir("out", { recursive: true });
  const payload = { generatedAt: new Date().toISOString(), totals, tasks };
  await fs.writeFile(path.join("out", "tasks.json"), JSON.stringify(payload, null, 2), "utf8");

  console.log(`✅ Aggregated ${tasks.length} task(s) → out/tasks.json (open:${totals.open}, done:${totals.done}, xp:${totals.xp})`);
})().catch(err => {
  console.error(err);
  process.exit(1);
});
