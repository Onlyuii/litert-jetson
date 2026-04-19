#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import time
import urllib.error
import urllib.request
from typing import Any


DEFAULT_CHAIN_TASKS: list[dict[str, Any]] = [
    {
        "name": "pick_and_place_block",
        "description": "先获取环境信息，再抓取物体并放置到目标位置。",
        "params": {
            "need_env": True,
            "initial_pose": {"x": 0.0, "y": 0.0, "z": 0.0},
            "move_arm_targets": [
                {"x": 0.42, "y": 0.15, "z": 0.30},
                {"x": -0.18, "y": 0.22, "z": 0.30},
            ],
            "gripper_actions": ["close", "open"],
        },
    },
    {
        "name": "mobile_pick_and_place",
        "description": "先移动机器人底盘到目标位姿，再抓取物体并放置到目标位置。",
        "params": {
            "base_pose": {"x": 1.2, "y": -0.4, "yaw": 1.57},
            "move_arm_targets": [
                {"x": 0.31, "y": 0.08, "z": 0.27},
                {"x": 0.05, "y": -0.26, "z": 0.34},
            ],
            "gripper_actions": ["close", "open"],
        },
    },
]


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Demo client for LLM-planned robot skill chains."
    )
    parser.add_argument("--base-url", default="http://127.0.0.1:8090")
    parser.add_argument("--api-key", default="")
    parser.add_argument("--verbose", action="store_true")
    parser.add_argument("--reset-before", action="store_true")
    parser.add_argument("--keep-session", action="store_true")
    parser.add_argument("--no-auto-retry", action="store_true")
    parser.add_argument("--max-steps", type=int, default=3)
    args = parser.parse_args()

    default_reset_before = args.reset_before
    default_reset_after = not args.keep_session and not args.reset_before

    for task in DEFAULT_CHAIN_TASKS:
        _print_task_header(
            task=task,
            reset_before=default_reset_before,
            reset_after=default_reset_after,
        )
        payload = {
            "task": _build_robot_chain_task_prompt(task),
            "max_steps": args.max_steps,
            "reset_before": default_reset_before,
            "verbose": args.verbose,
            "auto_retry_after_reset": not args.no_auto_retry,
        }

        print("[状态] LLM 正在规划技能链...", flush=True)
        request_started_at = time.time()
        response = _post_json_stream(
            args.base_url,
            "/run_live",
            payload,
            args.api_key,
            _handle_live_event,
        )
        client_elapsed = round(time.time() - request_started_at, 6)
        if not response.get("ok"):
            _print_error_response(response=response, client_elapsed=client_elapsed)
            if default_reset_after:
                _reset_session_after_task(args.base_url, args.api_key)
            continue

        _print_robot_chain_result(task=task, response=response, client_elapsed=client_elapsed)
        if default_reset_after:
            _reset_session_after_task(args.base_url, args.api_key)

    return 0


def _build_robot_chain_task_prompt(task: dict[str, Any]) -> str:
    return (
        "<robot-skill-chain-task>\n"
        "TASK_KIND=robot_skill_chain\n"
        f"TASK_NAME={task['name']}\n"
        f"TASK_DESCRIPTION={task['description']}\n"
        f"TASK_PARAMETERS_JSON={json.dumps(task['params'], ensure_ascii=False)}\n"
        "</robot-skill-chain-task>"
    )


def _print_task_header(*, task: dict[str, Any], reset_before: bool, reset_after: bool) -> None:
    print(f"\n=== Robot Chain Task: {task['name']} ===")
    print(f"[目标] {task['description']}")
    print(f"[参数] {json.dumps(task['params'], ensure_ascii=False)}")
    if reset_before and reset_after:
        mode = "独立任务（前后重置）"
    elif reset_before:
        mode = "独立任务（前重置）"
    elif reset_after:
        mode = "独立任务（后重置）"
    else:
        mode = "连续工作流（保持会话）"
    print(f"[模式] {mode}")


def _print_robot_chain_result(*, task: dict[str, Any], response: dict[str, Any], client_elapsed: float) -> None:
    planned_skill_calls = response.get("planned_skill_calls") or []
    skill_results = response.get("skill_results") or []
    stats = response.get("stats") or {}

    print("[状态] 执行完成")
    if planned_skill_calls:
        print("\n[规划队列]")
        for index, skill_call in enumerate(planned_skill_calls, start=1):
            print(
                f"  {index}. {skill_call.get('skill_name', 'unknown')}"
                f"({_format_arguments(skill_call.get('arguments') or {})})"
            )

    print("\n[执行结果]")
    for index, result in enumerate(skill_results, start=1):
        simplified = _simplify_skill_result(
            result.get("result"),
            result.get("arguments") or {},
        )
        print(
            f"  {index}. {result.get('skill_name', 'unknown')}"
            f" | 状态={result.get('status', 'unknown')}"
            f" | 成功={'是' if result.get('ok') else '否'}"
            f" | 结果={_format_value(simplified)}"
        )

    print("\n[统计]")
    print(f"  规划推理次数: {stats.get('llm_inference_count', 0)}")
    print(f"  规划推理耗时: {float(stats.get('llm_inference_seconds', 0.0) or 0.0):.3f}s")
    print(f"  规划步数: {stats.get('planned_skill_count', 0)}")
    print(f"  实际执行步数: {stats.get('skill_execution_count', 0)}")
    print(f"  skill执行总耗时: {float(stats.get('skill_execution_seconds', 0.0) or 0.0):.3f}s")
    print(f"  服务总耗时: {float(stats.get('service_elapsed_seconds', 0.0) or 0.0):.3f}s")
    print(f"  客户端总等待: {client_elapsed:.3f}s")
    print(f"  最终摘要: {response.get('final_message', '')}")


def _handle_live_event(event: dict[str, Any]) -> None:
    event_type = str(event.get("event", ""))
    if event_type == "attempt_started":
        print(f"[动作] 开始第 {event.get('attempt', '?')} 次服务尝试", flush=True)
        return
    if event_type == "attempt_completed":
        print(
            f"[动作] 第 {event.get('attempt', '?')} 次服务尝试完成"
            f"（{float(event.get('elapsed_seconds', 0.0) or 0.0):.3f}s）",
            flush=True,
        )
        return
    if event_type == "inference_started":
        print("[动作] LLM 正在生成技能队列", flush=True)
        return
    if event_type == "inference_completed":
        print(
            f"[动作] 技能链规划完成，耗时 {float(event.get('inference_seconds', 0.0) or 0.0):.3f}s"
            f"，类型 {event.get('response_type', 'unknown')}",
            flush=True,
        )
        return
    if event_type == "json_format_corrected":
        corrected_json = str(event.get("corrected_json") or "").strip()
        print("[动作] JSON 格式检查器已自动修正输出", flush=True)
        if corrected_json:
            print("[修正后的技能链 JSON]", flush=True)
            print(corrected_json, flush=True)
        return
    if event_type == "protocol_retry":
        reason = str(event.get("validation_error") or "输出格式不符合技能链要求")
        print(
            f"[动作] 技能链规划不合格，正在进行第 {event.get('retry_count', '?')} 次纠错重试"
            f"：{reason}",
            flush=True,
        )
        raw_response = str(event.get("raw_response") or "").strip()
        if raw_response:
            print("[模型原始输出]", flush=True)
            print(raw_response, flush=True)
        return
    if event_type == "invalid_model_output":
        reason = str(event.get("validation_error") or "模型输出不符合要求")
        print(f"[警告] 检测到不合格的模型输出：{reason}", flush=True)
        raw_response = str(event.get("raw_response") or "").strip()
        if raw_response:
            print("[模型原始输出]", flush=True)
            print(raw_response, flush=True)
        return
    if event_type == "skill_plan_ready":
        planned = event.get("planned_skill_calls") or []
        print(f"[动作] 已生成技能队列，共 {len(planned)} 步", flush=True)
        for index, skill_call in enumerate(planned, start=1):
            print(
                f"  [计划 {index}] {skill_call.get('skill_name', 'unknown')}"
                f"({_format_arguments(skill_call.get('arguments') or {})})",
                flush=True,
            )
        return
    if event_type == "skill_execution_started":
        queue_index = event.get("queue_index", "?")
        queue_total = event.get("queue_total", "?")
        print(
            f"[动作] 执行第 {queue_index}/{queue_total} 步: "
            f"{event.get('skill_name', 'unknown')}"
            f"，参数 {_format_arguments(event.get('arguments') or {})}",
            flush=True,
        )
        return
    if event_type == "skill_execution_completed":
        queue_index = event.get("queue_index", "?")
        queue_total = event.get("queue_total", "?")
        print(
            f"[动作] 第 {queue_index}/{queue_total} 步完成"
            f"，状态 {event.get('status', 'unknown')}"
            f"，耗时 {float(event.get('elapsed_seconds', 0.0) or 0.0):.3f}s",
            flush=True,
        )
        return
    if event_type == "attempt_failed":
        print(f"[警告] 本轮失败: {event.get('error', '')}", flush=True)
        return
    if event_type == "reset_started":
        reason = event.get("reason", "")
        if reason:
            print(f"[动作] 正在重置常驻会话: {reason}", flush=True)
        else:
            print("[动作] 正在重置常驻会话", flush=True)
        return
    if event_type == "reset_completed":
        reason = event.get("reason", "")
        suffix = f": {reason}" if reason else ""
        print(
            f"[动作] 重置完成{suffix}，方式 {_format_reset_mode(event.get('reset_mode'))}"
            f"（{float(event.get('reset_seconds', 0.0) or 0.0):.3f}s）",
            flush=True,
        )
        return


def _simplify_skill_result(result: object, arguments: dict[str, Any]) -> object:
    if not isinstance(result, dict):
        return result
    simplified = dict(result)
    simplified.pop("ok", None)
    simplified.pop("status", None)
    for key, value in arguments.items():
        if key in simplified and simplified[key] == value:
            simplified.pop(key)
    return simplified or None


def _format_arguments(arguments: dict[str, Any]) -> str:
    if not arguments:
        return "{}"
    return ", ".join(f"{key}={_format_scalar(value)}" for key, value in arguments.items())


def _format_scalar(value: object) -> str:
    if isinstance(value, str):
        return repr(value)
    return str(value)


def _format_value(value: object) -> str:
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False)
    return str(value)


def _print_error_response(*, response: dict[str, Any], client_elapsed: float) -> None:
    error = response.get("error") or {}
    print("[状态] 执行失败")
    print(f"[错误] {error.get('message', response)}")
    print(f"[客户端总等待] {client_elapsed:.3f}s")


def _reset_session_after_task(base_url: str, api_key: str) -> None:
    print("[动作] 当前任务结束，正在重置常驻会话...", flush=True)
    started_at = time.time()
    try:
        response = _post_json(base_url, "/reset", {}, api_key)
        elapsed = round(time.time() - started_at, 6)
        if response.get("ok"):
            print(
                f"[动作] 常驻会话重置完成，方式 {_format_reset_mode(response.get('mode'))}"
                f"（{elapsed:.3f}s）",
                flush=True,
            )
            return
        print(f"[警告] 常驻会话重置返回异常（{elapsed:.3f}s）: {response}", flush=True)
    except SystemExit as exc:
        elapsed = round(time.time() - started_at, 6)
        print(f"[警告] 常驻会话重置失败（{elapsed:.3f}s）: {exc}", flush=True)


def _post_json_stream(base_url: str, path: str, payload: dict[str, Any], api_key: str, on_event) -> dict[str, Any]:
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        base_url.rstrip("/") + path,
        data=data,
        headers={
            "Content-Type": "application/json; charset=utf-8",
            "Authorization": f"Bearer {api_key}" if api_key else "",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=600) as response:
            final_payload = {"ok": False, "error": {"message": "No response body received."}}
            for raw_line in response:
                line = raw_line.decode("utf-8").strip()
                if not line:
                    continue
                event = json.loads(line)
                on_event(event)
                if event.get("event") == "result":
                    final_payload = event.get("response") or final_payload
                elif event.get("event") == "error":
                    final_payload = event.get("response") or final_payload
            return final_payload
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise SystemExit(f"HTTP {exc.code}: {body}") from exc
    except urllib.error.URLError as exc:
        raise SystemExit(f"Failed to connect to skill service: {exc}") from exc


def _post_json(base_url: str, path: str, payload: dict[str, Any], api_key: str) -> dict[str, Any]:
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        base_url.rstrip("/") + path,
        data=data,
        headers={
            "Content-Type": "application/json; charset=utf-8",
            "Authorization": f"Bearer {api_key}" if api_key else "",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=120) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise SystemExit(f"HTTP {exc.code}: {body}") from exc
    except urllib.error.URLError as exc:
        raise SystemExit(f"Failed to connect to skill service: {exc}") from exc


def _format_reset_mode(mode: object) -> str:
    if mode == "soft":
        return "soft-reset"
    if mode == "hard":
        return "hard-restart"
    return str(mode or "unknown")


if __name__ == "__main__":
    raise SystemExit(main())
