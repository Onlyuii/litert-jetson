from __future__ import annotations

import json
import time
import traceback
import uuid
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import unquote

from .config import ServerConfig
from .messages import MessageError, estimate_token_count, normalize_messages, visible_transcript
from .sessions import ChatResult, SessionManager, SessionManagerError


def _json_dumps(payload: object) -> bytes:
    return json.dumps(payload, ensure_ascii=False).encode("utf-8")


def _chunk_text(text: str, chunk_size: int = 24) -> list[str]:
    if len(text) <= chunk_size:
        return [text]
    chunks: list[str] = []
    current = ""
    for char in text:
        current += char
        if len(current) >= chunk_size:
            chunks.append(current)
            current = ""
    if current:
        chunks.append(current)
    return chunks


class ClientDisconnectedError(ConnectionError):
    """Raised when the HTTP client closes a streaming response early."""


class LiteRTOpenAIServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, server_address: tuple[str, int], config: ServerConfig):
        self.config = config
        self.session_manager = SessionManager(config)
        super().__init__(server_address, LiteRTRequestHandler)


class LiteRTRequestHandler(BaseHTTPRequestHandler):
    server: LiteRTOpenAIServer

    def log_message(self, fmt: str, *args: object) -> None:
        print(
            f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {self.client_address[0]} "
            f"{self.command} {self.path} - {fmt % args}",
            flush=True,
        )

    def do_OPTIONS(self) -> None:
        self.send_response(HTTPStatus.NO_CONTENT)
        self._send_common_headers()
        self.end_headers()

    def do_HEAD(self) -> None:
        self._dispatch_request(expect_body=False)

    def do_GET(self) -> None:
        self._dispatch_request(expect_body=False)

    def do_POST(self) -> None:
        self._dispatch_request(expect_body=True)

    def _dispatch_request(self, expect_body: bool) -> None:
        path = self._request_path()

        if self.command in {"GET", "HEAD"}:
            if path in {"/", "/v1"}:
                return self._handle_root()
            if path in {"/health", "/healthz"}:
                return self._handle_health()
            if path in {"/models", "/v1/models"}:
                if not self._check_auth():
                    return
                return self._handle_models()
            if path.startswith("/models/") or path.startswith("/v1/models/"):
                if not self._check_auth():
                    return
                return self._handle_model_detail()
            self._send_error_json(HTTPStatus.NOT_FOUND, "not_found", "Unknown endpoint.")
            return

        if self.command == "POST":
            if path in {"/chat/completions", "/v1/chat/completions"}:
                if not self._check_auth():
                    return
                return self._handle_chat_completions()
            if path in {"/responses", "/v1/responses"}:
                if not self._check_auth():
                    return
                return self._handle_responses()
            if path in {"/embeddings", "/v1/embeddings"}:
                if not self._check_auth():
                    return
                return self._send_error_json(
                    HTTPStatus.BAD_REQUEST,
                    "unsupported_request",
                    "Embeddings are not supported by this runtime-only wrapper.",
                )
            if expect_body:
                self._send_error_json(HTTPStatus.NOT_FOUND, "not_found", "Unknown endpoint.")
                return

        self._send_error_json(HTTPStatus.METHOD_NOT_ALLOWED, "method_not_allowed", "Method not allowed.")

    def _request_path(self) -> str:
        return self.path.split("?", 1)[0]

    def _handle_root(self) -> None:
        payload = {
            "object": "service",
            "id": "litert-server",
            "status": "ok",
            "api": "openai-compatible",
            "default_model": self.server.config.default_model,
            "endpoints": {
                "models": "/v1/models",
                "chat_completions": "/v1/chat/completions",
                "responses": "/v1/responses",
                "health": "/health",
            },
        }
        self._send_json(HTTPStatus.OK, payload)

    def _handle_health(self) -> None:
        payload = {
            "status": "ok",
            "active_sessions": self.server.session_manager.session_count(),
            "max_sessions": self.server.config.max_sessions,
            "backend": self.server.config.backend,
        }
        self._send_json(HTTPStatus.OK, payload)

    def _handle_models(self) -> None:
        data = []
        for model in self.server.session_manager.model_list():
            data.append(self._build_model_payload(model.id))
        self._send_json(HTTPStatus.OK, {"object": "list", "data": data})

    def _handle_model_detail(self) -> None:
        path = self._request_path()
        prefix = "/v1/models/" if path.startswith("/v1/models/") else "/models/"
        requested_model = unquote(path[len(prefix) :])
        if not requested_model:
            self._send_error_json(HTTPStatus.NOT_FOUND, "not_found", "Unknown model.")
            return

        try:
            model = self.server.session_manager.resolve_model(requested_model)
        except SessionManagerError:
            self._send_error_json(HTTPStatus.NOT_FOUND, "not_found", f"Unknown model: {requested_model}")
            return

        self._send_json(HTTPStatus.OK, self._build_model_payload(model.id))

    def _build_model_payload(self, model_id: str) -> dict:
        return {
            "id": model_id,
            "object": "model",
            "created": 0,
            "owned_by": "litert-runtime",
            "root": model_id,
            "parent": None,
            "permission": [],
            "capabilities": {
                "chat_completions": True,
                "responses": True,
                "streaming": True,
                "embeddings": False,
                "tools": False,
            },
        }

    def _handle_chat_completions(self) -> None:
        body = self._read_json_body()
        if body is None:
            return

        if body.get("n", 1) != 1:
            self._send_error_json(
                HTTPStatus.BAD_REQUEST,
                "unsupported_request",
                "Only n=1 is supported by this server.",
            )
            return

        body, _ = self._sanitize_request_body(body)

        try:
            messages = normalize_messages(
                body.get("messages"),
                hidden_system_prompt=self.server.config.default_system_prompt,
            )
        except MessageError as exc:
            self._send_error_json(HTTPStatus.BAD_REQUEST, "invalid_request_error", str(exc))
            return

        session_id = self._resolve_session_id(body)
        request_id = f"chatcmpl-{uuid.uuid4().hex}"
        created = int(time.time())

        try:
            if body.get("stream"):
                self._send_streaming_chat_response(
                    request_id=request_id,
                    created=created,
                    requested_model=body.get("model"),
                    session_id=session_id,
                    messages=messages,
                )
                return

            result = self.server.session_manager.generate(
                model_name=body.get("model"),
                session_id=session_id,
                messages=messages,
            )
        except SessionManagerError as exc:
            self._send_error_json(HTTPStatus.BAD_REQUEST, "invalid_request_error", str(exc))
            return
        except Exception as exc:  # pragma: no cover - defensive boundary
            traceback.print_exc()
            self._send_error_json(
                HTTPStatus.INTERNAL_SERVER_ERROR,
                "server_error",
                f"LiteRT worker failed: {exc}",
            )
            return

        self._send_json(
            HTTPStatus.OK,
            self._build_chat_completion_payload(
                request_id=request_id,
                created=created,
                result=result,
                messages=messages,
            ),
        )

    def _handle_responses(self) -> None:
        body = self._read_json_body()
        if body is None:
            return

        body, _ = self._sanitize_request_body(body)

        try:
            messages = self._normalize_responses_input(body)
        except MessageError as exc:
            self._send_error_json(HTTPStatus.BAD_REQUEST, "invalid_request_error", str(exc))
            return

        session_id = self._resolve_session_id(body)
        request_id = f"resp-{uuid.uuid4().hex}"
        created = int(time.time())

        try:
            if body.get("stream"):
                self._send_streaming_responses_payload(
                    request_id=request_id,
                    created=created,
                    requested_model=body.get("model"),
                    session_id=session_id,
                    messages=messages,
                )
                return

            result = self.server.session_manager.generate(
                model_name=body.get("model"),
                session_id=session_id,
                messages=messages,
            )
        except SessionManagerError as exc:
            self._send_error_json(HTTPStatus.BAD_REQUEST, "invalid_request_error", str(exc))
            return
        except Exception as exc:  # pragma: no cover - defensive boundary
            traceback.print_exc()
            self._send_error_json(
                HTTPStatus.INTERNAL_SERVER_ERROR,
                "server_error",
                f"LiteRT worker failed: {exc}",
            )
            return

        self._send_json(
            HTTPStatus.OK,
            self._build_responses_payload(
                request_id=request_id,
                created=created,
                result=result,
                messages=messages,
            ),
        )

    @staticmethod
    def _sanitize_request_body(body: dict) -> tuple[dict, list[str]]:
        sanitized = dict(body)
        ignored_features: list[str] = []

        for field in (
            "tools",
            "tool_choice",
            "parallel_tool_calls",
            "functions",
            "function_call",
        ):
            value = sanitized.get(field)
            if value not in (None, False, "", [], {}):
                ignored_features.append(field)
            sanitized.pop(field, None)

        return sanitized, ignored_features

    def _normalize_responses_input(self, body: dict) -> list:
        instructions = body.get("instructions")
        raw_input = body.get("input")

        raw_messages: list[dict] = []
        if isinstance(instructions, str) and instructions.strip():
            raw_messages.append({"role": "system", "content": instructions})

        if isinstance(raw_input, str):
            raw_messages.append({"role": "user", "content": raw_input})
        elif isinstance(raw_input, list):
            raw_messages.extend(self._responses_input_list_to_messages(raw_input))
        elif raw_input is None:
            messages = body.get("messages")
            if messages is None:
                raise MessageError("Either input or messages must be provided.")
            raw_messages.extend(messages)
        else:
            raise MessageError("responses.input must be a string or an array.")

        return normalize_messages(
            raw_messages,
            hidden_system_prompt=self.server.config.default_system_prompt,
        )

    def _responses_input_list_to_messages(self, raw_input: list) -> list[dict]:
        raw_messages: list[dict] = []

        for item in raw_input:
            if isinstance(item, str):
                raw_messages.append({"role": "user", "content": item})
                continue

            if not isinstance(item, dict):
                raise MessageError("responses.input contains an unsupported item.")

            role = item.get("role")
            item_type = item.get("type")
            if role in {"system", "user", "assistant", "tool", "developer"}:
                raw_messages.append({"role": role, "content": item.get("content", "")})
                continue

            if item_type == "message":
                raw_messages.append(
                    {
                        "role": item.get("role", "user"),
                        "content": item.get("content", ""),
                    }
                )
                continue

            if item_type in {"input_text", "text"}:
                text = item.get("text")
                if not isinstance(text, str):
                    raise MessageError("Text input item is missing its text field.")
                raw_messages.append({"role": "user", "content": text})
                continue

            raise MessageError("responses.input contains an unsupported item.")

    def _resolve_session_id(self, body: dict) -> str:
        metadata = body.get("metadata")
        metadata_session_id = metadata.get("session_id") if isinstance(metadata, dict) else None
        header_session_id = self.headers.get("X-Session-Id")
        openclaw_session_id = self.headers.get("x-openclaw-session-key")
        request_session_id = body.get("session_id")
        user_session_id = body.get("user")
        resolved = (
            request_session_id
            or metadata_session_id
            or header_session_id
            or openclaw_session_id
            or user_session_id
            or "default"
        )
        return str(resolved)

    def _build_chat_completion_payload(
        self,
        request_id: str,
        created: int,
        result: ChatResult,
        messages: list,
    ) -> dict:
        visible_messages = visible_transcript(messages, self.server.config.default_system_prompt)
        prompt_text = "\n".join(message.content for message in visible_messages)
        completion_tokens = estimate_token_count(result.content)
        prompt_tokens = estimate_token_count(prompt_text)

        return {
            "id": request_id,
            "object": "chat.completion",
            "created": created,
            "model": result.model_id,
            "choices": [
                {
                    "index": 0,
                    "message": {
                        "role": "assistant",
                        "content": result.content,
                    },
                    "finish_reason": "stop",
                }
            ],
            "usage": {
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
                "total_tokens": prompt_tokens + completion_tokens,
            },
            "session_id": result.session_id,
            "rebuilt_session": result.rebuilt,
        }

    def _build_responses_payload(
        self,
        request_id: str,
        created: int,
        result: ChatResult,
        messages: list,
    ) -> dict:
        visible_messages = visible_transcript(messages, self.server.config.default_system_prompt)
        prompt_text = "\n".join(message.content for message in visible_messages)
        output_text = result.content
        input_tokens = estimate_token_count(prompt_text)
        output_tokens = estimate_token_count(output_text)

        return {
            "id": request_id,
            "object": "response",
            "created_at": created,
            "status": "completed",
            "model": result.model_id,
            "output": [
                {
                    "id": f"msg_{uuid.uuid4().hex}",
                    "object": "response.output_message",
                    "type": "message",
                    "status": "completed",
                    "role": "assistant",
                    "content": [
                        {
                            "type": "output_text",
                            "text": output_text,
                            "annotations": [],
                        }
                    ],
                }
            ],
            "output_text": output_text,
            "usage": {
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "total_tokens": input_tokens + output_tokens,
            },
            "metadata": {
                "session_id": result.session_id,
                "rebuilt_session": result.rebuilt,
            },
        }

    def _send_streaming_chat_response(
        self,
        request_id: str,
        created: int,
        requested_model: str | None,
        session_id: str,
        messages: list,
    ) -> None:
        try:
            self.send_response(HTTPStatus.OK)
            self._send_common_headers()
            self.send_header("Content-Type", "text/event-stream; charset=utf-8")
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()

            initial = {
                "id": request_id,
                "object": "chat.completion.chunk",
                "created": created,
                "model": requested_model or self.server.config.default_model,
                "choices": [{"index": 0, "delta": {"role": "assistant"}, "finish_reason": None}],
            }
            self._write_sse(initial)

            result = self.server.session_manager.generate_stream(
                model_name=requested_model,
                session_id=session_id,
                messages=messages,
                on_text=lambda chunk: self._write_sse(
                    {
                        "id": request_id,
                        "object": "chat.completion.chunk",
                        "created": created,
                        "model": requested_model or self.server.config.default_model,
                        "choices": [
                            {"index": 0, "delta": {"content": chunk}, "finish_reason": None}
                        ],
                    }
                ),
            )
            final = {
                "id": request_id,
                "object": "chat.completion.chunk",
                "created": created,
                "model": result.model_id,
                "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
                "session_id": result.session_id,
                "rebuilt_session": result.rebuilt,
            }
            self._write_sse(final)
            self._write_sse_done()
        except ClientDisconnectedError:
            self.server.session_manager.reset_session(requested_model, session_id)
            self.log_message("stream client disconnected; reset session %s", session_id)
            return
        except OSError as exc:
            if self._is_client_disconnect_error(exc):
                self.server.session_manager.reset_session(requested_model, session_id)
                self.log_message("stream client disconnected; reset session %s", session_id)
                return
            raise
        except SessionManagerError as exc:
            self._write_sse_error_if_possible("invalid_request_error", str(exc))
            return
        except Exception as exc:  # pragma: no cover - defensive boundary
            traceback.print_exc()
            self._write_sse_error_if_possible("server_error", f"LiteRT worker failed: {exc}")
            return

    def _send_streaming_responses_payload(
        self,
        request_id: str,
        created: int,
        requested_model: str | None,
        session_id: str,
        messages: list,
    ) -> None:
        try:
            self.send_response(HTTPStatus.OK)
            self._send_common_headers()
            self.send_header("Content-Type", "text/event-stream; charset=utf-8")
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()

            self._write_sse(
                {
                    "type": "response.created",
                    "response": {
                        "id": request_id,
                        "object": "response",
                        "created_at": created,
                        "status": "in_progress",
                        "model": requested_model or self.server.config.default_model,
                        "metadata": {"session_id": session_id},
                    },
                }
            )
            result = self.server.session_manager.generate_stream(
                model_name=requested_model,
                session_id=session_id,
                messages=messages,
                on_text=lambda chunk: self._write_sse(
                    {
                        "type": "response.output_text.delta",
                        "response_id": request_id,
                        "output_index": 0,
                        "content_index": 0,
                        "delta": chunk,
                    }
                ),
            )
            full_payload = self._build_responses_payload(request_id, created, result, messages)
            self._write_sse({"type": "response.completed", "response": full_payload})
            self._write_sse_done()
        except ClientDisconnectedError:
            self.server.session_manager.reset_session(requested_model, session_id)
            self.log_message("stream client disconnected; reset session %s", session_id)
            return
        except OSError as exc:
            if self._is_client_disconnect_error(exc):
                self.server.session_manager.reset_session(requested_model, session_id)
                self.log_message("stream client disconnected; reset session %s", session_id)
                return
            raise
        except SessionManagerError as exc:
            self._write_sse_error_if_possible("invalid_request_error", str(exc))
            return
        except Exception as exc:  # pragma: no cover - defensive boundary
            traceback.print_exc()
            self._write_sse_error_if_possible("server_error", f"LiteRT worker failed: {exc}")
            return

    def _write_sse(self, payload: dict) -> None:
        self._write_sse_bytes(b"data: " + _json_dumps(payload) + b"\n\n")

    def _write_sse_done(self) -> None:
        self._write_sse_bytes(b"data: [DONE]\n\n")

    def _write_sse_error(self, error_type: str, message: str) -> None:
        self._write_sse({"error": {"message": message, "type": error_type}})
        self._write_sse_done()

    def _write_sse_error_if_possible(self, error_type: str, message: str) -> None:
        try:
            self._write_sse_error(error_type, message)
        except ClientDisconnectedError:
            return

    def _write_sse_bytes(self, payload: bytes) -> None:
        try:
            self.wfile.write(payload)
            self.wfile.flush()
        except OSError as exc:
            if self._is_client_disconnect_error(exc):
                raise ClientDisconnectedError("Streaming client disconnected.") from exc
            raise

    @staticmethod
    def _is_client_disconnect_error(exc: OSError) -> bool:
        return isinstance(exc, (BrokenPipeError, ConnectionResetError, ConnectionAbortedError))

    def _check_auth(self) -> bool:
        api_key = self.server.config.api_key
        if not api_key:
            return True

        header = self.headers.get("Authorization", "")
        direct_api_key = self.headers.get("api-key", "")
        x_api_key = self.headers.get("x-api-key", "")

        if (
            header == f"Bearer {api_key}"
            or direct_api_key == api_key
            or x_api_key == api_key
        ):
            return True

        self.send_response(HTTPStatus.UNAUTHORIZED)
        self._send_common_headers()
        self.send_header("WWW-Authenticate", "Bearer")
        self.end_headers()
        self.wfile.write(
            _json_dumps(
                {
                    "error": {
                        "message": "Invalid or missing Bearer token.",
                        "type": "authentication_error",
                    }
                }
            )
        )
        return False

    def _read_json_body(self) -> dict | None:
        length_header = self.headers.get("Content-Length")
        if not length_header:
            self._send_error_json(
                HTTPStatus.BAD_REQUEST, "invalid_request_error", "Missing Content-Length header."
            )
            return None

        try:
            length = int(length_header)
        except ValueError:
            self._send_error_json(
                HTTPStatus.BAD_REQUEST, "invalid_request_error", "Invalid Content-Length header."
            )
            return None

        raw_body = self.rfile.read(length)
        try:
            body = json.loads(raw_body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            self._send_error_json(
                HTTPStatus.BAD_REQUEST, "invalid_request_error", "Request body is not valid JSON."
            )
            return None

        if not isinstance(body, dict):
            self._send_error_json(
                HTTPStatus.BAD_REQUEST, "invalid_request_error", "JSON body must be an object."
            )
            return None
        return body

    def _send_json(self, status: HTTPStatus, payload: dict) -> None:
        body = _json_dumps(payload)
        self.send_response(status)
        self._send_common_headers()
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _send_error_json(self, status: HTTPStatus, error_type: str, message: str) -> None:
        self._send_json(
            status,
            {"error": {"message": message, "type": error_type}},
        )

    def _send_common_headers(self) -> None:
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header(
            "Access-Control-Allow-Headers",
            (
                "Accept, Authorization, Content-Type, X-Session-Id, "
                "api-key, x-api-key, x-openclaw-model, "
                "x-openclaw-session-key, x-openclaw-message-channel"
            ),
        )
        self.send_header("Access-Control-Allow-Methods", "GET, HEAD, POST, OPTIONS")
