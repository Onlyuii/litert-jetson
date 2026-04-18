from __future__ import annotations

import codecs
import os
import pty
import re
import select
import subprocess
import threading
import time
from dataclasses import dataclass
from typing import Callable


PROMPT_MARKER = "Please enter the prompt (or press Enter to end): "
LINE_SEPARATOR = "\u2028"
_LOG_PATTERNS = (
    re.compile(r"^[IWEF]\d{4}\s"),
    re.compile(r"^(INFO|WARNING|ERROR): "),
    re.compile(r"^Warning: "),
    re.compile(r"^\*\*\* Check failure stack trace:"),
    re.compile(r"^\s+@\s+0x"),
    re.compile(r"^libnvrm_gpu\.so: "),
    re.compile(r"^NvRm"),
    re.compile(r"^\d+: Memory Manager"),
)


class LiteRTCliError(RuntimeError):
    """Raised when the wrapped LiteRT CLI fails."""


@dataclass
class LiteRTCliConfig:
    worker_binary: str
    model_path: str
    backend: str
    startup_timeout_seconds: int
    request_timeout_seconds: int
    max_output_tokens: int


class LiteRTCliSession:
    """Wraps the LiteRT interactive binary with PTY output and pipe-backed stdin."""

    def __init__(self, config: LiteRTCliConfig):
        self._config = config
        self._master_fd: int | None = None
        self._stdin_fd: int | None = None
        self._process: subprocess.Popen[bytes] | None = None
        self._decoder = codecs.getincrementaldecoder("utf-8")()
        self._buffer = ""
        self._lock = threading.Lock()
        self._start()

    @property
    def pid(self) -> int:
        return -1 if self._process is None else self._process.pid

    def _start(self) -> None:
        master_fd, slave_fd = pty.openpty()

        argv = [
            self._config.worker_binary,
            f"--model_path={self._config.model_path}",
            f"--backend={self._config.backend}",
            "--multi_turns=true",
        ]
        if self._config.max_output_tokens > 0:
            argv.append(f"--max_output_tokens={self._config.max_output_tokens}")

        try:
            self._process = subprocess.Popen(
                argv,
                # Keep stdout/stderr on a PTY so we can read the interactive prompt marker,
                # but send stdin over a plain pipe to avoid PTY line discipline corrupting
                # multi-byte UTF-8 input such as Chinese characters.
                stdin=subprocess.PIPE,
                stdout=slave_fd,
                stderr=slave_fd,
                close_fds=True,
                start_new_session=True,
            )
        finally:
            os.close(slave_fd)

        os.set_blocking(master_fd, False)
        self._master_fd = master_fd
        if self._process is None or self._process.stdin is None:
            self.close()
            raise LiteRTCliError("Failed to open LiteRT worker stdin pipe.")
        self._stdin_fd = self._process.stdin.fileno()

        try:
            self._read_until_prompt(self._config.startup_timeout_seconds)
        except Exception:
            self.close()
            raise

    def close(self) -> None:
        process = self._process
        self._process = None
        self._stdin_fd = None

        if self._master_fd is not None:
            try:
                os.close(self._master_fd)
            except OSError:
                pass
            self._master_fd = None

        if process is None:
            return
        if process.stdin is not None:
            try:
                process.stdin.close()
            except OSError:
                pass
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=3)

    def ask(self, prompt: str) -> str:
        parts: list[str] = []
        return self.ask_stream(prompt, parts.append)

    def ask_stream(self, prompt: str, on_text: Callable[[str], None]) -> str:
        if not prompt.strip():
            raise LiteRTCliError("Cannot send an empty prompt to LiteRT.")

        with self._lock:
            if self._process is None or self._master_fd is None:
                raise LiteRTCliError("LiteRT worker is not running.")

            if self._process.poll() is not None:
                raise LiteRTCliError("LiteRT worker has already exited.")

            wire_prompt = self._to_wire_prompt(prompt)
            payload = (wire_prompt + "\n").encode("utf-8")
            self._write_all(payload)
            return self._read_until_prompt_stream(
                self._config.request_timeout_seconds,
                wire_prompt,
                on_text,
            )

    @staticmethod
    def _to_wire_prompt(prompt: str) -> str:
        text = prompt.replace("\r\n", "\n").replace("\r", "\n")
        return text.replace("\n", LINE_SEPARATOR)

    def _write_all(self, payload: bytes) -> None:
        if self._stdin_fd is None:
            raise LiteRTCliError("LiteRT worker is not running.")

        os.set_blocking(self._stdin_fd, True)
        try:
            total_written = 0
            while total_written < len(payload):
                try:
                    written = os.write(self._stdin_fd, payload[total_written:])
                except OSError as exc:
                    raise LiteRTCliError(f"Failed to write prompt to LiteRT worker: {exc}") from exc
                if written <= 0:
                    raise LiteRTCliError("LiteRT worker stdin pipe stopped accepting prompt bytes.")
                total_written += written
        finally:
            os.set_blocking(self._stdin_fd, False)

    def _read_until_prompt(self, timeout_seconds: int) -> str:
        deadline = time.monotonic() + timeout_seconds

        while True:
            marker_index = self._buffer.find(PROMPT_MARKER)
            if marker_index >= 0:
                chunk = self._buffer[:marker_index]
                self._buffer = self._buffer[marker_index + len(PROMPT_MARKER) :]
                return chunk

            process = self._process
            if process is not None and process.poll() is not None:
                self._drain_once()
                stderr_tail = self._buffer.strip()
                raise LiteRTCliError(
                    f"LiteRT worker exited unexpectedly with code {process.returncode}: {stderr_tail}"
                )

            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise LiteRTCliError("Timed out waiting for LiteRT worker output.")

            ready, _, _ = select.select([self._master_fd], [], [], min(0.25, remaining))
            if not ready:
                continue
            self._drain_once()

    def _read_until_prompt_stream(
        self,
        timeout_seconds: int,
        sent_prompt: str,
        on_text: Callable[[str], None],
    ) -> str:
        deadline = time.monotonic() + timeout_seconds
        emitted = ""

        while True:
            marker_index = self._buffer.find(PROMPT_MARKER)
            if marker_index >= 0:
                chunk = self._buffer[:marker_index]
                self._buffer = self._buffer[marker_index + len(PROMPT_MARKER) :]
                cleaned = self._clean_response_snapshot(chunk, sent_prompt, final=True)
                if cleaned.startswith(emitted):
                    delta = cleaned[len(emitted) :]
                    if delta:
                        on_text(delta)
                if not cleaned:
                    raise LiteRTCliError("LiteRT worker returned an empty response.")
                return cleaned

            emitted = self._emit_stream_delta(
                sent_prompt=sent_prompt,
                emitted=emitted,
                on_text=on_text,
            )

            process = self._process
            if process is not None and process.poll() is not None:
                self._drain_once()
                stderr_tail = self._buffer.strip()
                raise LiteRTCliError(
                    f"LiteRT worker exited unexpectedly with code {process.returncode}: {stderr_tail}"
                )

            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise LiteRTCliError("Timed out waiting for LiteRT worker output.")

            ready, _, _ = select.select([self._master_fd], [], [], min(0.1, remaining))
            if not ready:
                continue
            self._drain_once()

    def _emit_stream_delta(
        self,
        sent_prompt: str,
        emitted: str,
        on_text: Callable[[str], None],
    ) -> str:
        overlap = self._prompt_marker_overlap(self._buffer)
        candidate = self._buffer[:-overlap] if overlap else self._buffer

        if not candidate:
            return emitted

        cleaned = self._clean_response_snapshot(candidate, sent_prompt, final=False)
        if cleaned.startswith(emitted) and len(cleaned) > len(emitted):
            delta = cleaned[len(emitted) :]
            if delta:
                on_text(delta)
                return cleaned
        return emitted

    def _drain_once(self) -> None:
        if self._master_fd is None:
            return
        try:
            data = os.read(self._master_fd, 4096)
        except BlockingIOError:
            return
        except OSError as exc:
            if exc.errno == 5:
                return
            raise LiteRTCliError(f"Failed to read LiteRT worker output: {exc}") from exc

        if not data:
            return
        self._buffer += self._decoder.decode(data)

    def _clean_response(self, raw: str, sent_prompt: str) -> str:
        return self._clean_response_snapshot(raw, sent_prompt, final=True)

    def _clean_response_snapshot(self, raw: str, sent_prompt: str, final: bool) -> str:
        text = raw.replace("\r\n", "\n").replace("\r", "")
        lines = text.split("\n")
        tail = None

        if not final and text and not text.endswith("\n"):
            tail = lines.pop()

        kept_lines: list[str] = []
        prompt_removed = False
        for line in lines:
            stripped = line.strip()
            if not stripped:
                kept_lines.append("")
                continue
            if any(pattern.match(stripped) for pattern in _LOG_PATTERNS):
                continue
            if not prompt_removed and stripped == sent_prompt.strip():
                prompt_removed = True
                continue
            kept_lines.append(line)

        while kept_lines and not kept_lines[0].strip():
            kept_lines.pop(0)

        if tail is not None:
            stripped_tail = tail.strip()
            if stripped_tail and not self._should_hold_back_partial_noise(
                stripped_tail, sent_prompt, prompt_removed
            ):
                kept_lines.append(tail)

        if final:
            while kept_lines and not kept_lines[-1].strip():
                kept_lines.pop()
            response = "\n".join(kept_lines).strip()
        else:
            response = "\n".join(kept_lines).lstrip("\n")

        if not response:
            if final:
                raise LiteRTCliError("LiteRT worker returned an empty response.")
        return response

    @staticmethod
    def _should_hold_back_partial_noise(
        stripped_tail: str,
        sent_prompt: str,
        prompt_removed: bool,
    ) -> bool:
        if any(pattern.match(stripped_tail) for pattern in _LOG_PATTERNS):
            return True
        noisy_prefixes = (
            "I000",
            "W000",
            "E000",
            "F000",
            "INFO:",
            "WARNING:",
            "ERROR:",
            "Warning:",
            "*** Check failure",
            "@",
            "libnvrm_gpu.so:",
            "NvRm",
        )
        if any(prefix.startswith(stripped_tail) or stripped_tail.startswith(prefix) for prefix in noisy_prefixes):
            return True
        if not prompt_removed and sent_prompt.strip().startswith(stripped_tail):
            return True
        return False

    @staticmethod
    def _prompt_marker_overlap(text: str) -> int:
        max_overlap = min(len(text), len(PROMPT_MARKER) - 1)
        for size in range(max_overlap, 0, -1):
            if text.endswith(PROMPT_MARKER[:size]):
                return size
        return 0
