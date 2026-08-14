#!/usr/bin/env python3
from __future__ import annotations

import argparse
import contextlib
import fcntl
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Iterator, Sequence

DEFAULT_RETRY_DELAYS = "60,120,240,480"
DEFAULT_GATE_INTERVAL = 60.0
RATE_LIMIT_MARKERS = (
    "rate limit",
    "rate-limit",
    "secondary limit",
    "abuse detection",
)


class BrokerError(RuntimeError):
    pass


def broker_root() -> Path:
    configured = os.environ.get("GITHUB_BROKER_DIR")
    return Path(configured) if configured else Path(f"/tmp/context-library-github-broker-{os.getuid()}")


def parse_retry_delays(value: str) -> list[float]:
    try:
        delays = [float(part) for part in value.split(",") if part.strip()]
    except ValueError as exc:
        raise BrokerError("retry delays must be comma-separated numbers") from exc
    if any(delay < 0 for delay in delays):
        raise BrokerError("retry delays cannot be negative")
    return delays


def validate_cache_key(key: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9._-]{1,100}", key):
        raise BrokerError("cache keys may contain only letters, numbers, dot, underscore, and hyphen")
    return key


def normalize_command(command: Sequence[str]) -> list[str]:
    normalized = list(command)
    if normalized[:1] == ["--"]:
        normalized = normalized[1:]
    if not normalized or Path(normalized[0]).name != "gh":
        raise BrokerError("brokered commands must invoke gh")
    return normalized


class GitHubBroker:
    def __init__(self, root: Path | None = None) -> None:
        self.root = root or broker_root()
        self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.lock_path = self.root / "request.lock"
        self.state_path = self.root / "state.json"
        self.cache_dir = self.root / "cache"
        self.cache_dir.mkdir(mode=0o700, exist_ok=True)

    @contextlib.contextmanager
    def request_lock(self) -> Iterator[None]:
        with self.lock_path.open("a+", encoding="utf-8") as handle:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            yield

    def cache_path(self, key: str) -> Path:
        return self.cache_dir / f"{validate_cache_key(key)}.snapshot"

    def _cache_metadata_path(self, key: str) -> Path:
        return self.cache_dir / f"{validate_cache_key(key)}.json"

    def _read_cache(self, key: str, ttl: float) -> str | None:
        if ttl <= 0:
            return None
        snapshot_path = self.cache_path(key)
        metadata_path = self._cache_metadata_path(key)
        try:
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
            created_at = float(metadata["created_at"])
            if time.time() - created_at > ttl:
                return None
            return snapshot_path.read_text(encoding="utf-8")
        except (FileNotFoundError, KeyError, TypeError, ValueError, json.JSONDecodeError):
            return None

    def _write_cache(self, key: str, output: str) -> None:
        snapshot_path = self.cache_path(key)
        metadata_path = self._cache_metadata_path(key)
        snapshot_tmp = snapshot_path.with_suffix(f".snapshot.{os.getpid()}.tmp")
        metadata_tmp = metadata_path.with_suffix(f".json.{os.getpid()}.tmp")
        snapshot_tmp.write_text(output, encoding="utf-8")
        metadata_tmp.write_text(json.dumps({"created_at": time.time()}), encoding="utf-8")
        snapshot_tmp.replace(snapshot_path)
        metadata_tmp.replace(metadata_path)

    def _read_state(self) -> dict[str, float]:
        try:
            payload = json.loads(self.state_path.read_text(encoding="utf-8"))
            return {"last_mutation": float(payload.get("last_mutation", 0.0))}
        except (FileNotFoundError, TypeError, ValueError, json.JSONDecodeError):
            return {"last_mutation": 0.0}

    def _write_state(self, state: dict[str, float]) -> None:
        temporary = self.state_path.with_suffix(f".json.{os.getpid()}.tmp")
        temporary.write_text(json.dumps(state), encoding="utf-8")
        temporary.replace(self.state_path)

    @staticmethod
    def _is_rate_limited(completed: subprocess.CompletedProcess[str]) -> bool:
        message = f"{completed.stdout}\n{completed.stderr}".lower()
        return any(marker in message for marker in RATE_LIMIT_MARKERS)

    @staticmethod
    def _retry_after(completed: subprocess.CompletedProcess[str], fallback: float) -> float:
        message = f"{completed.stdout}\n{completed.stderr}"
        match = re.search(r"retry-after[^0-9]*(\d+(?:\.\d+)?)", message, flags=re.IGNORECASE)
        return float(match.group(1)) if match else fallback

    def invoke(
        self,
        command: Sequence[str],
        *,
        mutating: bool = False,
        cache_key: str | None = None,
        cache_ttl: float = 0,
        retry_delays: Sequence[float] = (),
        mutation_interval: float = 1.0,
    ) -> subprocess.CompletedProcess[str]:
        normalized = normalize_command(command)
        if mutating and cache_key:
            raise BrokerError("mutating calls cannot use the read cache")
        if cache_key:
            validate_cache_key(cache_key)

        with self.request_lock():
            if cache_key:
                cached = self._read_cache(cache_key, cache_ttl)
                if cached is not None:
                    return subprocess.CompletedProcess(normalized, 0, cached, "")

            if mutating:
                state = self._read_state()
                delay = mutation_interval - (time.time() - state["last_mutation"])
                if delay > 0:
                    time.sleep(delay)

            attempts = [0.0, *retry_delays]
            completed: subprocess.CompletedProcess[str] | None = None
            for attempt, fallback_delay in enumerate(attempts):
                if attempt:
                    assert completed is not None
                    time.sleep(self._retry_after(completed, fallback_delay))
                completed = subprocess.run(normalized, capture_output=True, text=True, check=False)
                if completed.returncode == 0 or not self._is_rate_limited(completed):
                    break

            assert completed is not None
            if mutating:
                self._write_state({"last_mutation": time.time()})
            if completed.returncode == 0 and cache_key:
                self._write_cache(cache_key, completed.stdout)
            return completed


GATE_QUERY = """
query($itemId: ID!, $fieldName: String!) {
  node(id: $itemId) {
    ... on ProjectV2Item {
      fieldValueByName(name: $fieldName) {
        ... on ProjectV2ItemFieldSingleSelectValue {
          name
          optionId
        }
      }
    }
  }
}
""".strip()


def read_gate(
    broker: GitHubBroker,
    item_id: str,
    field_name: str,
    retry_delays: Sequence[float],
) -> dict[str, str | None]:
    completed = broker.invoke(
        [
            "gh",
            "api",
            "graphql",
            "-f",
            f"query={GATE_QUERY}",
            "-F",
            f"itemId={item_id}",
            "-F",
            f"fieldName={field_name}",
        ],
        retry_delays=retry_delays,
    )
    if completed.returncode != 0:
        raise BrokerError(completed.stderr.strip() or "GitHub gate query failed")
    try:
        payload = json.loads(completed.stdout)
        value = payload["data"]["node"]["fieldValueByName"]
    except (KeyError, TypeError, json.JSONDecodeError) as exc:
        raise BrokerError("GitHub gate query returned an invalid response") from exc
    if value is None:
        return {"gate": None, "option_id": None}
    return {"gate": value.get("name"), "option_id": value.get("optionId")}


def emit_gate(payload: dict[str, str | None], previous_gate: str | None = None) -> int:
    result = dict(payload)
    if previous_gate is not None:
        result["previous_gate"] = previous_gate
    print(json.dumps(result, sort_keys=True))
    return 0


def wait_for_gate(
    broker: GitHubBroker,
    item_id: str,
    field_name: str,
    from_state: str,
    interval: float,
    retry_delays: Sequence[float],
) -> int:
    failure_backoff = 0
    while True:
        try:
            payload = read_gate(broker, item_id, field_name, retry_delays)
            failure_backoff = 0
            gate = payload["gate"]
            if gate in {"Approved", "Changes requested"} and gate != from_state:
                return emit_gate(payload, from_state)
            time.sleep(interval)
        except BrokerError:
            delay = retry_delays[min(failure_backoff, len(retry_delays) - 1)] if retry_delays else 60.0
            failure_backoff += 1
            time.sleep(max(0.0, min(delay, 480.0)))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Serialize and cache orchestration GitHub access")
    parser.add_argument(
        "--retry-delays",
        default=os.environ.get("GITHUB_BROKER_RETRY_DELAYS", DEFAULT_RETRY_DELAYS),
        help="comma-separated rate-limit retry delays in seconds",
    )
    subparsers = parser.add_subparsers(dest="action", required=True)

    run_parser = subparsers.add_parser("run", help="run one serialized gh command")
    run_parser.add_argument("--mutating", action="store_true")
    run_parser.add_argument("--cache-key")
    run_parser.add_argument("--cache-ttl", type=float, default=0)
    run_parser.add_argument("--mutation-interval", type=float, default=1.0)
    run_parser.add_argument("command", nargs=argparse.REMAINDER)

    cache_parser = subparsers.add_parser("cache-path", help="print the snapshot path for a cache key")
    cache_parser.add_argument("key")

    gate_parser = subparsers.add_parser("gate", help="read one Project Spec Gate field")
    gate_parser.add_argument("--item-id", required=True)
    gate_parser.add_argument("--field-name", default="Spec Gate")

    wait_parser = subparsers.add_parser("wait-gate", help="quietly wait for an approval gate transition")
    wait_parser.add_argument("--item-id", required=True)
    wait_parser.add_argument("--field-name", default="Spec Gate")
    wait_parser.add_argument("--from-state", default="Awaiting approval")
    wait_parser.add_argument("--interval", type=float, default=DEFAULT_GATE_INTERVAL)
    return parser


def emit_completed(completed: subprocess.CompletedProcess[str]) -> int:
    if completed.stdout:
        sys.stdout.write(completed.stdout)
    if completed.stderr:
        sys.stderr.write(completed.stderr)
    return completed.returncode


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        retry_delays = parse_retry_delays(args.retry_delays)
        broker = GitHubBroker()
        if args.action == "run":
            completed = broker.invoke(
                args.command,
                mutating=args.mutating,
                cache_key=args.cache_key,
                cache_ttl=args.cache_ttl,
                retry_delays=retry_delays,
                mutation_interval=args.mutation_interval,
            )
            return emit_completed(completed)
        if args.action == "cache-path":
            print(broker.cache_path(args.key))
            return 0
        if args.action == "gate":
            return emit_gate(read_gate(broker, args.item_id, args.field_name, retry_delays))
        if args.action == "wait-gate":
            if args.interval < 0:
                raise BrokerError("gate interval cannot be negative")
            return wait_for_gate(
                broker,
                args.item_id,
                args.field_name,
                args.from_state,
                args.interval,
                retry_delays,
            )
        raise BrokerError("unknown broker action")
    except BrokerError as exc:
        print(f"github-broker: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
