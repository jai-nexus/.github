#!/usr/bin/env python3
"""
JAI Nexus — Workflow dispatcher via GitHub App

Environment:
  ORG_APP_ID              -> your GitHub App ID (int)
  ORG_APP_PRIVATE_KEY     -> full PEM (BEGIN/END lines included)
  ORG                     -> org login (default: "jai-nexus")
  REPO                    -> repo name hosting workflows (default: ".github")
  BRANCH                  -> ref for dispatch (default: "main")
  GITHUB_API              -> override API base (default: "https://api.github.com")
  DEBUG_DNS               -> set to "1" to print Python DNS/HTTP sanity info

Examples:
  python3 codex/dispatch.py check
  python3 codex/dispatch.py tasks --publish true --subset ""
  python3 codex/dispatch.py inventory --subset "" --issue ""
  python3 codex/dispatch.py harden --dry-run true --subset "agency-nexus,modelops-nexus"
"""
from __future__ import annotations

import os
import socket
import time
from typing import Dict, Any, Iterable

import requests
import jwt  # PyJWT

# --------------------------------------------------------------------------- #
# Config                                                                      #
# --------------------------------------------------------------------------- #
GITHUB_API = os.environ.get("GITHUB_API", "https://api.github.com")
ORG   = os.environ.get("ORG",  "jai-nexus")
REPO  = os.environ.get("REPO", ".github")
BRANCH = os.environ.get("BRANCH", "main")

APP_ID = int(os.environ["ORG_APP_ID"])       # required
APP_KEY = os.environ["ORG_APP_PRIVATE_KEY"]  # required (full PEM)

_UA = "jai-org-control-plane/1.2"
_TIMEOUT = 20  # seconds for HTTP requests

# --------------------------------------------------------------------------- #
# small helpers                                                               #
# --------------------------------------------------------------------------- #
def _hdr_bearer(tok: str) -> dict:
    return {
        "Authorization": f"Bearer {tok}",
        "Accept": "application/vnd.github+json",
        "User-Agent": _UA,
    }

def _hdr_token(tok: str) -> dict:
    return {
        "Authorization": f"token {tok}",
        "Accept": "application/vnd.github+json",
        "User-Agent": _UA,
    }

def _join_str(items: Iterable[object]) -> str:
    """Safe join for mixed types (keeps Pylance happy too)."""
    return ", ".join(map(str, items))

def _maybe_dns_probe() -> None:
    """Optional: print what Python resolves & a quick /meta call."""
    if os.environ.get("DEBUG_DNS") != "1":
        return
    host = "api.github.com"
    try:
        name, aliases, ips = socket.gethostbyname_ex(host)
        print(f"[dns] gethostbyname_ex({host}) -> name={name}, aliases={aliases}, ips={_join_str(sorted(ips))}")
    except Exception as e:
        print(f"[dns] resolution failed: {e}")

    try:
        r = requests.get(f"{GITHUB_API}/meta", timeout=_TIMEOUT)
        print(f"[dns] GET /meta -> {r.status_code}")
    except Exception as e:
        print(f"[dns] HTTP to {GITHUB_API} failed: {e}")

# --------------------------------------------------------------------------- #
# App/installation token flow                                                 #
# --------------------------------------------------------------------------- #
def make_app_jwt() -> str:
    now = int(time.time())
    return jwt.encode({"iat": now - 60, "exp": now + 9 * 60, "iss": APP_ID},
                      APP_KEY, algorithm="RS256")

def get_installation_id(app_jwt: str) -> int:
    _maybe_dns_probe()
    r = requests.get(
        f"{GITHUB_API}/orgs/{ORG}/installation",
        headers=_hdr_bearer(app_jwt),
        timeout=_TIMEOUT,
    )
    if r.status_code == 404:
        raise SystemExit(
            f"App is not installed on org '{ORG}'. Install it on **All repositories** and re-run."
        )
    r.raise_for_status()
    return int(r.json()["id"])

def get_installation_token(app_jwt: str, inst_id: int) -> str:
    url = f"{GITHUB_API}/app/installations/{inst_id}/access_tokens"
    hdr = _hdr_bearer(app_jwt)

    # Ask for Actions:write and contents:read so we can dispatch workflows.
    body = {"permissions": {"actions": "write", "contents": "read"}}
    r = requests.post(url, headers=hdr, json=body, timeout=_TIMEOUT)
    if r.status_code == 422:  # fall back to whatever is already granted
        r = requests.post(url, headers=hdr, timeout=_TIMEOUT)

    if r.status_code >= 300:
        raise SystemExit(f"Failed to create installation token {r.status_code}: {r.text}")

    return r.json()["token"]

# --------------------------------------------------------------------------- #
# Workflow helpers                                                            #
# --------------------------------------------------------------------------- #
def list_workflows(inst_token: str) -> Dict[str, Any]:
    url = f"{GITHUB_API}/repos/{ORG}/{REPO}/actions/workflows"
    r = requests.get(url, headers=_hdr_token(inst_token), timeout=_TIMEOUT)
    if r.status_code >= 300:
        raise SystemExit(f"List workflows failed {r.status_code}: {r.text}")
    return r.json()

def dispatch(inst_token: str, workflow_file: str, inputs: Dict[str, str]):
    url = f"{GITHUB_API}/repos/{ORG}/{REPO}/actions/workflows/{workflow_file}/dispatches"
    payload = {"ref": BRANCH, "inputs": inputs}
    r = requests.post(url, headers=_hdr_token(inst_token), json=payload, timeout=_TIMEOUT)
    if r.status_code >= 300:
        raise SystemExit(f"Dispatch failed {r.status_code}: {r.text}")
    print(f"✓ Dispatched {workflow_file} to {ORG}/{REPO}@{BRANCH} with inputs={inputs}")

# --------------------------------------------------------------------------- #
# CLI                                                                         #
# --------------------------------------------------------------------------- #
def main():
    import argparse

    p = argparse.ArgumentParser(description="Dispatch .github workflows via GitHub App")
    sub = p.add_subparsers(dest="cmd", required=True)

    # org_tasks.yml
    t = sub.add_parser("tasks", help="Run .github/workflows/org_tasks.yml")
    t.add_argument("--publish", default="true", choices=["true", "false"],
                   help="publish to public-nexus (default: true)")
    t.add_argument("--subset",  default="", help="comma-separated repo names (optional)")

    # org_inventory.yml
    inv = sub.add_parser("inventory", help="Run .github/workflows/org_inventory.yml")
    inv.add_argument("--subset", default="", help="comma-separated repo names")
    inv.add_argument("--issue",  default="", help="issue number in jai-nexus/jai-nexus (optional)")

    # org_hardener.yml
    hard = sub.add_parser("harden", help="Run .github/workflows/org_hardener.yml")
    hard.add_argument("--dry-run", default="true", choices=["true", "false"])
    hard.add_argument("--subset",  default="", help="comma-separated repo names")

    # sanity
    sub.add_parser("check", help="Verify access and list available workflows")

    args = p.parse_args()

    app_jwt = make_app_jwt()
    inst_id = get_installation_id(app_jwt)
    inst_tok = get_installation_token(app_jwt, inst_id)

    if args.cmd == "check":
        data = list_workflows(inst_tok)
        # make sure every element is a string before joining (appeases type checkers)
        names = [str(w.get("name") or w.get("path") or "") for w in data.get("workflows", [])]
        print(f"Workflows ({len(names)}): {_join_str(names)}")
        return

    if args.cmd == "tasks":
        inputs = {"publish": args.publish, "subset": args.subset}
        dispatch(inst_tok, "org_tasks.yml", inputs)
    elif args.cmd == "inventory":
        inputs = {"subset": args.subset, "issue_number": str(args.issue)}
        dispatch(inst_tok, "org_inventory.yml", inputs)
    else:  # harden
        inputs = {"dry_run": args.dry_run, "subset": args.subset}
        dispatch(inst_tok, "org_hardener.yml", inputs)

if __name__ == "__main__":
    main()
