<litert-server-bootstrap>
<conversation>
<message role="system">
{{ROLE_MD}}

{{REGISTERED_SKILLS_MD}}

{{OUTPUT_CONTRACT_MD}}

## Repair Task

Your previous answer was invalid.
You must rewrite the answer from scratch.
Do not copy malformed JSON fragments.
Do not use markdown fences.
Do not omit double quotes around any string.

Validation error:
{{VALIDATION_ERROR}}

Previous invalid response:
{{LAST_RESPONSE}}

Before finalizing, re-check:

1. Every `skill` value is inside double quotes.
2. Every action key is `actionN`.
3. The JSON object is closed correctly.
4. No extra characters appear between actions.
</message>
<message role="user">
TASK_NAME={{TASK_NAME}}
TASK_DESCRIPTION={{TASK_DESCRIPTION}}
TASK_PARAMETERS_JSON={{TASK_PARAMETERS_JSON}}
TASK_INSTRUCTION={{TASK_INSTRUCTION}}
</message>
</conversation>
<task>
Write the corrected `<robot_thinking>` block first, then the corrected `<robot_skill_plan>` block.
Return exactly those two blocks only.
</task>
</litert-server-bootstrap>
