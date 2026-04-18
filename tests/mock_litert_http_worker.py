#!/usr/bin/env python3
import json
import os
import sys


def parse_args(argv):
    config = {"backend": "gpu", "model_path": "", "cache_dir": ""}
    i = 1
    while i < len(argv):
        arg = argv[i]
        if arg in ("--model-path", "--backend", "--cache-dir"):
            if i + 1 >= len(argv):
                raise SystemExit(f"missing value for {arg}")
            value = argv[i + 1]
            i += 2
        elif "=" in arg and arg.startswith("--"):
            key, value = arg.split("=", 1)
            arg = key
            i += 1
        else:
            raise SystemExit(f"unknown arg: {arg}")

        if arg == "--model-path":
            config["model_path"] = value
        elif arg == "--backend":
            config["backend"] = value
        elif arg == "--cache-dir":
            config["cache_dir"] = value

    if not config["model_path"]:
        raise SystemExit("--model-path is required")
    return config


def extract_text(message):
    content = message.get("content", [])
    if isinstance(content, str):
      return content
    if isinstance(content, dict):
      return content.get("text", "")
    if not isinstance(content, list):
      return ""
    parts = []
    for part in content:
        if isinstance(part, str):
            parts.append(part)
        elif isinstance(part, dict) and isinstance(part.get("text"), str):
            parts.append(part["text"])
    return "".join(parts)


def make_reply(request, model_size):
    mode = request.get("mode", "chat")
    history = request.get("history", [])
    message = request.get("message", {})
    user_count = sum(1 for item in history if item.get("role") == "user")
    if message.get("role") == "user":
        user_count += 1
    text = extract_text(message)
    reply = f"你好 来自 {model_size} {mode} users={user_count} echo={text}"
    channels = {"thought": "mock-thought"} if mode == "thinking" else {}
    return reply, channels


def emit(event):
    sys.stdout.write(json.dumps(event, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def main():
    try:
        config = parse_args(sys.argv)
    except SystemExit as exc:
        emit({"event": "error", "message": str(exc)})
        return 1

    model_path = config["model_path"].lower()
    model_size = "4b" if "e4b" in model_path or "4b" in model_path else "2b"

    emit({
        "event": "ready",
        "backend": config["backend"],
        "model_path": config["model_path"],
        "pid": os.getpid(),
    })

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            request = json.loads(line)
        except json.JSONDecodeError:
            emit({"event": "error", "message": "invalid json"})
            continue

        if request.get("command") != "chat":
            emit({"event": "error", "message": "unsupported command"})
            continue

        reply, channels = make_reply(request, model_size)
        for chunk in reply.split(" "):
            if not chunk:
                continue
            emit({"event": "chunk", "text": chunk + " "})
        done = {"event": "done", "text": reply}
        if channels:
            done["channels"] = channels
        emit(done)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
