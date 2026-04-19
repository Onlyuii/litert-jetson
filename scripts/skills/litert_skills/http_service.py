from __future__ import annotations

import json
import time
from dataclasses import dataclass
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Callable

from .agent import AgentConfig, LiteRTSkillAgent, SkillRegistry


def _json_bytes(payload: object) -> bytes:
    return json.dumps(payload, ensure_ascii=False).encode("utf-8")


@dataclass(frozen=True)
class SkillServiceConfig:
    bind: str
    port: int
    api_key: str
    agent_config: AgentConfig


class LiteRTSkillHTTPServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, server_address: tuple[str, int], config: SkillServiceConfig, registry: SkillRegistry):
        self.config = config
        self.registry = registry
        self.agent = LiteRTSkillAgent(config.agent_config, registry)
        self.started_at = time.time()
        super().__init__(server_address, LiteRTSkillRequestHandler)

    def server_close(self) -> None:
        try:
            self.agent.close()
        finally:
            super().server_close()


class LiteRTSkillRequestHandler(BaseHTTPRequestHandler):
    server: LiteRTSkillHTTPServer

    def log_message(self, fmt: str, *args: object) -> None:
        print(
            f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {self.client_address[0]} "
            f"{self.command} {self.path} - {fmt % args}",
            flush=True,
        )

    def do_OPTIONS(self) -> None:
        self.send_response(HTTPStatus.NO_CONTENT)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Authorization, Content-Type")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.end_headers()

    def do_GET(self) -> None:
        path = self.path.split("?", 1)[0]
        if path in {"/", "/health", "/healthz"}:
            self._handle_health()
            return
        if path == "/skills":
            if not self._check_auth():
                return
            self._send_json(
                HTTPStatus.OK,
                {
                    "object": "list",
                    "data": self.server.registry.prompt_catalog(),
                },
            )
            return
        self._send_error(HTTPStatus.NOT_FOUND, "Unknown endpoint.")

    def do_POST(self) -> None:
        path = self.path.split("?", 1)[0]
        if path == "/run":
            if not self._check_auth():
                return
            self._handle_run()
            return
        if path == "/run_live":
            if not self._check_auth():
                return
            self._handle_run_live()
            return
        if path == "/reset":
            if not self._check_auth():
                return
            self._handle_reset()
            return
        self._send_error(HTTPStatus.NOT_FOUND, "Unknown endpoint.")

    def _handle_health(self) -> None:
        self._send_json(
            HTTPStatus.OK,
            {
                "status": "ok",
                "service": "litert-skill-demo",
                "uptime_seconds": round(time.time() - self.server.started_at, 3),
                "backend": self.server.config.agent_config.backend,
                "model_path": self.server.config.agent_config.model_path,
                "worker_binary": self.server.config.agent_config.worker_binary,
                "api": "robot-skill-chain-demo",
                "endpoints": ["/health", "/skills", "/run", "/run_live", "/reset"],
            },
        )

    def _handle_run(self) -> None:
        body = self._read_json_body()
        if body is None:
            return

        parsed = self._parse_run_request(body)
        if parsed is None:
            return

        started_at = time.time()
        try:
            outcome, recovery_stats = self._run_with_recovery(
                task=parsed["task"],
                max_steps=parsed["max_steps"],
                verbose=parsed["verbose"],
                auto_retry_after_reset=parsed["auto_retry_after_reset"],
                reset_before=parsed["reset_before"],
            )
        except Exception as exc:  # pragma: no cover - runtime boundary
            self._send_json(
                HTTPStatus.INTERNAL_SERVER_ERROR,
                {
                    "ok": False,
                    "error": {"message": str(exc)},
                },
            )
            return

        self._send_json(
            HTTPStatus.OK,
            self._build_result_payload(outcome, recovery_stats, time.time() - started_at),
        )

    def _handle_run_live(self) -> None:
        body = self._read_json_body()
        if body is None:
            return

        parsed = self._parse_run_request(body)
        if parsed is None:
            return

        started_at = time.time()
        self._start_ndjson_stream()
        self._send_stream_event(
            {
                "event": "task_started",
                "task": parsed["task"],
                "reset_before": parsed["reset_before"],
                "auto_retry_after_reset": parsed["auto_retry_after_reset"],
            }
        )

        try:
            outcome, recovery_stats = self._run_with_recovery(
                task=parsed["task"],
                max_steps=parsed["max_steps"],
                verbose=parsed["verbose"],
                auto_retry_after_reset=parsed["auto_retry_after_reset"],
                reset_before=parsed["reset_before"],
                progress_callback=self._send_stream_event,
            )
            self._send_stream_event(
                {
                    "event": "result",
                    "response": self._build_result_payload(outcome, recovery_stats, time.time() - started_at),
                }
            )
        except Exception as exc:  # pragma: no cover - runtime boundary
            self._send_stream_event(
                {
                    "event": "error",
                    "response": {
                        "ok": False,
                        "error": {"message": str(exc)},
                    },
                }
            )

    def _handle_reset(self) -> None:
        reset_mode = self.server.agent.reset()
        self._send_json(
            HTTPStatus.OK,
            {"ok": True, "status": "reset", "mode": reset_mode},
        )

    def _run_with_recovery(
        self,
        *,
        task: str,
        max_steps: int | None,
        verbose: bool,
        auto_retry_after_reset: bool,
        reset_before: bool,
        progress_callback: Callable[[dict[str, Any]], None] | None = None,
    ) -> tuple[Any, dict[str, Any]]:
        last_error: Exception | None = None
        attempt_count = 0
        auto_reset_retry_count = 0
        total_reset_seconds = 0.0
        attempt_traces: list[dict[str, Any]] = []

        if reset_before:
            _emit_progress(
                progress_callback,
                event="reset_started",
                reason="manual_reset_before_task",
            )
            reset_started_at = time.time()
            reset_mode = self.server.agent.reset()
            reset_seconds = round(time.time() - reset_started_at, 6)
            total_reset_seconds += reset_seconds
            _emit_progress(
                progress_callback,
                event="reset_completed",
                reason="manual_reset_before_task",
                reset_mode=reset_mode,
                reset_seconds=reset_seconds,
            )

        max_attempts = 2 if auto_retry_after_reset else 1
        for attempt_index in range(max_attempts):
            attempt_count += 1
            if attempt_index > 0:
                auto_reset_retry_count += 1
                _emit_progress(
                    progress_callback,
                    event="reset_started",
                    reason="auto_retry_after_failure",
                    attempt=attempt_count,
                )
                reset_started_at = time.time()
                reset_mode = self.server.agent.reset()
                reset_seconds = round(time.time() - reset_started_at, 6)
                total_reset_seconds += reset_seconds
                _emit_progress(
                    progress_callback,
                    event="reset_completed",
                    reason="auto_retry_after_failure",
                    attempt=attempt_count,
                    reset_mode=reset_mode,
                    reset_seconds=reset_seconds,
                )

            attempt_started_at = time.time()
            _emit_progress(
                progress_callback,
                event="attempt_started",
                attempt=attempt_count,
            )
            try:
                outcome = self.server.agent.run_task(
                    task,
                    max_steps=max_steps,
                    verbose=verbose,
                    progress_callback=progress_callback,
                )
                elapsed_seconds = round(time.time() - attempt_started_at, 6)
                attempt_traces.append(
                    {
                        "attempt": attempt_count,
                        "status": "completed",
                        "elapsed_seconds": elapsed_seconds,
                    }
                )
                _emit_progress(
                    progress_callback,
                    event="attempt_completed",
                    attempt=attempt_count,
                    elapsed_seconds=elapsed_seconds,
                )
                return outcome, {
                    "service_attempt_count": attempt_count,
                    "auto_reset_retry_count": auto_reset_retry_count,
                    "total_reset_seconds": round(total_reset_seconds, 6),
                    "attempt_traces": attempt_traces,
                }
            except Exception as exc:
                last_error = exc
                elapsed_seconds = round(time.time() - attempt_started_at, 6)
                attempt_traces.append(
                    {
                        "attempt": attempt_count,
                        "status": "failed",
                        "elapsed_seconds": elapsed_seconds,
                        "error": str(exc),
                    }
                )
                _emit_progress(
                    progress_callback,
                    event="attempt_failed",
                    attempt=attempt_count,
                    elapsed_seconds=elapsed_seconds,
                    error=str(exc),
                )

        assert last_error is not None
        raise last_error

    def _build_result_payload(
        self,
        outcome,
        recovery_stats: dict[str, Any],
        service_elapsed_seconds: float,
    ) -> dict[str, Any]:
        last_skill = outcome.skill_results[-1] if outcome.skill_results else None
        executed_skill = None
        if last_skill is not None:
            executed_skill = self.server.registry.describe(last_skill.get("skill_name", ""))
        executed_skills = [
            self.server.registry.describe(skill_result.get("skill_name", "")) or {}
            for skill_result in outcome.skill_results
        ]

        stats = dict(outcome.stats)
        stats.update(recovery_stats)
        stats["service_elapsed_seconds"] = round(service_elapsed_seconds, 6)

        return {
            "ok": True,
            "task": outcome.task,
            "final_message": outcome.final_message,
            "raw_responses": outcome.raw_responses,
            "planned_skill_calls": outcome.planned_skill_calls,
            "skill_results": outcome.skill_results,
            "executed_skill": executed_skill,
            "executed_skills": executed_skills,
            "stats": stats,
        }

    def _read_json_body(self) -> dict[str, Any] | None:
        length_header = self.headers.get("Content-Length")
        if not length_header:
            self._send_json(HTTPStatus.BAD_REQUEST, {"error": {"message": "Missing Content-Length."}})
            return None
        try:
            length = int(length_header)
        except ValueError:
            self._send_json(HTTPStatus.BAD_REQUEST, {"error": {"message": "Invalid Content-Length."}})
            return None

        raw = self.rfile.read(length)
        try:
            body = json.loads(raw.decode("utf-8"))
        except Exception:
            self._send_json(HTTPStatus.BAD_REQUEST, {"error": {"message": "Body must be valid JSON."}})
            return None
        if not isinstance(body, dict):
            self._send_json(HTTPStatus.BAD_REQUEST, {"error": {"message": "JSON body must be an object."}})
            return None
        return body

    def _check_auth(self) -> bool:
        expected = self.server.config.api_key
        if not expected:
            return True
        header = self.headers.get("Authorization", "")
        if header == f"Bearer {expected}":
            return True
        self._send_json(HTTPStatus.UNAUTHORIZED, {"error": {"message": "Unauthorized."}})
        return False

    def _send_error(self, status: HTTPStatus, message: str) -> None:
        self._send_json(status, {"error": {"message": message}})

    def _send_json(self, status: HTTPStatus, payload: object) -> None:
        body = _json_bytes(payload)
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Authorization, Content-Type")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.end_headers()
        self.wfile.write(body)

    def _parse_run_request(self, body: dict[str, Any]) -> dict[str, Any] | None:
        task = body.get("task")
        if not isinstance(task, str) or not task.strip():
            self._send_json(
                HTTPStatus.BAD_REQUEST,
                {"error": {"message": "task must be a non-empty string."}},
            )
            return None

        return {
            "task": task.strip(),
            "max_steps": _coerce_int(body.get("max_steps")),
            "verbose": _coerce_bool(body.get("verbose"), False),
            "auto_retry_after_reset": _coerce_bool(body.get("auto_retry_after_reset"), True),
            "reset_before": _coerce_bool(body.get("reset_before"), False),
        }

    def _start_ndjson_stream(self) -> None:
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "application/x-ndjson; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "close")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Authorization, Content-Type")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.end_headers()

    def _send_stream_event(self, payload: dict[str, Any]) -> None:
        try:
            self.wfile.write(_json_bytes(payload) + b"\n")
            self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, ValueError):
            pass


def _emit_progress(callback: Callable[[dict[str, Any]], None] | None, **payload: Any) -> None:
    if callback is None:
        return
    callback(payload)


def _coerce_bool(value: object, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)


def _coerce_int(value: object) -> int | None:
    if value is None:
        return None
    return int(value)
