#!/usr/bin/env bash
set -euo pipefail

model_path=""
prompt=""

for arg in "$@"; do
  case "$arg" in
    --model_path=*)
      model_path="${arg#*=}"
      ;;
    --input_prompt=*)
      prompt="${arg#*=}"
      ;;
  esac
done

mode="chat"
if [[ "$prompt" == *"<|think|>"* ]]; then
  mode="thinking"
fi

size="2b"
if [[ "$model_path" == *"4B"* || "$model_path" == *"4b"* ]]; then
  size="4b"
fi

user_count="$(printf '%s' "$prompt" | grep -o '<ctrl99>user' | wc -l | tr -d ' ')"
reply="mock-${size}-${mode} users=${user_count}"

if [[ "${MOCK_ECHO_PROMPT:-0}" == "1" ]]; then
  printf 'input_prompt: %s\n' "$prompt"
fi

if [[ "${MOCK_UTF8_REPLY:-0}" == "1" ]]; then
  reply="你好 来自 ${size} ${mode} users=${user_count}"
fi

for token in $reply; do
  printf '%s ' "$token"
  sleep 0.02
done
printf '\n'
