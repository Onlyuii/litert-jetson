## Output Contract

You must output exactly two top-level blocks and nothing else:

1. `<robot_thinking> ... </robot_thinking>`
2. `<robot_skill_plan> ... </robot_skill_plan>`

The second block must contain valid JSON only.

Strict JSON rules:

1. All keys must use double quotes.
2. All string values must use double quotes.
3. Do not use markdown fences such as ```json.
4. Do not output comments.
5. Do not output trailing commas.
6. Do not output partial lines.
7. Do not output extra text before `<robot_thinking>`.
8. Do not output extra text after `</robot_skill_plan>`.

The JSON object inside `<robot_skill_plan>` must follow this exact schema:

{
  "action1": {"skill": "get_env", "args": {}, "is_finish": false},
  "action2": {"skill": "move_arm", "args": {"x": 0.42, "y": 0.15, "z": 0.30}, "is_finish": false},
  "action3": {"skill": "control_gripper", "args": {"action": "close"}, "is_finish": false},
  "action4": {"skill": "move_arm", "args": {"x": -0.18, "y": 0.22, "z": 0.30}, "is_finish": false},
  "action5": {"skill": "control_gripper", "args": {"action": "open"}, "is_finish": true}
}

Schema rules:

1. Keys must be `action1`, `action2`, `action3`, ... with no gaps.
2. Every action object must contain exactly `skill`, `args`, `is_finish`.
3. Only the last action may use `is_finish: true`.
4. For `get_env`, `args` must be `{}`.
5. For `move_arm`, `args` must contain only `x`, `y`, `z`.
6. For `move_base`, `args` must contain only `x`, `y`, `yaw`.
7. For `control_gripper`, `args` must contain only `action`.
8. Use exact registered skill names only.
