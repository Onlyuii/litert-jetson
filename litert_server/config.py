from __future__ import annotations

import argparse
import os
from dataclasses import dataclass
from pathlib import Path


def _expand_path(value: str) -> str:
    return str(Path(value).expanduser())


def _env_int(name: str, default: int) -> int:
    value = os.getenv(name)
    if value is None or not value.strip():
        return default
    return int(value)


@dataclass(frozen=True)
class ModelConfig:
    id: str
    path: str
    aliases: tuple[str, ...] = ()


@dataclass(frozen=True)
class ServerConfig:
    bind: str
    port: int
    api_key: str
    runtime_dir: str
    worker_binary: str
    backend: str
    default_model: str
    session_ttl_seconds: int
    max_sessions: int
    startup_timeout_seconds: int
    request_timeout_seconds: int
    max_output_tokens: int
    default_system_prompt: str
    model_e2b_path: str
    model_e4b_path: str


def parse_args(argv: list[str] | None = None) -> ServerConfig:
    runtime_dir = _expand_path(
        os.getenv("LITERT_RUNTIME_DIR", "/home/zeyaoz/litert-lm-runtime")
    )

    parser = argparse.ArgumentParser(
        description="OpenAI-compatible HTTP server for LiteRT-LM runtime binaries."
    )
    parser.add_argument(
        "--bind",
        default=os.getenv("LITERT_SERVER_BIND", "0.0.0.0"),
        help="Bind address.",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=_env_int("LITERT_SERVER_PORT", 8080),
        help="Bind port.",
    )
    parser.add_argument(
        "--api-key",
        default=os.getenv("LITERT_SERVER_API_KEY", ""),
        help="Bearer API key. Empty disables auth.",
    )
    parser.add_argument(
        "--runtime-dir",
        default=runtime_dir,
        help="LiteRT runtime root directory.",
    )
    parser.add_argument(
        "--worker-binary",
        default=os.getenv(
            "LITERT_SERVER_WORKER_BINARY",
            os.path.join(runtime_dir, "bin", "run_litert_lm_advanced_main_gpu"),
        ),
        help="Executable used to start the LiteRT multi-turn REPL.",
    )
    parser.add_argument(
        "--backend",
        default=os.getenv("LITERT_SERVER_BACKEND", "gpu"),
        help="LiteRT backend passed to the worker.",
    )
    parser.add_argument(
        "--default-model",
        default=os.getenv("LITERT_SERVER_DEFAULT_MODEL", "gemma-4-E2B-it"),
        help="Default model id when the request omits model.",
    )
    parser.add_argument(
        "--session-ttl",
        type=int,
        default=_env_int("LITERT_SERVER_SESSION_TTL", 1800),
        help="Idle session time-to-live in seconds.",
    )
    parser.add_argument(
        "--max-sessions",
        type=int,
        default=_env_int("LITERT_SERVER_MAX_SESSIONS", 1),
        help="Maximum number of live model sessions kept in memory.",
    )
    parser.add_argument(
        "--startup-timeout",
        type=int,
        default=_env_int("LITERT_SERVER_STARTUP_TIMEOUT", 300),
        help="Worker startup timeout in seconds.",
    )
    parser.add_argument(
        "--request-timeout",
        type=int,
        default=_env_int("LITERT_SERVER_REQUEST_TIMEOUT", 300),
        help="Per-request timeout in seconds.",
    )
    parser.add_argument(
        "--max-output-tokens",
        type=int,
        default=_env_int("LITERT_SERVER_MAX_OUTPUT_TOKENS", 0),
        help="Optional max output tokens passed to the worker. 0 keeps runtime default.",
    )
    parser.add_argument(
        "--system-prompt",
        default=os.getenv("LITERT_SERVER_SYSTEM_PROMPT", ""),
        help="Optional hidden system prompt prepended to each request.",
    )
    parser.add_argument(
        "--model-e2b",
        default=os.getenv(
            "LITERT_MODEL_E2B",
            os.path.join(runtime_dir, "models", "gemma-4-E2B-it", "model.litertlm"),
        ),
        help="Path to the Gemma 4 E2B model.",
    )
    parser.add_argument(
        "--model-e4b",
        default=os.getenv(
            "LITERT_MODEL_E4B",
            os.path.join(runtime_dir, "models", "gemma-4-E4B-it", "model.litertlm"),
        ),
        help="Path to the Gemma 4 E4B model.",
    )

    args = parser.parse_args(argv)

    return ServerConfig(
        bind=args.bind,
        port=args.port,
        api_key=args.api_key,
        runtime_dir=_expand_path(args.runtime_dir),
        worker_binary=_expand_path(args.worker_binary),
        backend=args.backend,
        default_model=args.default_model,
        session_ttl_seconds=args.session_ttl,
        max_sessions=max(1, args.max_sessions),
        startup_timeout_seconds=max(1, args.startup_timeout),
        request_timeout_seconds=max(1, args.request_timeout),
        max_output_tokens=max(0, args.max_output_tokens),
        default_system_prompt=args.system_prompt,
        model_e2b_path=_expand_path(args.model_e2b),
        model_e4b_path=_expand_path(args.model_e4b),
    )


def build_models(config: ServerConfig) -> tuple[dict[str, ModelConfig], dict[str, str]]:
    canonical_models = {
        "gemma-4-E2B-it": ModelConfig(
            id="gemma-4-E2B-it",
            path=config.model_e2b_path,
            aliases=("gemma-4-2b-chat", "gemma-4-e2b-it"),
        ),
        "gemma-4-E4B-it": ModelConfig(
            id="gemma-4-E4B-it",
            path=config.model_e4b_path,
            aliases=("gemma-4-4b-chat", "gemma-4-e4b-it"),
        ),
    }

    aliases: dict[str, str] = {}
    for model_id, model in canonical_models.items():
        aliases[model_id] = model_id
        aliases[model_id.lower()] = model_id
        for alias in model.aliases:
            aliases[alias] = model_id
            aliases[alias.lower()] = model_id

    return canonical_models, aliases
