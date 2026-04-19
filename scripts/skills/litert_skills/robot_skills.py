from __future__ import annotations

from typing import Any

from .agent import SkillRegistry


def move_arm(x: float, y: float, z: float) -> dict[str, Any]:
    """Move the robot arm to a target 3D position."""
    return {
        "ok": True,
        "status": "completed",
        "command": "move_arm",
        "target_pose": {
            "x": float(x),
            "y": float(y),
            "z": float(z),
        },
        "simulated": True,
    }


def control_gripper(action: str) -> dict[str, Any]:
    """Open or close the robot gripper."""
    normalized = str(action).strip().lower()
    if normalized not in {"open", "close"}:
        raise RuntimeError(f"Unsupported gripper action: {action}")
    return {
        "ok": True,
        "status": "completed",
        "command": "control_gripper",
        "action": normalized,
        "simulated": True,
    }


def get_env() -> dict[str, Any]:
    """Capture the current environment observation."""
    return {
        "ok": True,
        "status": "completed",
        "command": "get_env",
        "observation": {
            "scene_id": "sim_env_001",
            "summary": "A tabletop scene with a robot arm, a red block, and a blue cup.",
        },
        "simulated": True,
    }


def move_base(x: float, y: float, yaw: float) -> dict[str, Any]:
    """Move the robot base to a planar pose."""
    return {
        "ok": True,
        "status": "completed",
        "command": "move_base",
        "target_pose": {
            "x": float(x),
            "y": float(y),
            "yaw": float(yaw),
        },
        "simulated": True,
    }


def register_robot_skills(registry: SkillRegistry) -> None:
    registry.register_function(
        move_arm,
        description="Move the robot arm to a target Cartesian position (x, y, z).",
        aliases=["reach_pose", "move_to_position", "arm_move", "机械臂移动", "移动机械臂"],
    )
    registry.register_function(
        control_gripper,
        description="Open or close the robot gripper.",
        aliases=["gripper", "open_gripper", "close_gripper", "夹爪控制", "打开夹爪", "闭合夹爪"],
    )
    registry.register_function(
        get_env,
        description="Capture the current environment observation.",
        aliases=["perceive_env", "capture_scene", "获取环境", "拍照", "观察环境"],
    )
    registry.register_function(
        move_base,
        description="Move the mobile base to a 2D pose (x, y, yaw).",
        aliases=["navigate_base", "drive_base", "底盘移动", "移动底盘", "导航到位姿"],
    )
