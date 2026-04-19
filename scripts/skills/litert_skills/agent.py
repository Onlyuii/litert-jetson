from __future__ import annotations

import codecs
import inspect
import json
import os
import pty
import re
import select
import subprocess
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

PROMPT_MARKER = "Please enter the prompt (or press Enter to end): "
LINE_SEPARATOR = "\u2028"
PROMPTS_DIR = Path(__file__).resolve().parent.parent / "prompts"
ProgressCallback = Callable[[dict[str, Any]], None]
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


class LiteRTProtocolError(RuntimeError):
    """Raised when the model output violates the planning protocol."""


@dataclass(frozen=True)
class SkillParameter:
    name: str
    param_type: str
    description: str = ""
    required: bool = True

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "type": self.param_type,
            "description": self.description,
            "required": self.required,
        }


@dataclass
class SkillSpec:
    name: str
    description: str
    parameters: list[SkillParameter]
    handler: Callable[..., Any]
    aliases: list[str] = field(default_factory=list)

    def as_prompt_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "parameters": [parameter.as_dict() for parameter in self.parameters],
            "aliases": list(self.aliases),
        }


class SkillRegistry:
    def __init__(self) -> None:
        self._skills: dict[str, SkillSpec] = {}

    def register_function(
        self,
        func: Callable[..., Any],
        *,
        name: str | None = None,
        description: str | None = None,
        aliases: list[str] | None = None,
    ) -> SkillSpec:
        skill_name = name or func.__name__
        skill_description = description or inspect.getdoc(func) or skill_name
        parameters: list[SkillParameter] = []
        signature = inspect.signature(func)
        for parameter in signature.parameters.values():
            annotation = _annotation_name(parameter.annotation)
            required = parameter.default is inspect._empty
            parameters.append(
                SkillParameter(
                    name=parameter.name,
                    param_type=annotation,
                    required=required,
                )
            )

        spec = SkillSpec(
            name=skill_name,
            description=skill_description,
            parameters=parameters,
            handler=func,
            aliases=list(aliases or []),
        )
        self._skills[skill_name] = spec
        return spec

    def execute(self, skill_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        started_at = time.time()
        resolved_name, spec = self._resolve_skill(skill_name)
        if spec is None:
            return {
                "ok": False,
                "status": "failed",
                "skill_name": skill_name,
                "arguments": dict(arguments or {}),
                "error": f"Unknown skill: {skill_name}",
                "elapsed_seconds": 0.0,
            }

        try:
            final_arguments = dict(arguments or {})
            result = spec.handler(**final_arguments)
            return {
                "ok": True,
                "status": "completed",
                "skill_name": resolved_name,
                "arguments": final_arguments,
                "result": result,
                "elapsed_seconds": round(time.time() - started_at, 6),
            }
        except Exception as exc:  # pragma: no cover - runtime boundary
            return {
                "ok": False,
                "status": "failed",
                "skill_name": resolved_name,
                "arguments": dict(arguments or {}),
                "error": str(exc),
                "elapsed_seconds": round(time.time() - started_at, 6),
            }

    def prompt_catalog(self) -> list[dict[str, Any]]:
        return [self._skills[name].as_prompt_dict() for name in sorted(self._skills)]

    def describe(self, skill_name: str) -> dict[str, Any] | None:
        _, spec = self._resolve_skill(skill_name)
        if spec is None:
            return None
        return spec.as_prompt_dict()

    def canonical_name(self, skill_name: str) -> str | None:
        resolved_name, spec = self._resolve_skill(skill_name)
        if spec is None:
            return None
        return resolved_name

    def _resolve_skill(self, skill_name: str) -> tuple[str, SkillSpec | None]:
        if skill_name in self._skills:
            return skill_name, self._skills[skill_name]

        normalized = str(skill_name or "").strip()
        if not normalized:
            return normalized, None

        lowered = normalized.lower()
        for name, spec in self._skills.items():
            if name.lower() == lowered:
                return name, spec
            if lowered in {alias.lower() for alias in spec.aliases}:
                return name, spec
        return normalized, None


@dataclass(frozen=True)
class AgentConfig:
    worker_binary: str
    model_path: str
    backend: str = "gpu"
    async_mode: bool = False
    startup_timeout_seconds: int = 120
    request_timeout_seconds: int = 240
    max_output_tokens: int = 1024
    max_steps: int = 3
    num_iterations: int = 1000000


@dataclass
class RunOutcome:
    task: str
    final_message: str
    raw_responses: list[str] = field(default_factory=list)
    planned_skill_calls: list[dict[str, Any]] = field(default_factory=list)
    skill_results: list[dict[str, Any]] = field(default_factory=list)
    stats: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class LiteRTCliConfig:
    worker_binary: str
    model_path: str
    backend: str
    async_mode: bool
    startup_timeout_seconds: int
    request_timeout_seconds: int
    max_output_tokens: int
    num_iterations: int


@dataclass(frozen=True)
class RobotChainTaskData:
    name: str
    description: str
    parameters: dict[str, Any]
    instruction: str


@dataclass(frozen=True)
class RobotChainRequirements:
    need_env: bool
    base_pose: dict[str, float] | None
    move_arm_targets: list[dict[str, float]]
    gripper_actions: list[str]


class LiteRTCliSession:
    def __init__(self, config: LiteRTCliConfig):
        self._config = config
        self._master_fd: int | None = None
        self._stdin_fd: int | None = None
        self._process: subprocess.Popen[bytes] | None = None
        self._decoder = codecs.getincrementaldecoder("utf-8")()
        self._buffer = ""
        self._lock = threading.Lock()
        self._start()

    def _start(self) -> None:
        master_fd, slave_fd = pty.openpty()
        argv = [
            self._config.worker_binary,
            f"--model_path={self._config.model_path}",
            f"--backend={self._config.backend}",
            "--multi_turns=true",
            f"--async={'true' if self._config.async_mode else 'false'}",
        ]
        if self._config.max_output_tokens > 0:
            argv.append(f"--max_output_tokens={self._config.max_output_tokens}")
        if self._config.num_iterations > 0:
            argv.append(f"--num_iterations={self._config.num_iterations}")

        try:
            self._process = subprocess.Popen(
                argv,
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

    def soft_reset(self) -> None:
        with self._lock:
            if self._process is None or self._master_fd is None or self._stdin_fd is None:
                raise LiteRTCliError("LiteRT worker is not running.")
            if self._process.poll() is not None:
                raise LiteRTCliError("LiteRT worker has already exited.")
            self._write_all(b"\n")
            self._read_until_prompt(self._config.startup_timeout_seconds)

    def ask(self, prompt: str) -> str:
        with self._lock:
            if self._process is None or self._master_fd is None or self._stdin_fd is None:
                raise LiteRTCliError("LiteRT worker is not running.")
            if self._process.poll() is not None:
                raise LiteRTCliError("LiteRT worker has already exited.")

            wire_prompt = self._to_wire_prompt(prompt)
            self._write_all((wire_prompt + "\n").encode("utf-8"))
            raw = self._read_until_prompt(self._config.request_timeout_seconds)
            return self._clean_response(raw, wire_prompt)

    @staticmethod
    def _to_wire_prompt(prompt: str) -> str:
        normalized = prompt.replace("\r\n", "\n").replace("\r", "\n")
        return normalized.replace("\n", LINE_SEPARATOR)

    def _write_all(self, payload: bytes) -> None:
        os.set_blocking(self._stdin_fd, True)
        try:
            written_total = 0
            while written_total < len(payload):
                written = os.write(self._stdin_fd, payload[written_total:])
                if written <= 0:
                    raise LiteRTCliError("LiteRT worker stdin stopped accepting data.")
                written_total += written
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
            if ready:
                self._drain_once()

    def _drain_once(self) -> None:
        try:
            data = os.read(self._master_fd, 4096)
        except BlockingIOError:
            return
        except OSError as exc:
            if exc.errno == 5:
                return
            raise LiteRTCliError(f"Failed to read LiteRT worker output: {exc}") from exc
        if data:
            self._buffer += self._decoder.decode(data)

    def _clean_response(self, raw: str, sent_prompt: str) -> str:
        text = raw.replace("\r\n", "\n").replace("\r", "")
        lines = text.split("\n")
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
        while kept_lines and not kept_lines[-1].strip():
            kept_lines.pop()

        cleaned = "\n".join(kept_lines).strip()
        if not cleaned:
            raise LiteRTCliError("LiteRT worker returned an empty response.")
        return cleaned


class LiteRTSkillAgent:
    def __init__(self, config: AgentConfig, registry: SkillRegistry):
        self._config = config
        self._registry = registry
        self._run_lock = threading.Lock()
        self._cli = self._create_cli_session()

    def _create_cli_session(self) -> LiteRTCliSession:
        return LiteRTCliSession(
            LiteRTCliConfig(
                worker_binary=self._config.worker_binary,
                model_path=self._config.model_path,
                backend=self._config.backend,
                async_mode=self._config.async_mode,
                startup_timeout_seconds=self._config.startup_timeout_seconds,
                request_timeout_seconds=self._config.request_timeout_seconds,
                max_output_tokens=self._config.max_output_tokens,
                num_iterations=self._config.num_iterations,
            )
        )

    def _hard_reset_locked(self) -> None:
        previous = self._cli
        previous.close()
        self._cli = self._create_cli_session()

    def close(self) -> None:
        with self._run_lock:
            self._cli.close()

    def reset(self) -> str:
        with self._run_lock:
            try:
                self._cli.soft_reset()
                return "soft"
            except Exception:
                self._hard_reset_locked()
                return "hard"

    def __enter__(self) -> "LiteRTSkillAgent":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    def run_task(
        self,
        task: str,
        *,
        max_steps: int | None = None,
        verbose: bool = False,
        progress_callback: ProgressCallback | None = None,
    ) -> RunOutcome:
        with self._run_lock:
            return self._run_task_locked(
                task,
                max_steps=max_steps,
                verbose=verbose,
                progress_callback=progress_callback,
            )

    def _run_task_locked(
        self,
        task: str,
        *,
        max_steps: int | None,
        verbose: bool,
        progress_callback: ProgressCallback | None,
    ) -> RunOutcome:
        task_data = _extract_robot_chain_task_data(task)
        requirements = _extract_robot_chain_requirements(task_data)

        run_started_at = time.time()
        prompt = self._build_robot_chain_prompt(task_data)
        _emit_progress(
            progress_callback,
            event="inference_started",
            step=1,
            prompt_reason="initial_task",
        )

        inference_started_at = time.time()
        response = self._cli.ask(prompt)
        inference_elapsed = round(time.time() - inference_started_at, 6)
        parsed_plan = _parse_robot_skill_plan_json(response)
        response_type = "skill_plan" if parsed_plan is not None else "invalid"
        _emit_progress(
            progress_callback,
            event="inference_completed",
            step=1,
            prompt_reason="initial_task",
            inference_seconds=inference_elapsed,
            response_type=response_type,
        )

        if verbose:
            print("\n[model-step-1]")
            print(response)

        step_trace: dict[str, Any] = {
            "step": 1,
            "prompt_reason": "initial_task",
            "inference_seconds": inference_elapsed,
            "response_type": response_type,
        }

        validation_error = _robot_chain_output_error(parsed_plan)
        canonical_plan: list[dict[str, Any]] = []
        normalized_plan: list[dict[str, Any]] = []
        if validation_error is None and parsed_plan is not None:
            canonical_plan, validation_error = self._canonicalize_skill_calls(parsed_plan)
        if validation_error is None:
            normalized_plan = _normalize_robot_chain_skill_calls(requirements, canonical_plan)
            validation_error = _validate_robot_chain_skill_calls(requirements, normalized_plan)

        if validation_error is not None:
            step_trace["validation_error"] = validation_error
            _emit_progress(
                progress_callback,
                event="invalid_model_output",
                step=1,
                validation_error=validation_error,
                raw_response=response,
            )
            raise LiteRTProtocolError(validation_error)

        planned_skill_calls = normalized_plan
        step_trace["planned_skill_count"] = len(planned_skill_calls)
        _emit_progress(
            progress_callback,
            event="skill_plan_ready",
            step=1,
            planned_skill_count=len(planned_skill_calls),
            planned_skill_calls=planned_skill_calls,
        )

        skill_results, skill_execution_seconds = self._execute_plan(
            planned_skill_calls,
            progress_callback=progress_callback,
        )

        return RunOutcome(
            task=task,
            final_message=_fast_final_message_from_skill_results(skill_results),
            raw_responses=[response],
            planned_skill_calls=planned_skill_calls,
            skill_results=skill_results,
            stats={
                "llm_inference_count": 1,
                "llm_inference_seconds": inference_elapsed,
                "planned_skill_count": len(planned_skill_calls),
                "skill_execution_count": len(skill_results),
                "skill_execution_seconds": round(skill_execution_seconds, 6),
                "protocol_retry_count": 0,
                "dispatch_mode": "robot_chain_planner",
                "end_to_end_seconds": round(time.time() - run_started_at, 6),
                "step_traces": [step_trace],
                "max_steps_requested": max_steps or self._config.max_steps,
            },
        )

    def _canonicalize_skill_calls(
        self,
        calls: list[dict[str, Any]],
    ) -> tuple[list[dict[str, Any]], str | None]:
        normalized_calls: list[dict[str, Any]] = []
        for call in calls:
            raw_name = str(call.get("skill_name", "") or "").strip()
            canonical_name = self._registry.canonical_name(raw_name)
            if canonical_name is None:
                return [], f"Robot skill plan validation failed: unknown skill '{raw_name}'"
            normalized_calls.append(
                {
                    "skill_name": canonical_name,
                    "arguments": dict(call.get("arguments") or {}),
                }
            )
        return normalized_calls, None

    def _execute_plan(
        self,
        calls: list[dict[str, Any]],
        *,
        progress_callback: ProgressCallback | None,
    ) -> tuple[list[dict[str, Any]], float]:
        executed_results: list[dict[str, Any]] = []
        total_elapsed = 0.0
        queue_total = len(calls)
        for queue_index, call in enumerate(calls, start=1):
            skill_name = call["skill_name"]
            arguments = call["arguments"]
            _emit_progress(
                progress_callback,
                event="skill_execution_started",
                skill_name=skill_name,
                arguments=arguments,
                queue_index=queue_index,
                queue_total=queue_total,
            )
            skill_started_at = time.time()
            result = self._registry.execute(skill_name, arguments)
            skill_elapsed = round(time.time() - skill_started_at, 6)
            result.setdefault("elapsed_seconds", skill_elapsed)
            total_elapsed += skill_elapsed
            executed_results.append(result)
            _emit_progress(
                progress_callback,
                event="skill_execution_completed",
                skill_name=result.get("skill_name", skill_name),
                ok=result.get("ok", False),
                status=result.get("status"),
                elapsed_seconds=skill_elapsed,
                queue_index=queue_index,
                queue_total=queue_total,
            )
            if not result.get("ok", False):
                break
        return executed_results, total_elapsed

    def _build_robot_chain_prompt(self, task_data: RobotChainTaskData) -> str:
        return _render_prompt_template(
            "robot_main_prompt.md",
            ROLE_MD=_read_prompt_template("robot_role.md"),
            OUTPUT_CONTRACT_MD=_read_prompt_template("robot_output_contract.md"),
            REGISTERED_SKILLS_MD=_render_registered_skills_markdown(self._registry),
            TASK_NAME=task_data.name or "robot_chain_task",
            TASK_DESCRIPTION=task_data.description,
            TASK_PARAMETERS_JSON=json.dumps(task_data.parameters, ensure_ascii=False, indent=2),
            TASK_INSTRUCTION=task_data.instruction,
        )


def _annotation_name(annotation: Any) -> str:
    if annotation is inspect._empty:
        return "any"
    if getattr(annotation, "__name__", None):
        return str(annotation.__name__)
    return str(annotation).replace("typing.", "")


def _read_prompt_template(filename: str) -> str:
    return (PROMPTS_DIR / filename).read_text(encoding="utf-8").strip()


def _render_prompt_template(filename: str, **values: str) -> str:
    template = _read_prompt_template(filename)
    rendered = template
    for key, value in values.items():
        rendered = rendered.replace(f"{{{{{key}}}}}", value)
    return rendered


def _render_registered_skills_markdown(registry: SkillRegistry) -> str:
    lines = ["## Registered Skills", ""]
    for skill in registry.prompt_catalog():
        lines.append(f"- {skill['name']}: {skill['description']}")
        parameters = skill.get("parameters") or []
        if parameters:
            lines.append("  parameters:")
            for parameter in parameters:
                requirement = "required" if parameter.get("required", True) else "optional"
                lines.append(
                    f"  - {parameter['name']}: {parameter['type']} ({requirement})"
                )
        else:
            lines.append("  parameters: none")
        lines.append("")
    return "\n".join(lines).strip()


def _emit_progress(callback: ProgressCallback | None, **payload: Any) -> None:
    if callback is None:
        return
    callback(payload)


def _extract_task_field(text: str, key: str) -> str | None:
    match = re.search(rf"^{re.escape(key)}=(.+)$", text, flags=re.MULTILINE)
    if not match:
        return None
    return match.group(1).strip()


def _extract_robot_chain_task_data(text: str) -> RobotChainTaskData:
    task_name = _extract_task_field(text, "TASK_NAME") or ""
    description = (
        _extract_task_field(text, "TASK_DESCRIPTION")
        or _extract_task_field(text, "USER_INSTRUCTION")
        or text.strip()
    )
    parameters: dict[str, Any] = {}
    raw_parameters = _extract_task_field(text, "TASK_PARAMETERS_JSON")
    if raw_parameters:
        try:
            parsed = json.loads(raw_parameters)
            if isinstance(parsed, dict):
                parameters = parsed
        except json.JSONDecodeError:
            parameters = {}
    instruction = _render_robot_chain_instruction(description, parameters)
    return RobotChainTaskData(
        name=task_name,
        description=description.strip(),
        parameters=parameters,
        instruction=instruction,
    )


def _render_robot_chain_instruction(description: str, parameters: dict[str, Any]) -> str:
    if not parameters:
        return description.strip()

    lines = [description.strip()]
    if parameters.get("need_env"):
        lines.append("需要先获取当前环境信息。")

    initial_pose = _coerce_xyz_pose(parameters.get("initial_pose"))
    if initial_pose is not None:
        lines.append(_format_xyz_line("初始位置", initial_pose))

    base_pose = _coerce_xyaw_pose(parameters.get("base_pose"))
    if base_pose is not None:
        lines.append(_format_xyaw_line("底盘目标位姿", base_pose))

    move_arm_targets = _coerce_move_arm_targets(parameters)
    for index, pose in enumerate(move_arm_targets, start=1):
        lines.append(_format_xyz_line(f"机械臂目标{index}", pose))

    gripper_actions = _coerce_gripper_actions(parameters.get("gripper_actions"))
    if gripper_actions:
        lines.append(f"夹爪动作序列: {', '.join(gripper_actions)}")

    return "\n".join(line for line in lines if line.strip())


def _extract_robot_chain_requirements(task_data: RobotChainTaskData) -> RobotChainRequirements:
    if task_data.parameters:
        return RobotChainRequirements(
            need_env=bool(task_data.parameters.get("need_env")) or _instruction_requires_env(task_data.instruction),
            base_pose=_coerce_xyaw_pose(task_data.parameters.get("base_pose")),
            move_arm_targets=_coerce_move_arm_targets(task_data.parameters),
            gripper_actions=_coerce_gripper_actions(task_data.parameters.get("gripper_actions")),
        )

    instruction = task_data.instruction
    return RobotChainRequirements(
        need_env=_instruction_requires_env(instruction),
        base_pose=_extract_base_pose_from_text(instruction),
        move_arm_targets=_extract_xyz_triplets_from_text(instruction),
        gripper_actions=_extract_gripper_actions_from_text(instruction),
    )


def _parse_robot_skill_plan_json(text: str) -> list[dict[str, Any]] | None:
    stripped = text.strip()
    if not stripped:
        return None

    try:
        parsed = json.loads(stripped)
    except json.JSONDecodeError:
        return None

    if not isinstance(parsed, dict):
        return None
    if set(parsed.keys()) != {"skills"}:
        return None

    skills = parsed.get("skills")
    if not isinstance(skills, list) or not skills:
        return None

    parsed_calls: list[dict[str, Any]] = []
    for item in skills:
        if not isinstance(item, dict):
            return None
        if set(item.keys()) - {"skill", "args", "parameters"}:
            return None
        skill_name = item.get("skill")
        if not isinstance(skill_name, str) or not skill_name.strip():
            return None
        arguments = item.get("args")
        parameters = item.get("parameters")
        if arguments is not None and parameters is not None:
            return None
        if arguments is None:
            arguments = parameters
        if arguments is None:
            arguments = {}
        if not isinstance(arguments, dict):
            return None
        parsed_calls.append(
            {
                "skill_name": skill_name.strip(),
                "arguments": dict(arguments),
            }
        )

    return parsed_calls


def _robot_chain_output_error(parsed_plan: list[dict[str, Any]] | None) -> str | None:
    if parsed_plan is None:
        return (
            "Robot chain mode requires exactly one valid JSON object with top-level key "
            "'skills', for example: {\"skills\":[{\"skill\":\"get_env\"}]}"
        )
    return None


def _normalize_robot_chain_skill_calls(
    requirements: RobotChainRequirements,
    calls: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    normalized_calls: list[dict[str, Any]] = []
    move_arm_index = 0
    gripper_index = 0

    for call in calls:
        skill_name = call["skill_name"]
        arguments = dict(call.get("arguments") or {})
        if skill_name == "get_env":
            arguments = {}
        elif skill_name == "move_base" and requirements.base_pose is not None:
            arguments = dict(requirements.base_pose)
        elif skill_name == "move_arm" and move_arm_index < len(requirements.move_arm_targets):
            arguments = dict(requirements.move_arm_targets[move_arm_index])
            move_arm_index += 1
        elif skill_name == "control_gripper" and gripper_index < len(requirements.gripper_actions):
            arguments = {"action": requirements.gripper_actions[gripper_index]}
            gripper_index += 1

        normalized_calls.append(
            {
                "skill_name": skill_name,
                "arguments": arguments,
            }
        )

    return normalized_calls


def _validate_robot_chain_skill_calls(
    requirements: RobotChainRequirements,
    calls: list[dict[str, Any]],
) -> str | None:
    if not calls:
        return "Robot skill plan validation failed: empty plan"

    lowered_names = [str(call.get("skill_name", "") or "").lower() for call in calls]
    issues: list[str] = []

    if requirements.need_env:
        if "get_env" not in lowered_names:
            issues.append("missing get_env step")
        elif lowered_names[0] != "get_env":
            issues.append("get_env must be the first step")

    if requirements.base_pose is not None:
        if "move_base" not in lowered_names:
            issues.append("missing move_base step")
        else:
            move_base_index = lowered_names.index("move_base")
            first_arm_index = next((idx for idx, name in enumerate(lowered_names) if name == "move_arm"), None)
            if first_arm_index is not None and move_base_index > first_arm_index:
                issues.append("move_base must happen before move_arm")

    move_arm_count = sum(1 for name in lowered_names if name == "move_arm")
    if move_arm_count < len(requirements.move_arm_targets):
        issues.append(
            f"missing move_arm steps: expected {len(requirements.move_arm_targets)}, got {move_arm_count}"
        )

    planned_gripper_actions = [
        str((call.get("arguments") or {}).get("action", "")).strip().lower()
        for call in calls
        if str(call.get("skill_name", "") or "").lower() == "control_gripper"
    ]
    expected_gripper_actions = list(requirements.gripper_actions)
    if len(planned_gripper_actions) < len(expected_gripper_actions):
        issues.append(
            "missing control_gripper steps: expected "
            f"{len(expected_gripper_actions)}, got {len(planned_gripper_actions)}"
        )
    elif expected_gripper_actions and planned_gripper_actions[: len(expected_gripper_actions)] != expected_gripper_actions:
        issues.append(
            "control_gripper sequence mismatch: expected "
            f"{expected_gripper_actions}, got {planned_gripper_actions}"
        )

    if issues:
        return "Robot skill plan validation failed: " + "; ".join(issues)
    return None


def _instruction_requires_env(text: str) -> bool:
    return any(token in text for token in ("环境", "观察", "拍照", "scene", "observe", "get_env"))


def _coerce_xyz_pose(value: Any) -> dict[str, float] | None:
    if not isinstance(value, dict):
        return None
    if not {"x", "y", "z"}.issubset(value):
        return None
    try:
        return {
            "x": float(value["x"]),
            "y": float(value["y"]),
            "z": float(value["z"]),
        }
    except (TypeError, ValueError):
        return None


def _coerce_xyaw_pose(value: Any) -> dict[str, float] | None:
    if not isinstance(value, dict):
        return None
    if not {"x", "y", "yaw"}.issubset(value):
        return None
    try:
        return {
            "x": float(value["x"]),
            "y": float(value["y"]),
            "yaw": float(value["yaw"]),
        }
    except (TypeError, ValueError):
        return None


def _coerce_move_arm_targets(parameters: dict[str, Any]) -> list[dict[str, float]]:
    targets: list[dict[str, float]] = []
    raw_targets = parameters.get("move_arm_targets")
    if isinstance(raw_targets, list):
        for item in raw_targets:
            pose = _coerce_xyz_pose(item)
            if pose is not None:
                targets.append(pose)

    if targets:
        return targets

    for key in ("pick_pose", "place_pose"):
        pose = _coerce_xyz_pose(parameters.get(key))
        if pose is not None:
            targets.append(pose)
    return targets


def _coerce_gripper_actions(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    actions: list[str] = []
    for item in value:
        normalized = str(item or "").strip().lower()
        if normalized in {"open", "close"}:
            actions.append(normalized)
    return actions


def _extract_xyz_triplets_from_text(text: str) -> list[dict[str, float]]:
    pattern = re.compile(
        r"x\s*=\s*(-?\d+(?:\.\d+)?)\s*,\s*y\s*=\s*(-?\d+(?:\.\d+)?)\s*,\s*z\s*=\s*(-?\d+(?:\.\d+)?)",
        flags=re.IGNORECASE,
    )
    triplets: list[dict[str, float]] = []
    for match in pattern.finditer(text):
        context_prefix = text[max(0, match.start() - 24) : match.start()]
        if _has_initial_pose_context(context_prefix):
            continue
        triplets.append(
            {
                "x": float(match.group(1)),
                "y": float(match.group(2)),
                "z": float(match.group(3)),
            }
        )
    return triplets


def _extract_base_pose_from_text(text: str) -> dict[str, float] | None:
    pattern = re.compile(
        r"x\s*=\s*(-?\d+(?:\.\d+)?)\s*,\s*y\s*=\s*(-?\d+(?:\.\d+)?)\s*,\s*yaw\s*=\s*(-?\d+(?:\.\d+)?)",
        flags=re.IGNORECASE,
    )
    for match in pattern.finditer(text):
        context_prefix = text[max(0, match.start() - 24) : match.start()]
        if _has_initial_pose_context(context_prefix):
            continue
        return {
            "x": float(match.group(1)),
            "y": float(match.group(2)),
            "yaw": float(match.group(3)),
        }
    return None


def _has_initial_pose_context(text: str) -> bool:
    lowered = text.strip().lower()
    return any(
        marker in lowered
        for marker in (
            "初始位置",
            "当前位置",
            "起始位置",
            "initial position",
            "initial pose",
            "current position",
            "current pose",
            "start position",
            "start pose",
            "home position",
            "home pose",
        )
    )


def _extract_gripper_actions_from_text(text: str) -> list[str]:
    actions: list[str] = []
    segments = re.split(r"[，,。；;\n]+|然后|再|最后", text)
    for segment in segments:
        normalized = segment.strip().lower()
        if not normalized:
            continue
        if any(token in normalized for token in ("打开", "释放", "松开", "open")):
            actions.append("open")
            continue
        if "闭合" in normalized or "close" in normalized:
            actions.append("close")
            continue
        if re.search(r"抓取(?!点)|抓住|夹取", normalized):
            actions.append("close")
    return actions


def _format_xyz_line(label: str, pose: dict[str, float]) -> str:
    return f"{label}: x={pose['x']}, y={pose['y']}, z={pose['z']}"


def _format_xyaw_line(label: str, pose: dict[str, float]) -> str:
    return f"{label}: x={pose['x']}, y={pose['y']}, yaw={pose['yaw']}"


def _fast_final_message_from_skill_results(results: list[dict[str, Any]]) -> str:
    if not results:
        return ""
    failed_result = next((item for item in results if not item.get("ok", False)), None)
    if failed_result is not None:
        return (
            f"Skill chain failed at {failed_result.get('skill_name', 'unknown')}: "
            f"{failed_result.get('error') or failed_result.get('status', 'failed')}"
        )
    names = " -> ".join(str(item.get("skill_name", "")) for item in results)
    return f"Executed {len(results)} planned skills successfully: {names}"
