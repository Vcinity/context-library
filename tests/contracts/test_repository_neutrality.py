from __future__ import annotations

import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_repository_inventory_requests_tracked_files_only(monkeypatch):
    calls: list[list[str]] = []

    def capture(command, **_kwargs):
        calls.append(command)
        return b""

    monkeypatch.setattr(subprocess, "check_output", capture)
    assert repository_files() == ()
    assert calls == [["git", "ls-files", "--cached", "-z"]]


def repository_files() -> tuple[Path, ...]:
    output = subprocess.check_output(
        ["git", "ls-files", "--cached", "-z"],
        cwd=ROOT,
    )
    return tuple(path for item in output.rstrip(b"\0").split(b"\0") if item if (path := ROOT / item.decode()).is_file())


def test_repository_has_no_product_branding_or_machine_local_paths():
    blocked_terms = (
        b"vci" + b"nity",
        b"ult" + b"x",
        b"ult" + b"-x",
        b"acce" + b"lerate",
        b"key" + b"cloak",
        b"apd" + b"-",
    )
    local_prefixes = tuple(b"/" + name + b"/" for name in (b"home", b"Users", b"mnt"))
    violations: list[str] = []

    for path in repository_files():
        content = path.read_bytes().lower()
        for term in blocked_terms:
            if term.lower() in content:
                violations.append(f"{path.relative_to(ROOT)} contains blocked branding")
        for prefix in local_prefixes:
            if prefix.lower() in content:
                violations.append(f"{path.relative_to(ROOT)} contains a machine-local path")

    assert violations == []
