#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_MARKETPLACE_PATH = ".agents/plugins/marketplace.json"
DEFAULT_PLUGIN_PATH = "plugins/context-library"


def fail(message: str) -> None:
    print(f"ERROR: {message}", file=sys.stderr)
    raise SystemExit(1)


def git_output(*args: str) -> str:
    try:
        return subprocess.check_output(["git", *args], cwd=ROOT, text=True).strip()
    except subprocess.CalledProcessError as exc:
        fail(f"git {' '.join(args)} failed with exit code {exc.returncode}")


def maybe_git_output(*args: str) -> str | None:
    try:
        return subprocess.check_output(
            ["git", *args],
            cwd=ROOT,
            text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
    except subprocess.CalledProcessError:
        return None


def parse_remote(remote: str) -> tuple[str | None, str, str | None]:
    http_match = re.fullmatch(r"(https?://[^/]+)/(.+?)(?:\.git)?", remote)
    if http_match:
        return None, http_match.group(2), http_match.group(1)

    ssh_url_match = re.fullmatch(r"ssh://(?:[^@]+@)?([^/]+)/(.+?)(?:\.git)?", remote)
    if ssh_url_match:
        return ssh_url_match.group(1), ssh_url_match.group(2), None

    scp_match = re.fullmatch(r"(?:[^@]+@)?([^:]+):(.+?)(?:\.git)?", remote)
    if scp_match:
        return scp_match.group(1), scp_match.group(2), None

    fail(f"unsupported remote format: {remote}")


def join_url(base: str, suffix: str) -> str:
    return f"{base.rstrip('/')}/{suffix.lstrip('/')}"


def load_marketplace_name(path: Path) -> str:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        fail(f"unable to read marketplace manifest {path}: {exc}")
    except json.JSONDecodeError as exc:
        fail(f"invalid marketplace manifest {path}: {exc}")
    name = payload.get("name")
    if not isinstance(name, str) or not name.strip():
        fail(f"marketplace manifest {path} must define a non-empty name")
    return name


def main() -> None:
    parser = argparse.ArgumentParser(description="Print repository and plugin URLs derived from origin.")
    parser.add_argument("--remote", default="origin", help="Git remote name to inspect (default: origin)")
    parser.add_argument(
        "--branch",
        default=maybe_git_output("rev-parse", "--abbrev-ref", "HEAD") or "main",
        help="Branch name for raw URLs (default: current branch)",
    )
    parser.add_argument(
        "--web-base",
        help="Optional HTTP(S) base for SSH-style remotes, for example https://git.example.com",
    )
    parser.add_argument(
        "--marketplace-path",
        default=DEFAULT_MARKETPLACE_PATH,
        help=f"Marketplace path relative to repo root (default: {DEFAULT_MARKETPLACE_PATH})",
    )
    parser.add_argument(
        "--plugin-path",
        default=DEFAULT_PLUGIN_PATH,
        help=f"Plugin path relative to repo root (default: {DEFAULT_PLUGIN_PATH})",
    )
    args = parser.parse_args()

    marketplace_abs_path = (ROOT / args.marketplace_path).resolve()
    marketplace_root = ROOT.resolve()
    marketplace_name = load_marketplace_name(marketplace_abs_path)
    print(f"Plugin path: {args.plugin_path}")
    print(f"Marketplace path: {args.marketplace_path}")
    print(f"Marketplace name: {marketplace_name}")
    print(f"Marketplace root: {marketplace_root}")
    print("Local install commands:")
    print(f"  codex plugin marketplace add {marketplace_root}")
    print(f"  codex plugin add context-library@{marketplace_name}")
    print("  Start a new Codex thread to pick up the updated plugin.")

    remote = maybe_git_output("remote", "get-url", args.remote)
    if not remote:
        print("Web URL: unavailable")
        print("Note: no git remote is configured yet for this repository.")
        return

    _, repo_path, remote_web_base = parse_remote(remote)
    print(f"Remote: {remote}")
    print(f"Repo path: {repo_path}")

    web_base = args.web_base or remote_web_base
    if not web_base:
        print("Web URL: unavailable")
        print(
            "Note: the remote does not include an HTTP(S) browser base. Re-run with "
            "--web-base http://<gitlab-host> or --web-base https://<gitlab-host> "
            "to print browser/raw URLs."
        )
        return

    repo_url = join_url(web_base, repo_path)
    plugin_url = join_url(repo_url, args.plugin_path)
    marketplace_raw_url = join_url(repo_url, f"-/raw/{args.branch}/{args.marketplace_path}")
    plugin_manifest_raw_url = join_url(
        repo_url,
        f"-/raw/{args.branch}/{args.plugin_path}/.codex-plugin/plugin.json",
    )

    print(f"Web URL: {repo_url}")
    print(f"Plugin URL: {plugin_url}")
    print(f"Marketplace raw URL: {marketplace_raw_url}")
    print(f"Plugin manifest raw URL: {plugin_manifest_raw_url}")


if __name__ == "__main__":
    main()
