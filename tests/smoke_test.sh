#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TMP_DIR="$(mktemp -d)"
PORT="${LITERT_SERVER_TEST_PORT:-$((20000 + RANDOM % 20000))}"
API_KEY="test-key"
SERVER_PID=""

cleanup() {
  if [[ -n "${SERVER_PID}" ]] && kill -0 "${SERVER_PID}" 2>/dev/null; then
    kill "${SERVER_PID}" 2>/dev/null || true
    wait "${SERVER_PID}" 2>/dev/null || true
  fi
  rm -rf "${TMP_DIR}"
}
trap cleanup EXIT

start_server() {
  local log_file="$1"
  LITERT_SERVER_API_KEY="${API_KEY}" \
  LITERT_SERVER_BIND="127.0.0.1" \
  LITERT_SERVER_PORT="${PORT}" \
  LITERT_SERVER_WORKER_BINARY="${ROOT_DIR}/tests/mock_litert_cli.py" \
  LITERT_MODEL_E2B="${TMP_DIR}/gemma-4-E2B-it.litertlm" \
  LITERT_MODEL_E4B="${TMP_DIR}/gemma-4-E4B-it.litertlm" \
  python3 "${ROOT_DIR}/run_server.py" >"${log_file}" 2>&1 &
  SERVER_PID=$!

  for _ in $(seq 1 50); do
    if curl -s "http://127.0.0.1:${PORT}/health" >/dev/null 2>&1; then
      return 0
    fi
    sleep 0.2
  done

  echo "Server did not become ready. Log:" >&2
  cat "${log_file}" >&2
  return 1
}

assert_json_field() {
  local json="$1"
  local expr="$2"
  python3 - "$json" "$expr" <<'PY'
import json
import sys

payload = json.loads(sys.argv[1])
expr = sys.argv[2]
value = eval(expr, {"payload": payload})
if not value:
    raise SystemExit(1)
PY
}

touch "${TMP_DIR}/gemma-4-E2B-it.litertlm"
touch "${TMP_DIR}/gemma-4-E4B-it.litertlm"

start_server "${TMP_DIR}/server.log"

health="$(curl -s "http://127.0.0.1:${PORT}/health")"
assert_json_field "${health}" "payload['status'] == 'ok'"

root_payload="$(curl -s "http://127.0.0.1:${PORT}/")"
assert_json_field "${root_payload}" "payload['api'] == 'openai-compatible'"

models="$(curl -s -H "Authorization: Bearer ${API_KEY}" \
  "http://127.0.0.1:${PORT}/v1/models")"
assert_json_field "${models}" "len(payload['data']) == 2"
assert_json_field "${models}" "payload['data'][0]['id'] == 'gemma-4-E2B-it'"

models_alias="$(curl -s -H "api-key: ${API_KEY}" \
  "http://127.0.0.1:${PORT}/models")"
assert_json_field "${models_alias}" "len(payload['data']) == 2"

model_detail="$(curl -s -H "Authorization: Bearer ${API_KEY}" \
  "http://127.0.0.1:${PORT}/v1/models/gemma-4-E2B-it")"
assert_json_field "${model_detail}" "payload['id'] == 'gemma-4-E2B-it'"
assert_json_field "${model_detail}" "payload['capabilities']['chat_completions'] is True"

first="$(curl -s "http://127.0.0.1:${PORT}/v1/chat/completions" \
  -H "Authorization: Bearer ${API_KEY}" \
  -H 'Content-Type: application/json' \
  -d '{"model":"gemma-4-E2B-it","messages":[{"role":"user","content":"hello"}]}')"
assert_json_field "${first}" "'[mock e2b gpu] turn=1 users=1 reply_to=hello' == payload['choices'][0]['message']['content']"
assert_json_field "${first}" "payload['session_id'] == 'default'"

second="$(curl -s "http://127.0.0.1:${PORT}/v1/chat/completions" \
  -H "Authorization: Bearer ${API_KEY}" \
  -H 'Content-Type: application/json' \
  -d '{"model":"gemma-4-E2B-it","messages":[{"role":"user","content":"hello"},{"role":"assistant","content":"[mock e2b gpu] turn=1 users=1 reply_to=hello"},{"role":"user","content":"next turn"}]}')"
assert_json_field "${second}" "'turn=2' in payload['choices'][0]['message']['content']"
assert_json_field "${second}" "'users=2' in payload['choices'][0]['message']['content']"
assert_json_field "${second}" "'reply_to=next turn' in payload['choices'][0]['message']['content']"
assert_json_field "${second}" "payload['rebuilt_session'] is False"

chat_alias="$(curl -s "http://127.0.0.1:${PORT}/chat/completions" \
  -H "Authorization: Bearer ${API_KEY}" \
  -H 'Content-Type: application/json' \
  -d '{"model":"gemma-4-E2B-it","messages":[{"role":"user","content":"alias route"}]}')"
assert_json_field "${chat_alias}" "'reply_to=alias route' in payload['choices'][0]['message']['content']"

rebuilt="$(curl -s "http://127.0.0.1:${PORT}/v1/chat/completions" \
  -H "Authorization: Bearer ${API_KEY}" \
  -H 'Content-Type: application/json' \
  -H 'X-Session-Id: rebuild-case' \
  -d '{"model":"gemma-4-E4B-it","messages":[{"role":"system","content":"You are terse."},{"role":"user","content":"say hi"}]}')"
assert_json_field "${rebuilt}" "'[mock e4b gpu] turn=1 users=1 reply_to=say hi' in payload['choices'][0]['message']['content']"
assert_json_field "${rebuilt}" "payload['session_id'] == 'rebuild-case'"

unicode_newline="$(curl -s "http://127.0.0.1:${PORT}/v1/chat/completions" \
  -H "Authorization: Bearer ${API_KEY}" \
  -H 'Content-Type: application/json' \
  --data-binary @- <<'JSON'
{"model":"gemma-4-E2B-it","messages":[{"role":"user","content":"请解释《三体》这本书。\n第二段单独展开。"}]}
JSON
)"
assert_json_field "${unicode_newline}" "'《三体》' in payload['choices'][0]['message']['content']"
assert_json_field "${unicode_newline}" "'第二段单独展开。' in payload['choices'][0]['message']['content']"

stream_response="$(curl -sN "http://127.0.0.1:${PORT}/v1/chat/completions" \
  -H "Authorization: Bearer ${API_KEY}" \
  -H 'Content-Type: application/json' \
  -d '{"model":"gemma-4-E2B-it","stream":true,"messages":[{"role":"user","content":"stream me"}]}')"
python3 - "${stream_response}" <<'PY'
import json
import sys

payload = sys.argv[1]
chunks = []
done = False
for line in payload.splitlines():
    if not line.startswith("data: "):
        continue
    data = line[6:]
    if data == "[DONE]":
        done = True
        continue
    obj = json.loads(data)
    chunks.append(obj["choices"][0]["delta"].get("content", ""))

text = "".join(chunks)
if "[mock e2b gpu]" not in text or "reply_to=stream me" not in text or not done:
    raise SystemExit(1)
PY

python3 - "${PORT}" "${API_KEY}" <<'PY'
import json
import socket
import sys

port = int(sys.argv[1])
api_key = sys.argv[2]
body = json.dumps(
    {
        "model": "gemma-4-E2B-it",
        "stream": True,
        "session_id": "interrupt-case",
        "messages": [{"role": "user", "content": "interrupt me"}],
    },
    ensure_ascii=False,
).encode("utf-8")
request = (
    f"POST /v1/chat/completions HTTP/1.1\r\n"
    f"Host: 127.0.0.1:{port}\r\n"
    f"Authorization: Bearer {api_key}\r\n"
    "Content-Type: application/json\r\n"
    f"Content-Length: {len(body)}\r\n"
    "\r\n"
).encode("utf-8") + body

sock = socket.create_connection(("127.0.0.1", port), timeout=5)
sock.sendall(request)
sock.recv(512)
sock.close()
PY
sleep 0.2

after_interrupt="$(curl -s "http://127.0.0.1:${PORT}/v1/chat/completions" \
  -H "Authorization: Bearer ${API_KEY}" \
  -H 'Content-Type: application/json' \
  -d '{"model":"gemma-4-E2B-it","session_id":"interrupt-case","messages":[{"role":"user","content":"after interrupt"}]}')"
assert_json_field "${after_interrupt}" "'turn=1' in payload['choices'][0]['message']['content']"
assert_json_field "${after_interrupt}" "'users=1' in payload['choices'][0]['message']['content']"
assert_json_field "${after_interrupt}" "'reply_to=after interrupt' in payload['choices'][0]['message']['content']"

tools_ignored="$(curl -s "http://127.0.0.1:${PORT}/v1/chat/completions" \
  -H "Authorization: Bearer ${API_KEY}" \
  -H 'Content-Type: application/json' \
  -d '{"model":"gemma-4-E2B-it","tool_choice":"auto","tools":[{"type":"function","function":{"name":"noop","description":"ignored","parameters":{"type":"object","properties":{}}}}],"messages":[{"role":"user","content":"hello with tools"}]}')"
assert_json_field "${tools_ignored}" "'reply_to=hello with tools' in payload['choices'][0]['message']['content']"

responses="$(curl -s "http://127.0.0.1:${PORT}/v1/responses" \
  -H "Authorization: Bearer ${API_KEY}" \
  -H 'Content-Type: application/json' \
  -d '{"model":"gemma-4-E2B-it","input":"hello responses"}')"
assert_json_field "${responses}" "payload['object'] == 'response'"
assert_json_field "${responses}" "payload['output_text'].endswith('reply_to=hello responses')"

responses_stream="$(curl -sN "http://127.0.0.1:${PORT}/v1/responses" \
  -H "Authorization: Bearer ${API_KEY}" \
  -H 'Content-Type: application/json' \
  -d '{"model":"gemma-4-E2B-it","stream":true,"input":"stream responses"}')"
[[ "${responses_stream}" == *"response.output_text.delta"* ]]
[[ "${responses_stream}" == *"data: [DONE]"* ]]

echo "Smoke test passed."
