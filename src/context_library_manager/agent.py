from __future__ import annotations

import json
import os
import selectors
import signal
import subprocess
import time
from dataclasses import dataclass
from typing import Callable

from .domain import AgentRequest, AgentResponse
from .security import contains_secret, filter_confidential_value, redact_text


@dataclass
class AgentResult:
    status: str
    payload: dict
    input_tokens: int
    output_tokens: int
    confidence: float | None = None


class AgentCancelled(RuntimeError):
    pass


def _redact_text(value: str) -> str:
    return redact_text(value)


def redact_evidence(evidence: list[dict]) -> list[dict[str, str]]:
    """Keep only bounded citation fields before crossing the agent boundary."""
    redacted: list[dict[str, str]] = []
    for item in evidence:
        if not isinstance(item, dict):
            continue
        allowed = {}
        for key in ("observation_id", "location", "uri", "excerpt", "timestamp"):
            value = item.get(key)
            if value is not None:
                allowed[key] = _redact_text(str(value))
        if allowed:
            redacted.append(allowed)
    return redacted[:20]


def redact_provider_value(value):
    """Recursively remove secret-bearing fields before crossing the provider boundary."""
    return filter_confidential_value(value)


def provider_value_contains_secret(value) -> bool:
    return contains_secret(value)


def _signal_process_group(process: subprocess.Popen, sig: signal.Signals) -> None:
    try:
        if os.name == "posix":
            os.killpg(process.pid, sig)
        elif process.poll() is None:
            process.terminate() if sig == signal.SIGTERM else process.kill()
    except ProcessLookupError:
        pass


def _stop_process_group(process: subprocess.Popen, *, graceful: bool) -> None:
    _signal_process_group(process, signal.SIGTERM if graceful else signal.SIGKILL)
    if process.poll() is None:
        try:
            process.wait(timeout=2 if graceful else 0.5)
        except subprocess.TimeoutExpired:
            _signal_process_group(process, signal.SIGKILL)
            process.wait(timeout=2)
    if graceful:
        _signal_process_group(process, signal.SIGKILL)


def invoke(
    command: list[str],
    request: dict,
    timeout: int = 60,
    *,
    cancellation_requested: Callable[[], bool] | None = None,
    max_output_bytes: int = 1_000_000,
) -> AgentResult:
    process = subprocess.Popen(
        command,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=os.name == "posix",
    )
    assert process.stdin and process.stdout and process.stderr
    process.stdin.write(json.dumps(request).encode())
    process.stdin.close()
    buffers = {"stdout": bytearray(), "stderr": bytearray()}
    selector = selectors.DefaultSelector()
    for name, stream in (("stdout", process.stdout), ("stderr", process.stderr)):
        os.set_blocking(stream.fileno(), False)
        selector.register(stream, selectors.EVENT_READ, name)

    def drain_ready(wait: float) -> None:
        for key, _ in selector.select(wait):
            stream = key.fileobj
            while True:
                try:
                    chunk = os.read(stream.fileno(), 65536)
                except BlockingIOError:
                    break
                if not chunk:
                    try:
                        selector.unregister(stream)
                    except KeyError:
                        pass
                    break
                remaining = max_output_bytes - len(buffers[key.data])
                if remaining > 0:
                    buffers[key.data].extend(chunk[:remaining])

    deadline = time.monotonic() + timeout
    canceled = False
    timed_out = False
    while True:
        drain_ready(0.05)
        if process.poll() is not None:
            _signal_process_group(process, signal.SIGTERM)
            drain_deadline = time.monotonic() + 0.25
            while selector.get_map() and time.monotonic() < drain_deadline:
                drain_ready(0.01)
            _signal_process_group(process, signal.SIGKILL)
            break
        if cancellation_requested and cancellation_requested():
            canceled = True
            _stop_process_group(process, graceful=True)
            drain_ready(0)
            break
        if time.monotonic() >= deadline:
            _stop_process_group(process, graceful=False)
            drain_ready(0)
            timed_out = True
            break
    selector.close()
    stdout = buffers["stdout"].decode("utf-8", errors="replace")
    stderr = buffers["stderr"].decode("utf-8", errors="replace")
    process.stdout.close()
    process.stderr.close()
    if timed_out:
        raise TimeoutError(f"agent exceeded {timeout}s timeout")
    if canceled:
        raise AgentCancelled("agent cancellation acknowledged")
    if process.returncode:
        raise RuntimeError(f"agent exited {process.returncode}: {_redact_text(stderr[-1000:])}")
    try:
        response = AgentResponse.model_validate_json(stdout)
    except Exception as exc:
        raise ValueError("agent response schema is invalid") from exc
    if response.run_id != request.get("run_id"):
        raise ValueError("agent response schema or run_id is invalid")
    if provider_value_contains_secret(response.result) or provider_value_contains_secret(response.warnings):
        raise ValueError("agent response contains a secret pattern")
    usage = response.usage
    request_model = AgentRequest.model_validate(request)
    if (
        usage.input_tokens > request_model.budget.max_input_tokens
        or usage.output_tokens > request_model.budget.max_output_tokens
    ):
        raise ValueError("agent response exceeded its token budget")
    return AgentResult(
        response.status,
        response.result,
        usage.input_tokens,
        usage.output_tokens,
        response.confidence,
    )
