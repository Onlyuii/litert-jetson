from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Callable

from .config import ModelConfig, ServerConfig, build_models
from .messages import Message, incremental_user_turn, render_bootstrap_prompt
from .repl import LiteRTCliConfig, LiteRTCliSession


class SessionManagerError(RuntimeError):
    """Raised when a model session cannot be served."""


@dataclass
class ChatResult:
    model_id: str
    session_id: str
    content: str
    rebuilt: bool
    visible_prompt_text: str


@dataclass
class PreparedGeneration:
    model: ModelConfig
    session_id: str
    session_key: str
    record: "SessionRecord"
    prompt_text: str
    rebuilt: bool
    replaced: "SessionRecord | None"


@dataclass
class SessionRecord:
    session_key: str
    session_id: str
    model: ModelConfig
    cli: LiteRTCliSession
    transcript: list[Message] = field(default_factory=list)
    last_used_at: float = field(default_factory=time.time)
    created_at: float = field(default_factory=time.time)

    def close(self) -> None:
        self.cli.close()


class SessionManager:
    def __init__(self, config: ServerConfig):
        self._config = config
        self._models, self._aliases = build_models(config)
        self._lock = threading.Lock()
        self._sessions: dict[str, SessionRecord] = {}

    def model_list(self) -> list[ModelConfig]:
        return list(self._models.values())

    def resolve_model(self, requested_model: str | None) -> ModelConfig:
        model_name = requested_model or self._config.default_model
        canonical_id = self._aliases.get(model_name, self._aliases.get(model_name.lower()))
        if canonical_id is None:
            raise SessionManagerError(f"Unknown model: {model_name}")
        return self._models[canonical_id]

    def close_all(self) -> None:
        with self._lock:
            records = list(self._sessions.values())
            self._sessions.clear()
        for record in records:
            record.close()

    def session_count(self) -> int:
        with self._lock:
            self._purge_expired_locked()
            return len(self._sessions)

    def reset_session(self, model_name: str | None, session_id: str) -> None:
        try:
            model = self.resolve_model(model_name)
        except SessionManagerError:
            return

        self._discard_session_key(f"{model.id}:{session_id}")

    def generate(
        self,
        model_name: str | None,
        session_id: str,
        messages: list[Message],
    ) -> ChatResult:
        prepared = self._prepare_generation(model_name, session_id, messages)
        if prepared.replaced is not None:
            prepared.replaced.close()

        try:
            reply = prepared.record.cli.ask(prepared.prompt_text)
        except Exception:
            self._discard_prepared_generation(prepared)
            raise
        self._finalize_generation(prepared, messages, reply)
        return ChatResult(
            model_id=prepared.model.id,
            session_id=prepared.session_id,
            content=reply,
            rebuilt=prepared.rebuilt,
            visible_prompt_text=prepared.prompt_text,
        )

    def generate_stream(
        self,
        model_name: str | None,
        session_id: str,
        messages: list[Message],
        on_text: Callable[[str], None],
    ) -> ChatResult:
        prepared = self._prepare_generation(model_name, session_id, messages)
        if prepared.replaced is not None:
            prepared.replaced.close()

        try:
            reply = prepared.record.cli.ask_stream(prepared.prompt_text, on_text)
        except Exception:
            self._discard_prepared_generation(prepared)
            raise
        self._finalize_generation(prepared, messages, reply)
        return ChatResult(
            model_id=prepared.model.id,
            session_id=prepared.session_id,
            content=reply,
            rebuilt=prepared.rebuilt,
            visible_prompt_text=prepared.prompt_text,
        )

    def _prepare_generation(
        self,
        model_name: str | None,
        session_id: str,
        messages: list[Message],
    ) -> PreparedGeneration:
        model = self.resolve_model(model_name)
        session_key = f"{model.id}:{session_id}"
        prompt_text: str
        record: SessionRecord
        replaced: SessionRecord | None = None

        with self._lock:
            self._purge_expired_locked()
            record = self._sessions.get(session_key)
            prompt_text = ""
            rebuilt = False

            if record is not None and record.model.id != model.id:
                replaced = self._sessions.pop(session_key)
                record = None

            if record is not None:
                extension = incremental_user_turn(record.transcript, messages)
                if extension is not None:
                    prompt_text = extension
                else:
                    rebuilt = True
                    replaced = self._sessions.pop(session_key)
                    record = None

            if record is None:
                self._ensure_capacity_locked()
                record = SessionRecord(
                    session_key=session_key,
                    session_id=session_id,
                    model=model,
                    cli=self._create_cli(model),
                )
                self._sessions[session_key] = record
                prompt_text = self._bootstrap_prompt_for(messages)
            else:
                rebuilt = False

            record.last_used_at = time.time()

        return PreparedGeneration(
            model=model,
            session_id=session_id,
            session_key=session_key,
            record=record,
            prompt_text=prompt_text,
            rebuilt=rebuilt,
            replaced=replaced,
        )

    def _finalize_generation(
        self,
        prepared: PreparedGeneration,
        messages: list[Message],
        reply: str,
    ) -> None:
        with self._lock:
            current = self._sessions.get(prepared.session_key)
            if current is prepared.record:
                prepared.record.transcript = list(messages) + [Message(role="assistant", content=reply)]
                prepared.record.last_used_at = time.time()

    def _discard_prepared_generation(self, prepared: PreparedGeneration) -> None:
        self._discard_session_key(prepared.session_key, expected=prepared.record)

    def _discard_session_key(
        self,
        session_key: str,
        expected: SessionRecord | None = None,
    ) -> None:
        record_to_close: SessionRecord | None = None
        with self._lock:
            current = self._sessions.get(session_key)
            if current is None:
                record_to_close = expected
            elif expected is None or current is expected:
                record_to_close = self._sessions.pop(session_key)

        if record_to_close is not None:
            record_to_close.close()

    def _bootstrap_prompt_for(self, messages: list[Message]) -> str:
        if len(messages) == 1 and messages[0].role == "user" and not self._config.default_system_prompt:
            return messages[0].content
        return render_bootstrap_prompt(messages)

    def _create_cli(self, model: ModelConfig) -> LiteRTCliSession:
        cli_config = LiteRTCliConfig(
            worker_binary=self._config.worker_binary,
            model_path=model.path,
            backend=self._config.backend,
            startup_timeout_seconds=self._config.startup_timeout_seconds,
            request_timeout_seconds=self._config.request_timeout_seconds,
            max_output_tokens=self._config.max_output_tokens,
        )
        return LiteRTCliSession(cli_config)

    def _ensure_capacity_locked(self) -> None:
        while len(self._sessions) >= self._config.max_sessions:
            victim_key = self._choose_eviction_victim_locked()
            if victim_key is None:
                raise SessionManagerError(
                    "All live sessions are busy; try again later or increase max sessions."
                )
            victim = self._sessions.pop(victim_key)
            victim.close()

    def _choose_eviction_victim_locked(self) -> str | None:
        unlocked_items = [
            (key, record)
            for key, record in self._sessions.items()
            if not record.cli._lock.locked()  # pylint: disable=protected-access
        ]
        if not unlocked_items:
            return None
        unlocked_items.sort(key=lambda item: item[1].last_used_at)
        return unlocked_items[0][0]

    def _purge_expired_locked(self) -> None:
        now = time.time()
        expired_keys = [
            key
            for key, record in self._sessions.items()
            if now - record.last_used_at > self._config.session_ttl_seconds
            and not record.cli._lock.locked()  # pylint: disable=protected-access
        ]
        for key in expired_keys:
            record = self._sessions.pop(key)
            record.close()
