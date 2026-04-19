#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parent
SKILLS_ROOT = SCRIPT_DIR.parent
if str(SKILLS_ROOT) not in sys.path:
    sys.path.insert(0, str(SKILLS_ROOT))

from litert_skills import AgentConfig, LiteRTSkillHTTPServer, SkillRegistry, SkillServiceConfig
from litert_skills.robot_skills import register_robot_skills


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Resident LiteRT robot skill-chain demo service."
    )
    parser.add_argument("--bind", default=os.getenv("LITERT_SKILL_BIND", "0.0.0.0"))
    parser.add_argument("--port", type=int, default=int(os.getenv("LITERT_SKILL_PORT", "8090")))
    parser.add_argument("--api-key", default=os.getenv("LITERT_SKILL_API_KEY", ""))
    parser.add_argument(
        "--runtime-dir",
        default=os.getenv("LITERT_RUNTIME_DIR", "/home/zeyaoz/litert-lm-runtime"),
    )
    parser.add_argument("--worker-binary", help="Override worker binary path.")
    parser.add_argument("--model-path", help="Override model path.")
    parser.add_argument("--backend", default=os.getenv("LITERT_SKILL_BACKEND", "gpu"))
    parser.add_argument(
        "--async-mode",
        action=argparse.BooleanOptionalAction,
        default=os.getenv("LITERT_SKILL_ASYNC_MODE", "false").strip().lower() in {"1", "true", "yes", "on"},
        help="Whether to run LiteRT CLI in async mode.",
    )
    parser.add_argument("--startup-timeout", type=int, default=int(os.getenv("LITERT_SKILL_STARTUP_TIMEOUT", "300")))
    parser.add_argument("--request-timeout", type=int, default=int(os.getenv("LITERT_SKILL_REQUEST_TIMEOUT", "300")))
    parser.add_argument(
        "--max-output-tokens",
        type=int,
        default=int(os.getenv("LITERT_SKILL_MAX_OUTPUT_TOKENS", "1024")),
    )
    parser.add_argument("--max-steps", type=int, default=int(os.getenv("LITERT_SKILL_MAX_STEPS", "3")))
    parser.add_argument(
        "--num-iterations",
        type=int,
        default=int(os.getenv("LITERT_SKILL_NUM_ITERATIONS", "1000000")),
    )
    args = parser.parse_args()

    runtime_dir = Path(args.runtime_dir).expanduser().resolve()
    worker_binary = (
        Path(args.worker_binary).expanduser().resolve()
        if args.worker_binary
        else runtime_dir / "bin" / "run_litert_lm_advanced_main_gpu"
    )
    model_path = (
        Path(args.model_path).expanduser().resolve()
        if args.model_path
        else runtime_dir / "models" / "gemma-4-E2B-it" / "model.litertlm"
    )

    registry = SkillRegistry()
    register_robot_skills(registry)

    config = SkillServiceConfig(
        bind=args.bind,
        port=args.port,
        api_key=args.api_key,
        agent_config=AgentConfig(
            worker_binary=str(worker_binary),
            model_path=str(model_path),
            backend=args.backend,
            async_mode=bool(args.async_mode),
            startup_timeout_seconds=max(1, args.startup_timeout),
            request_timeout_seconds=max(1, args.request_timeout),
            max_output_tokens=max(0, args.max_output_tokens),
            max_steps=max(1, args.max_steps),
            num_iterations=max(1, args.num_iterations),
        ),
    )

    server = LiteRTSkillHTTPServer((config.bind, config.port), config, registry)
    print(
        f"LiteRT robot skill demo listening on http://{config.bind}:{config.port} "
        f"with worker {config.agent_config.worker_binary}",
        flush=True,
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
