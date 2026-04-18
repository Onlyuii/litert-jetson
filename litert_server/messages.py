from __future__ import annotations

import re
from dataclasses import dataclass


class MessageError(ValueError):
    """Raised when the incoming OpenAI-style messages cannot be handled."""


@dataclass(frozen=True)
class Message:
    role: str
    content: str


_TAG_RE = re.compile(r"</?message[^>]*>")


def _normalize_line_breaks(text: str) -> str:
    return text.replace("\r\n", "\n").replace("\r", "\n")


def _sanitize_content(text: str) -> str:
    text = _normalize_line_breaks(text).strip()
    return _TAG_RE.sub("", text)


def extract_text_content(raw_message: dict) -> str:
    content = raw_message.get("content", "")
    if isinstance(content, str):
        return _sanitize_content(content)
    if content is None:
        return ""
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
                continue
            if not isinstance(item, dict):
                raise MessageError("messages[].content contains an unsupported part.")
            part_type = item.get("type", "text")
            if part_type not in ("text", "input_text"):
                raise MessageError(
                    "This runtime-only wrapper currently supports text content only."
                )
            text = item.get("text")
            if not isinstance(text, str):
                raise MessageError("Text content part is missing its text field.")
            parts.append(text)
        return _sanitize_content("".join(parts))
    raise MessageError("messages[].content must be a string or a list of text parts.")


def normalize_messages(
    raw_messages: object,
    hidden_system_prompt: str = "",
) -> list[Message]:
    if not isinstance(raw_messages, list) or not raw_messages:
        raise MessageError("messages must be a non-empty array.")

    normalized: list[Message] = []
    if hidden_system_prompt.strip():
        normalized.append(Message(role="system", content=_sanitize_content(hidden_system_prompt)))

    for raw in raw_messages:
        if not isinstance(raw, dict):
            raise MessageError("Each message must be an object.")
        role = raw.get("role")
        if role not in {"system", "user", "assistant", "tool", "developer"}:
            raise MessageError(f"Unsupported message role: {role!r}.")
        content = extract_text_content(raw)
        normalized.append(Message(role=role, content=content))

    if not normalized:
        raise MessageError("messages must not be empty.")
    if normalized[-1].role not in {"user", "tool"}:
        raise MessageError("The final message must be a user or tool message.")

    return normalized


def render_bootstrap_prompt(messages: list[Message]) -> str:
    lines = [
        "<litert-server-bootstrap>",
        "You are answering a chat request that has been adapted into a single prompt.",
        "Follow every system or developer instruction in the transcript.",
        "Reply only with the next assistant message.",
        "<conversation>",
    ]

    for message in messages:
        lines.append(f'<message role="{message.role}">')
        lines.append(message.content)
        lines.append("</message>")

    lines.extend(
        [
            "</conversation>",
            "<task>",
            "Write the next assistant reply to the final message above.",
            "Do not repeat XML tags, role names, or wrapper instructions.",
            "</task>",
            "</litert-server-bootstrap>",
        ]
    )
    return "\n".join(lines).strip()


def incremental_user_turn(
    existing_transcript: list[Message],
    incoming_messages: list[Message],
) -> str | None:
    if len(incoming_messages) != len(existing_transcript) + 1:
        return None
    if incoming_messages[:-1] != existing_transcript:
        return None
    next_message = incoming_messages[-1]
    if next_message.role != "user":
        return None
    return next_message.content


def visible_transcript(messages: list[Message], hidden_system_prompt: str) -> list[Message]:
    if not hidden_system_prompt.strip():
        return list(messages)
    if messages and messages[0].role == "system" and messages[0].content == _sanitize_content(
        hidden_system_prompt
    ):
        return messages[1:]
    return list(messages)


def estimate_token_count(text: str) -> int:
    chunks = re.findall(r"\w+|[^\w\s]", text, flags=re.UNICODE)
    return max(1, len(chunks)) if text else 0
