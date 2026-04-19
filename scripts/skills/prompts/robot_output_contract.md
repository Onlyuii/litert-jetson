## Output Contract

Output exactly one JSON object and nothing else.

Use this schema:

{
  "skills": [
    {"skill": "get_env"},
    {"skill": "move_arm"},
    {"skill": "control_gripper"},
    {"skill": "move_arm"},
    {"skill": "control_gripper"}
  ]
}

Rules:

1. The top-level object must contain exactly one key: "skills".
2. "skills" must be a JSON array.
3. Each array item must be a JSON object.
4. Each skill object must contain exactly one required key: "skill".
5. Do not output markdown fences, XML tags, comments, or explanation text.
6. Use valid JSON with double quotes.
7. Use exact registered skill names only.
8. Do not output private thinking.
