<litert-server-bootstrap>
<conversation>
<message role="system">
{{ROLE_MD}}

{{REGISTERED_SKILLS_MD}}

{{OUTPUT_CONTRACT_MD}}
</message>
<message role="user">
TASK_NAME={{TASK_NAME}}
TASK_DESCRIPTION={{TASK_DESCRIPTION}}
TASK_PARAMETERS_JSON={{TASK_PARAMETERS_JSON}}
TASK_INSTRUCTION={{TASK_INSTRUCTION}}
</message>
</conversation>
<task>
Think carefully first.
Then output the final JSON object only.
</task>
</litert-server-bootstrap>
