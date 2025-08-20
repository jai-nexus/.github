# public-nexus — AGENTS Guide

Purpose: Serve public JSON feeds for JAI NEXUS via GitHub Pages (`/data/tasks.json`, `/data/nexus.json`).

## How to work

- Keep PRs small and scoped to JSON shape or docs in this repo.
- Do **not** add secrets or private data. Everything here is public.
- Validate JSON with `jq`. If schemas exist in `/schemas`, note any violations in the PR body.

## What to edit

- Primary: `data/tasks.json`, `data/nexus.json`
- Optional docs: `README.md`, `docs/` with a simple index to preview JSON.

## Verification

- `jq empty data/tasks.json && jq empty data/nexus.json`
- If JSON Schema exists: run the schema validator (future step).

## PR template

- What changed / Why
- Validation output (commands + results)
- Risk + rollback (usually: revert the file)
- Follow‑ups (e.g., add schema, fields, docs)
