#!/usr/bin/env python3

from __future__ import annotations

import re
import sys
import time


PROMPT = "Please enter the prompt (or press Enter to end): "
LINE_SEPARATOR = "\u2028"


def parse_args(argv: list[str]) -> dict[str, str]:
    parsed = {"model_path": "", "backend": "gpu"}
    for arg in argv[1:]:
        if not arg.startswith("--") or "=" not in arg:
            continue
        key, value = arg.split("=", 1)
        if key == "--model_path":
            parsed["model_path"] = value
        elif key == "--backend":
            parsed["backend"] = value
    return parsed


def last_user_from_bootstrap(prompt: str) -> tuple[str, int]:
    users = re.findall(r'<message role="user">(.*?)</message>', prompt)
    if not users:
        return prompt.replace(LINE_SEPARATOR, "\n").strip(), 1
    cleaned = [item.replace(LINE_SEPARATOR, "\n").strip() for item in users]
    return cleaned[-1], len(cleaned)


def emit(text: str, newline: bool = True) -> None:
    if newline:
        sys.stdout.write(text + "\n")
    else:
        sys.stdout.write(text)
    sys.stdout.flush()


def emit_streamed_line(text: str, chunk_size: int = 8, delay_seconds: float = 0.01) -> None:
    for index in range(0, len(text), chunk_size):
        emit(text[index : index + chunk_size], newline=False)
        time.sleep(delay_seconds)
    emit("")


def main() -> int:
    args = parse_args(sys.argv)
    model_path = args["model_path"].lower()
    backend = args["backend"]
    size = "e4b" if "e4b" in model_path or "4b" in model_path else "e2b"

    emit("WARNING: All log messages before absl::InitializeLog() is called are written to STDERR")
    emit("I0000 00:00:0000000000.000000 mock litert_lm_lib.cc:775] Running multi-turns conversation")
    emit(PROMPT, newline=False)

    user_count = 0
    turn_count = 0

    for raw_line in sys.stdin:
        prompt = raw_line.strip()
        if not prompt:
            break

        turn_count += 1
        if "<litert-server-bootstrap>" in prompt:
            last_user, user_count = last_user_from_bootstrap(prompt)
        else:
            last_user = prompt.replace(LINE_SEPARATOR, "\n").strip()
            user_count += 1

        emit("I0000 00:00:0000000000.000001 mock session_basic.cc:382] RunPrefillAsync status: OK")
        emit("I0000 00:00:0000000000.000002 mock session_basic.cc:502] RunDecodeAsync")
        emit_streamed_line(
            f"[mock {size} {backend}] turn={turn_count} users={user_count} reply_to={last_user}"
        )
        emit(PROMPT, newline=False)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
