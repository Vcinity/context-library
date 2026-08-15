from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
BROKER = ROOT / "scripts" / "github_broker.py"


@pytest.fixture
def broker_env(tmp_path: Path) -> dict[str, str]:
    binary_dir = tmp_path / "bin"
    binary_dir.mkdir()
    fake_gh = binary_dir / "gh"
    fake_gh.write_text(
        """#!/usr/bin/env python3
import json
import os
from pathlib import Path
import sys
import time

mode = os.environ["FAKE_GH_MODE"]
state_path = Path(os.environ["FAKE_GH_STATE"])
log_path = Path(os.environ["FAKE_GH_LOG"])

if mode == "serialize":
    with log_path.open("a", encoding="utf-8") as handle:
        handle.write(f"start {sys.argv[-1]}\\n")
    time.sleep(0.15)
    with log_path.open("a", encoding="utf-8") as handle:
        handle.write(f"end {sys.argv[-1]}\\n")
    print(sys.argv[-1])
elif mode == "count":
    count = int(state_path.read_text() if state_path.exists() else "0") + 1
    state_path.write_text(str(count))
    print(f"snapshot-{count}")
elif mode == "retry":
    count = int(state_path.read_text() if state_path.exists() else "0") + 1
    state_path.write_text(str(count))
    if count == 1:
        print("secondary rate limit; retry-after: 0", file=sys.stderr)
        raise SystemExit(1)
    print("recovered")
elif mode == "mutate":
    with log_path.open("a", encoding="utf-8") as handle:
        handle.write(f"{time.time()}\\n")
    print("updated")
elif mode == "gate":
    state = state_path.read_text(encoding="utf-8") if state_path.exists() else "Awaiting approval"
    print(json.dumps({"data": {"node": {"fieldValueByName": {"name": state, "optionId": "fixture-option"}}}}))
else:
    raise SystemExit(f"unknown fake mode: {mode}")
""",
        encoding="utf-8",
    )
    fake_gh.chmod(0o755)
    return {
        **os.environ,
        "PATH": f"{binary_dir}{os.pathsep}{os.environ['PATH']}",
        "GITHUB_BROKER_DIR": str(tmp_path / "broker"),
        "FAKE_GH_MODE": "count",
        "FAKE_GH_STATE": str(tmp_path / "state"),
        "FAKE_GH_LOG": str(tmp_path / "gh.log"),
    }


def broker_command(*args: str) -> list[str]:
    return [sys.executable, str(BROKER), "--retry-delays", "0", *args]


def run_broker(env: dict[str, str], *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        broker_command(*args),
        capture_output=True,
        check=False,
        env=env,
        text=True,
    )


def test_concurrent_commands_are_serialized(broker_env: dict[str, str]) -> None:
    broker_env["FAKE_GH_MODE"] = "serialize"
    first = subprocess.Popen(
        broker_command("run", "--", "gh", "first"),
        env=broker_env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    second = subprocess.Popen(
        broker_command("run", "--", "gh", "second"),
        env=broker_env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )

    assert first.communicate(timeout=3)[0].strip() == "first"
    assert second.communicate(timeout=3)[0].strip() == "second"
    lines = Path(broker_env["FAKE_GH_LOG"]).read_text(encoding="utf-8").splitlines()
    assert lines in (
        ["start first", "end first", "start second", "end second"],
        ["start second", "end second", "start first", "end first"],
    )


def test_cached_read_reuses_raw_snapshot(broker_env: dict[str, str]) -> None:
    arguments = ("run", "--cache-key", "issue-5", "--cache-ttl", "60", "--", "gh", "issue", "view", "5")

    first = run_broker(broker_env, *arguments)
    second = run_broker(broker_env, *arguments)
    cache_path = run_broker(broker_env, "cache-path", "issue-5")

    assert first.returncode == second.returncode == cache_path.returncode == 0
    assert first.stdout == second.stdout == "snapshot-1\n"
    assert Path(cache_path.stdout.strip()).read_text(encoding="utf-8") == "snapshot-1\n"
    assert Path(broker_env["FAKE_GH_STATE"]).read_text() == "1"


def test_rate_limit_failure_is_retried_by_broker(broker_env: dict[str, str]) -> None:
    broker_env["FAKE_GH_MODE"] = "retry"

    result = run_broker(broker_env, "run", "--", "gh", "api", "rate_limit")

    assert result.returncode == 0
    assert result.stdout == "recovered\n"
    assert Path(broker_env["FAKE_GH_STATE"]).read_text() == "2"


def test_mutations_are_spaced(broker_env: dict[str, str]) -> None:
    broker_env["FAKE_GH_MODE"] = "mutate"
    arguments = ("run", "--mutating", "--mutation-interval", "0.2", "--", "gh", "issue", "edit", "5")

    assert run_broker(broker_env, *arguments).returncode == 0
    assert run_broker(broker_env, *arguments).returncode == 0
    timestamps = [float(value) for value in Path(broker_env["FAKE_GH_LOG"]).read_text().splitlines()]
    assert timestamps[1] - timestamps[0] >= 0.17


def test_rejects_unbrokered_executable_and_cached_mutation(broker_env: dict[str, str]) -> None:
    not_gh = run_broker(broker_env, "run", "--", "curl", "https://example.invalid")
    cached_mutation = run_broker(
        broker_env,
        "run",
        "--mutating",
        "--cache-key",
        "bad",
        "--",
        "gh",
        "issue",
        "edit",
        "5",
    )

    assert not_gh.returncode == 2
    assert "must invoke gh" in not_gh.stderr
    assert cached_mutation.returncode == 2
    assert "mutating calls cannot use" in cached_mutation.stderr


def test_broker_help_exposes_bounded_surface(broker_env: dict[str, str]) -> None:
    result = subprocess.run(
        [sys.executable, str(BROKER), "--help"],
        capture_output=True,
        check=False,
        env=broker_env,
        text=True,
    )

    assert result.returncode == 0
    assert "run" in result.stdout
    assert "cache-path" in result.stdout
    assert "wait-gate" not in result.stdout
    assert "gate" not in result.stdout
