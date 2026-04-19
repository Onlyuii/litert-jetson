<litert-server-bootstrap>
<conversation>
<message role="system">
{{ROLE_MD}}

{{REGISTERED_SKILLS_MD}}

{{OUTPUT_CONTRACT_MD}}

## Planning Checklist

1. Read the structured task.
2. Decide the minimal valid ordered skill sequence.
3. Re-check all numeric values from TASK_PARAMETERS_JSON.
4. Before writing the final JSON, verify every string has double quotes.
5. Before finishing, verify the last action has `"is_finish": true` and all previous actions have `"is_finish": false`.
</message>
<message role="user">
TASK_NAME={{TASK_NAME}}
TASK_DESCRIPTION={{TASK_DESCRIPTION}}
TASK_PARAMETERS_JSON={{TASK_PARAMETERS_JSON}}
TASK_INSTRUCTION={{TASK_INSTRUCTION}}
</message>
</conversation>
<task>
Write the full `<robot_thinking>` block first, then the full `<robot_skill_plan>` block.
Return exactly those two blocks only.
</task>
</litert-server-bootstrap>
