#!/usr/bin/env python3
import os, time, requests
import jwt  # PyJWT

GITHUB_API = os.environ.get("GITHUB_API", "https://api.github.com")
ORG  = os.environ.get("ORG",  "jai-nexus")
REPO = os.environ.get("REPO", ".github")
BRANCH = os.environ.get("BRANCH", "main")

APP_ID = os.environ["ORG_APP_ID"]               # set in Codex secrets
APP_KEY = os.environ["ORG_APP_PRIVATE_KEY"]     # full PEM, with BEGIN/END lines

def _hdr_bearer(tok: str) -> dict:
    return {"Authorization": f"Bearer {tok}", "Accept": "application/vnd.github+json",
            "User-Agent": "jai-org-control-plane"}

def _hdr_token(tok: str) -> dict:
    return {"Authorization": f"token {tok}", "Accept": "application/vnd.github+json",
            "User-Agent": "jai-org-control-plane"}

def make_app_jwt() -> str:
    now = int(time.time())
    return jwt.encode({"iat": now - 60, "exp": now + 9 * 60, "iss": APP_ID}, APP_KEY, algorithm="RS256")

def get_installation_id(app_jwt: str) -> int:
    r = requests.get(f"{GITHUB_API}/orgs/{ORG}/installation", headers=_hdr_bearer(app_jwt))
    r.raise_for_status()
    return int(r.json()["id"])

def get_installation_token(app_jwt: str, inst_id: int) -> str:
    # request Actions:write so we can dispatch workflows
    body = {"permissions": {"actions": "write", "contents": "read"}}
    r = requests.post(f"{GITHUB_API}/app/installations/{inst_id}/access_tokens",
                      headers=_hdr_bearer(app_jwt), json=body)
    r.raise_for_status()
    return r.json()["token"]

def dispatch(inst_token: str, workflow_file: str, inputs: dict):
    url = f"{GITHUB_API}/repos/{ORG}/{REPO}/actions/workflows/{workflow_file}/dispatches"
    payload = {"ref": BRANCH, "inputs": inputs}
    r = requests.post(url, headers=_hdr_token(inst_token), json=payload)
    if r.status_code >= 300:
        raise SystemExit(f"Dispatch failed {r.status_code}: {r.text}")
    print(f"✓ Dispatched {workflow_file} on {ORG}/{REPO}@{BRANCH} with inputs={inputs}")

def main():
    import argparse
    p = argparse.ArgumentParser(description="Dispatch .github workflows via GitHub App")
    sub = p.add_subparsers(dest="cmd", required=True)

    inv = sub.add_parser("inventory")
    inv.add_argument("--subset", default="")
    inv.add_argument("--issue", default="26")

    hard = sub.add_parser("harden")
    hard.add_argument("--dry-run", default="true", choices=["true","false"])
    hard.add_argument("--subset", default="")

    args = p.parse_args()

    app_jwt = make_app_jwt()
    inst_id  = get_installation_id(app_jwt)
    inst_tok = get_installation_token(app_jwt, inst_id)

    if args.cmd == "inventory":
        dispatch(inst_tok, "org_inventory.yml", {"subset": args.subset, "issue_number": str(args.issue)})
    else:
        dispatch(inst_tok, "org_hardener.yml", {"dry_run": args.dry_run, "subset": args.subset})

if __name__ == "__main__":
    main()
