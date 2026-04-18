# LiteRT Server

`litert-server` 现在是一套“只依赖 runtime、不依赖 LiteRT-LM 源码”的轻量服务层。

它直接托管你已经整理好的：

- `/home/zeyaoz/litert-lm-runtime/bin/run_litert_lm_advanced_main_gpu`
- `/home/zeyaoz/litert-lm-runtime/models/...`
- `/home/zeyaoz/litert-lm-runtime/lib/...`

不需要重新编译 LiteRT-LM，也不会链接 `/home/zeyaoz/LiteRT-LM` 里的任何源码或静态库。

## 目标

这套服务优先解决你当前最想要的能力：

- 后台常驻模型进程
- HTTP 调用
- OpenAI-compatible `/v1/models` 和 `/v1/chat/completions`
- 文本多轮对话
- 模型切换

## 当前实现方式

服务不是直接链接 LiteRT-LM C++ API，而是包装官方可执行程序：

- `litert_lm_advanced_main`
- `--multi_turns=true`

底层通过 pseudo-terminal 常驻托管 REPL，会话不退出，后续请求继续复用同一个 LiteRT 会话。

这条路线的好处是：

- 落地快
- 不依赖 LiteRT-LM 源码树
- 不需要重编译
- 能直接复用你已经验证过的 Jetson + GPU runtime

## 目录

新的核心代码在这些文件里：

- [run_server.py](/home/zeyaoz/litert-server/run_server.py)
- [litert_server/config.py](/home/zeyaoz/litert-server/litert_server/config.py)
- [litert_server/messages.py](/home/zeyaoz/litert-server/litert_server/messages.py)
- [litert_server/repl.py](/home/zeyaoz/litert-server/litert_server/repl.py)
- [litert_server/sessions.py](/home/zeyaoz/litert-server/litert_server/sessions.py)
- [litert_server/http_api.py](/home/zeyaoz/litert-server/litert_server/http_api.py)
- [litert_server/main.py](/home/zeyaoz/litert-server/litert_server/main.py)
- [tests/mock_litert_cli.py](/home/zeyaoz/litert-server/tests/mock_litert_cli.py)
- [tests/smoke_test.sh](/home/zeyaoz/litert-server/tests/smoke_test.sh)

## 运行要求

- Python 3.10+
- 已经可用的 `/home/zeyaoz/litert-lm-runtime`
- Jetson 上可直接运行的 GPU launcher

默认情况下，服务会直接使用：

- worker: `/home/zeyaoz/litert-lm-runtime/bin/run_litert_lm_advanced_main_gpu`
- E2B: `/home/zeyaoz/litert-lm-runtime/models/gemma-4-E2B-it/model.litertlm`
- E4B: `/home/zeyaoz/litert-lm-runtime/models/gemma-4-E4B-it/model.litertlm`

## 启动

最简单的方式：

```bash
cd /home/zeyaoz/litert-server

export LITERT_SERVER_API_KEY='change-me'
python3 run_server.py
```

默认监听：

- `0.0.0.0:8080`

也可以显式指定：

```bash
python3 run_server.py \
  --bind 0.0.0.0 \
  --port 8080 \
  --api-key change-me \
  --backend gpu
```

## 常用环境变量

- `LITERT_SERVER_API_KEY`
- `LITERT_SERVER_BIND`
- `LITERT_SERVER_PORT`
- `LITERT_SERVER_BACKEND`
- `LITERT_SERVER_WORKER_BINARY`
- `LITERT_SERVER_DEFAULT_MODEL`
- `LITERT_SERVER_SESSION_TTL`
- `LITERT_SERVER_MAX_SESSIONS`
- `LITERT_SERVER_STARTUP_TIMEOUT`
- `LITERT_SERVER_REQUEST_TIMEOUT`
- `LITERT_SERVER_MAX_OUTPUT_TOKENS`
- `LITERT_SERVER_SYSTEM_PROMPT`
- `LITERT_MODEL_E2B`
- `LITERT_MODEL_E4B`

## 模型

当前 `/v1/models` 暴露两个 canonical model id：

- `gemma-4-E2B-it`
- `gemma-4-E4B-it`

同时也兼容几个常见别名：

- `gemma-4-2b-chat`
- `gemma-4-4b-chat`

## OpenAI 兼容接口

### 健康检查

```bash
curl http://127.0.0.1:8080/health
```

### 模型列表

```bash
curl -H 'Authorization: Bearer change-me' \
  http://127.0.0.1:8080/v1/models
```

### 单轮聊天

```bash
curl http://127.0.0.1:8080/v1/chat/completions \
  -H 'Authorization: Bearer change-me' \
  -H 'Content-Type: application/json' \
  -d '{
    "model": "gemma-4-E2B-it",
    "messages": [
      {"role": "user", "content": "你好"}
    ]
  }'
```

### 多轮聊天

同一个会话建议显式传 `session_id`：

```bash
curl http://127.0.0.1:8080/v1/chat/completions \
  -H 'Authorization: Bearer change-me' \
  -H 'Content-Type: application/json' \
  -d '{
    "model": "gemma-4-E2B-it",
    "session_id": "demo-session",
    "messages": [
      {"role": "user", "content": "你好"}
    ]
  }'
```

下一轮：

```bash
curl http://127.0.0.1:8080/v1/chat/completions \
  -H 'Authorization: Bearer change-me' \
  -H 'Content-Type: application/json' \
  -d '{
    "model": "gemma-4-E2B-it",
    "session_id": "demo-session",
    "messages": [
      {"role": "user", "content": "你好"},
      {"role": "assistant", "content": "上一轮回复内容"},
      {"role": "user", "content": "继续说"}
    ]
  }'
```

服务会优先识别：

- 请求体里的 `session_id`
- `metadata.session_id`
- Header `X-Session-Id`
- `user`

如果都没有，会退回到默认会话 `default`。

### SSE 流式

```bash
curl -N http://127.0.0.1:8080/v1/chat/completions \
  -H 'Authorization: Bearer change-me' \
  -H 'Content-Type: application/json' \
  -d '{
    "model": "gemma-4-E2B-it",
    "stream": true,
    "messages": [
      {"role": "user", "content": "请简短介绍自己"}
    ]
  }'
```

注意：当前 SSE 是“协议兼容”的流式输出，但实现上是先拿到完整回答，再按 chunk 回放，不是底层 token 级实时透传。

## 会话行为

这是当前版本最重要的设计点。

`litert_lm_advanced_main --multi_turns=true` 原生只接受“下一条用户输入”，并不原生接受 OpenAI 请求里的完整 `messages` 数组。所以这里做了两层适配：

- 如果请求正好是“已有 transcript + 1 条新 user”，就直接把这条新 user 发给常驻 LiteRT 会话
- 如果请求历史和当前常驻会话对不上，服务会重建该 session，并把完整历史压成一个 bootstrap prompt 再送进 LiteRT

这意味着：

- 常见的本地多轮对话可以工作
- 不需要重编译 LiteRT-LM
- OpenAI 风格 `messages` 可以被基本兼容

但也意味着它仍然是“包装层”，不是 LiteRT-LM 原生 HTTP server。

## 重要限制

当前版本为了尽快落地，明确只保证这些能力：

- 文本输入
- 文本输出
- 多轮会话
- OpenAI-compatible 基本 chat 接口

以下能力暂未支持：

- 图像输入
- 音频输入
- 结构化 tool use / function calling
- 真正的 token 级流式透传
- 完全无损的 system / assistant history 注回底层原生会话

其中 tool calling 之所以没做，不是 HTTP 层偷懒，而是因为当前这条 runtime-only 路线只包装官方 CLI，CLI 本身不会把 tool call 结构化暴露出来。

## Jetson 建议

Jetson Orin Nano 的显存比较紧，所以默认：

- `LITERT_SERVER_MAX_SESSIONS=1`

这是有意为之，避免你一不小心同时拉起多个模型进程把资源打爆。

如果你后面确定 E2B / E4B 的显存占用可控，再逐步往上调。

## 测试

本地 smoke test：

```bash
cd /home/zeyaoz/litert-server
tests/smoke_test.sh
```

这套测试不会调用 LiteRT-LM 源码，也不会依赖 CMake，只验证：

- HTTP server 启动
- `/health`
- `/v1/models`
- 单轮
- 多轮
- SSE
- 模型切换

## 后续建议

如果你下一步要继续做真正可用的 `litert-server`，建议按这个顺序推进：

1. 先把现在这套服务接到你的 OpenAI client 上，确认文本多轮流程稳定。
2. 增加更稳的 session 管理，例如持久化 session 元数据、空闲回收策略、并发限制。
3. 把 buffered SSE 升级成真正的 token 级流式。
4. 如果后面必须做 tool use / image / audio，优先考虑单独写一个“原生 worker”，直接调用 runtime API，而不是继续堆在 CLI 包装层上。

## 结论

当前这个重构版本已经满足你这一阶段的核心诉求：

- 放在 `/home/zeyaoz/litert-server`
- 完全依赖 `/home/zeyaoz/litert-lm-runtime`
- 不依赖 `/home/zeyaoz/LiteRT-LM` 源码
- 可以作为后台常驻模型，通过 HTTP 按 OpenAI 风格被其他工具调用
