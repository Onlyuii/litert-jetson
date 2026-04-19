#!/usr/bin/env python3

from __future__ import annotations

import json
import re
import sys
import time


PROMPT = "Please enter the prompt (or press Enter to end): "
LINE_SEPARATOR = "\u2028"


def parse_args(argv: list[str]) -> dict[str, str]:
    parsed = {"model_path": "", "backend": "gpu", "num_iterations": "1"}
    for arg in argv[1:]:
        if not arg.startswith("--") or "=" not in arg:
            continue
        key, value = arg.split("=", 1)
        if key == "--model_path":
            parsed["model_path"] = value
        elif key == "--backend":
            parsed["backend"] = value
        elif key == "--num_iterations":
            parsed["num_iterations"] = value
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


def _extract_xyz_triplets(text: str) -> list[dict[str, float]]:
    pattern = re.compile(
        r"x\s*=\s*(-?\d+(?:\.\d+)?)\s*,\s*y\s*=\s*(-?\d+(?:\.\d+)?)\s*,\s*z\s*=\s*(-?\d+(?:\.\d+)?)",
        flags=re.IGNORECASE,
    )
    triplets: list[dict[str, float]] = []
    for match in pattern.finditer(text):
        triplets.append(
            {
                "x": float(match.group(1)),
                "y": float(match.group(2)),
                "z": float(match.group(3)),
            }
        )
    return triplets


def _extract_task_field(text: str, key: str) -> str | None:
    match = re.search(rf"^{re.escape(key)}=(.+)$", text, flags=re.MULTILINE)
    if not match:
        return None
    return match.group(1).strip()


def _extract_structured_parameters(text: str) -> dict[str, object]:
    raw = _extract_task_field(text, "TASK_PARAMETERS_JSON")
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    if isinstance(parsed, dict):
        return parsed
    return {}


def _extract_base_pose(text: str) -> dict[str, float] | None:
    match = re.search(
        r"x\s*=\s*(-?\d+(?:\.\d+)?)\s*,\s*y\s*=\s*(-?\d+(?:\.\d+)?)\s*,\s*yaw\s*=\s*(-?\d+(?:\.\d+)?)",
        text,
        flags=re.IGNORECASE,
    )
    if not match:
        return None
    return {
        "x": float(match.group(1)),
        "y": float(match.group(2)),
        "yaw": float(match.group(3)),
    }


def _coerce_xyz_pose(value: object) -> dict[str, float] | None:
    if not isinstance(value, dict) or not {"x", "y", "z"}.issubset(value):
        return None
    try:
        return {
            "x": float(value["x"]),
            "y": float(value["y"]),
            "z": float(value["z"]),
        }
    except (TypeError, ValueError):
        return None


def _coerce_xyaw_pose(value: object) -> dict[str, float] | None:
    if not isinstance(value, dict) or not {"x", "y", "yaw"}.issubset(value):
        return None
    try:
        return {
            "x": float(value["x"]),
            "y": float(value["y"]),
            "yaw": float(value["yaw"]),
        }
    except (TypeError, ValueError):
        return None


def _extract_structured_arm_targets(parameters: dict[str, object]) -> list[dict[str, float]]:
    targets: list[dict[str, float]] = []
    raw_targets = parameters.get("move_arm_targets")
    if isinstance(raw_targets, list):
        for item in raw_targets:
            pose = _coerce_xyz_pose(item)
            if pose is not None:
                targets.append(pose)
    return targets


def _mock_skill_plan_response(text: str) -> str:
    instruction = text.replace(LINE_SEPARATOR, "\n")
    parameters = _extract_structured_parameters(instruction)
    skills: list[dict[str, object]] = []

    if parameters.get("need_env") or any(token in instruction for token in ("环境", "观察", "拍照")):
        skills.append({"skill_name": "get_env", "arguments": {}})

    base_pose = _coerce_xyaw_pose(parameters.get("base_pose")) or _extract_base_pose(instruction)
    if base_pose is not None:
        skills.append({"skill_name": "move_base", "arguments": base_pose})

    triplets = _extract_structured_arm_targets(parameters) or _extract_xyz_triplets(instruction)
    if triplets:
        skills.append({"skill_name": "move_arm", "arguments": triplets[0]})

    gripper_actions = parameters.get("gripper_actions")
    if isinstance(gripper_actions, list) and gripper_actions:
        if str(gripper_actions[0]).strip().lower() == "close":
            skills.append({"skill_name": "control_gripper", "arguments": {"action": "close"}})
    elif any(token in instruction for token in ("闭合", "抓取", "close")):
        skills.append({"skill_name": "control_gripper", "arguments": {"action": "close"}})

    if len(triplets) > 1:
        skills.append({"skill_name": "move_arm", "arguments": triplets[1]})

    if isinstance(gripper_actions, list) and len(gripper_actions) > 1:
        if str(gripper_actions[1]).strip().lower() == "open":
            skills.append({"skill_name": "control_gripper", "arguments": {"action": "open"}})
    elif any(token in instruction for token in ("打开", "释放", "open")):
        skills.append({"skill_name": "control_gripper", "arguments": {"action": "open"}})

    if not skills:
        skills.append({"skill_name": "get_env", "arguments": {}})

    action_map: dict[str, object] = {}
    for index, skill in enumerate(skills, start=1):
        action_map[f"action{index}"] = {
            "skill": skill["skill_name"],
            "args": skill["arguments"],
            "is_finish": index == len(skills),
        }

    thinking = (
        "<robot_thinking>\n"
        "1. Analyze the task.\n"
        "2. Decide the full ordered action sequence.\n"
        "3. Confirm exact parameters.\n"
        "</robot_thinking>\n"
    )
    plan = (
        "<robot_skill_plan>\n"
        f"{json.dumps(action_map, ensure_ascii=False, indent=2)}\n"
        "</robot_skill_plan>"
    )
    return thinking + plan


def main() -> int:
    args = parse_args(sys.argv)
    model_path = args["model_path"].lower()
    backend = args["backend"]
    num_iterations = max(1, int(args["num_iterations"]))
    size = "e4b" if "e4b" in model_path or "4b" in model_path else "e2b"

    emit("WARNING: All log messages before absl::InitializeLog() is called are written to STDERR")
    user_count = 0
    turn_count = 0
    current_iteration = 0

    while current_iteration < num_iterations:
        current_iteration += 1
        user_count = 0
        emit("I0000 00:00:0000000000.000000 mock litert_lm_lib.cc:775] Running multi-turns conversation")
        emit(PROMPT, newline=False)

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
            if "TASK_KIND=robot_skill_chain" in last_user:
                emit_streamed_line(_mock_skill_plan_response(last_user))
            else:
                emit_streamed_line(
                    f"[mock {size} {backend}] turn={turn_count} users={user_count} reply_to={last_user}"
                )
            emit(PROMPT, newline=False)
        else:
            return 0

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
