#!/usr/bin/env bash
set -euo pipefail

RUNTIME_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

export LITERT_RUNTIME_DIR="$RUNTIME_DIR"
export LITERT_BIN_DIR="$RUNTIME_DIR/bin"
export LITERT_LIB_DIR="$RUNTIME_DIR/lib"
export LITERT_TOOLS_DIR="$RUNTIME_DIR/tools"
export LITERT_MODELS_DIR="$RUNTIME_DIR/models"
export LITERT_VAR_DIR="$RUNTIME_DIR/var"
export LITERT_DEFAULT_MODEL="$LITERT_MODELS_DIR/gemma-4-E2B-it/model.litertlm"

export LD_LIBRARY_PATH="$LITERT_LIB_DIR${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
