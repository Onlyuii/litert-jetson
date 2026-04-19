from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class RobotSkillJsonCheckResult:
    calls: list[dict[str, Any]] | None
    corrected_json: str | None
    corrected: bool
    error: str | None = None


def check_and_repair_robot_skill_plan(body: str) -> RobotSkillJsonCheckResult:
    strict_calls = _try_parse_strict_json(body)
    if strict_calls is not None:
        return RobotSkillJsonCheckResult(
            calls=strict_calls,
            corrected_json=None,
            corrected=False,
            error=None,
        )

    repaired_calls = _repair_action_calls(body)
    if repaired_calls is None:
        return RobotSkillJsonCheckResult(
            calls=None,
            corrected_json=None,
            corrected=False,
            error="Unable to repair robot_skill_plan JSON.",
        )

    corrected_json = _render_calls_as_json(repaired_calls)
    return RobotSkillJsonCheckResult(
        calls=repaired_calls,
        corrected_json=corrected_json,
        corrected=True,
        error=None,
    )


def _try_parse_strict_json(body: str) -> list[dict[str, Any]] | None:
    try:
        parsed = json.loads(body)
    except json.JSONDecodeError:
        return None
    return _plan_dict_to_calls(parsed)


def _plan_dict_to_calls(parsed: Any) -> list[dict[str, Any]] | None:
    if not isinstance(parsed, dict) or not parsed:
        return None

    action_items: list[tuple[int, dict[str, Any]]] = []
    for key, value in parsed.items():
        if not isinstance(key, str) or not isinstance(value, dict):
            return None
        key_match = re.fullmatch(r"action(\d+)", key.strip(), flags=re.IGNORECASE)
        if not key_match:
            return None
        action_items.append((int(key_match.group(1)), value))

    action_items.sort(key=lambda item: item[0])
    calls: list[dict[str, Any]] = []
    expected_index = 1
    total_actions = len(action_items)

    for action_index, action_value in action_items:
        if action_index != expected_index:
            return None
        expected_index += 1

        skill_name = action_value.get("skill")
        arguments = action_value.get("args")
        is_finish = action_value.get("is_finish")
        if not isinstance(skill_name, str) or not skill_name.strip():
            return None
        if arguments is None:
            arguments = {}
        if not isinstance(arguments, dict):
            return None
        if not isinstance(is_finish, bool):
            return None
        if action_index < total_actions and is_finish:
            return None
        if action_index == total_actions and not is_finish:
            return None

        calls.append(
            {
                "skill_name": skill_name.strip(),
                "arguments": dict(arguments),
            }
        )

    return calls


def _repair_action_calls(body: str) -> list[dict[str, Any]] | None:
    fragments = _extract_action_fragments(body)
    if not fragments:
        return None

    repaired_calls: list[dict[str, Any]] = []
    for fragment in fragments:
        skill_name = _extract_skill_name(fragment)
        if skill_name is None:
            return None
        repaired_calls.append(
            {
                "skill_name": skill_name,
                "arguments": _extract_args(fragment),
            }
        )

    return repaired_calls or None


def _extract_action_fragments(body: str) -> list[str]:
    action_start_re = re.compile(r'"?(?:action)?\d+"?\s*:\s*\{', flags=re.IGNORECASE)
    fragments: list[str] = []
    for match in action_start_re.finditer(body):
        brace_start = body.find("{", match.start())
        if brace_start < 0:
            continue
        fragment = _extract_balanced_object_loose(body, brace_start)
        if fragment:
            fragments.append(fragment)
    return fragments


def _extract_balanced_object_loose(text: str, start_index: int) -> str | None:
    if start_index < 0 or start_index >= len(text) or text[start_index] != "{":
        return None

    depth = 0
    for index in range(start_index, len(text)):
        char = text[index]
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return text[start_index : index + 1]
    return text[start_index:].strip() or None


def _extract_skill_name(fragment: str) -> str | None:
    patterns = (
        r'"skill"\s*:\s*"([A-Za-z_][A-Za-z0-9_]*)"',
        r'"skill"\s*:\s*([A-Za-z_][A-Za-z0-9_]*)"?',
        r'skill\s*:\s*"([A-Za-z_][A-Za-z0-9_]*)"',
        r'skill\s*:\s*([A-Za-z_][A-Za-z0-9_]*)"?',
    )
    for pattern in patterns:
        match = re.search(pattern, fragment)
        if match:
            return match.group(1)
    return None


def _extract_args(fragment: str) -> dict[str, Any]:
    args_match = re.search(r'"?args"?\s*:\s*\{', fragment, flags=re.IGNORECASE)
    if not args_match:
        return {}
    brace_start = fragment.find("{", args_match.start())
    if brace_start < 0:
        return {}

    args_text = _extract_balanced_object_loose(fragment, brace_start)
    if not args_text:
        return {}

    repaired = _repair_shallow_object(args_text)
    if repaired is None or not isinstance(repaired, dict):
        return {}
    return repaired


def _repair_shallow_object(text: str) -> dict[str, Any] | None:
    candidate = text.strip()
    if not candidate:
        return None

    try:
        parsed = json.loads(candidate)
        if isinstance(parsed, dict):
            return parsed
    except json.JSONDecodeError:
        pass

    candidate = re.sub(r'([,{]\s*)([A-Za-z_][A-Za-z0-9_]*)(\s*:)', r'\1"\2"\3', candidate)
    candidate = re.sub(r'(:\s*)([A-Za-z_][A-Za-z0-9_]*)(\s*[,}])', _quote_bare_string_value, candidate)
    candidate = re.sub(r',\s*"\s*(?="?[A-Za-z_][A-Za-z0-9_]*"?\s*:)', ", ", candidate)

    try:
        parsed = json.loads(candidate)
    except json.JSONDecodeError:
        return None
    if isinstance(parsed, dict):
        return parsed
    return None


def _quote_bare_string_value(match: re.Match[str]) -> str:
    token = match.group(2)
    if token in {"true", "false", "null"}:
        return f"{match.group(1)}{token}{match.group(3)}"
    return f'{match.group(1)}"{token}"{match.group(3)}'


def _render_calls_as_json(calls: list[dict[str, Any]]) -> str:
    payload: dict[str, Any] = {}
    total = len(calls)
    for index, call in enumerate(calls, start=1):
        payload[f"action{index}"] = {
            "skill": call["skill_name"],
            "args": dict(call.get("arguments") or {}),
            "is_finish": index == total,
        }
    return json.dumps(payload, ensure_ascii=False, indent=2)
