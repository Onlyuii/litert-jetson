
---

我先核对一下 LiteRT-LM 官方 C++ API 里和你这个计划最相关的几件事：会话状态、流式输出、tool/use、以及能否通过 system instruction 控制 Gemma 4 thinking。然后我给你一份面向“第一阶段可落地”的计划书。

下面给你一份**基于 LiteRT-LM 官方 C++ API 的第一阶段计划书**。范围只做到**本地 HTTP 服务**，但从一开始就满足你提的 4 个要求：

- **OpenAI-compatible**
- **流式对话**
- **前文记忆**
- **chat / thinking 双模式**

这份计划默认你的底层模型继续用你现在在 Jetson 上已经跑通的 **Gemma 4 E2B + LiteRT-LM GPU**。

---

# 一、项目目标

在 Jetson 上实现一个**常驻本地模型服务**，对外暴露 OpenAI-compatible HTTP 接口，供：

- OpenClaw 作为外层编排器接入
- Python 程序调用
- C++ 程序调用

第一阶段只做**文本对话服务**，不做图片和工具执行，但接口设计要为后续 agent/tool use 预留扩展位。LiteRT-LM 官方把 **Conversation** 作为推荐入口，它是高层、有状态的会话 API，负责维护初始上下文、工具定义、多模态预处理和模板化提示；同时 **SendMessageAsync** 支持通过回调进行流式输出。

---

# 二、为什么选官方 C++ API

这条路线的核心原因有三点。

第一，**LiteRT-LM 官方推荐用 Conversation API 做状态化对话**，而不是自己手工拼 prompt。`Engine` 是重量级对象，持有模型权重；`Conversation` 是轻量级、可多实例的会话对象，适合做服务端的 session 管理。

第二，**流式输出有官方支持**。`SendMessageAsync` 是非阻塞接口，能把模型输出按 token/消息块回调出来，天然适合映射成 HTTP 流式返回。

第三，**thinking 模式是 Gemma 4 官方能力**，不是 hack。Gemma 4 的官方格式说明要求在 **system instruction** 中加入 `<|think|>` 来开启 thinking，而且这是**会话级**设置，不是简单往用户 prompt 里乱塞控制词。

---

# 三、第一阶段范围

第一阶段只交付一个本地 HTTP 服务，功能边界如下。

会做：

- `GET /health`
- `GET /v1/models`
- `POST /v1/chat/completions`
- 非流式与流式两种回复
- session 级前文记忆
- `chat` / `thinking` 两种模式切换
- 单模型优先：`gemma-4-e2b`

暂不做：

- 图片输入
- tools / function calling
- 多模型路由
- 向量检索记忆
- OpenClaw 适配细节优化

不过接口设计会为第二阶段保留 `tools`、`tool_choice`、`metadata` 这些字段。

---

# 四、总体架构

推荐做成两层，但**推理核心必须是 C++**。

Client(OpenClaw / Python / C++)  
        |  
        v  
HTTP Gateway  
        |  
        v  
LiteRT Worker (C++ / Conversation API / GPU / persistent)  
        |  
        v  
Gemma 4 E2B (.litertlm)

更具体一点：

**C++ Worker**

- 持有单个 `Engine`
- 为每个 `session_id` 持有一个 `Conversation`
- 根据模式注入不同 system instruction
- 处理同步与异步生成

**HTTP 层**

- 暴露 OpenAI-compatible endpoint
- 做 JSON 编解码
- 把 stream 映射成 SSE
- 管理 `session_id`
- 做基础鉴权与错误码

这套拆法的好处是：GPU 推理层稳定、轻量、可复用；协议层改起来快。

---

# 五、流式对话设计

LiteRT-LM 官方 C++ API 支持 `SendMessageAsync`，并通过 `MessageCallback` 持续回调消息块。这个能力可以直接映射成 **Server-Sent Events**。

## 设计方案

当请求里：

{ "stream": true }

服务端返回：

Content-Type: text/event-stream  
Cache-Control: no-cache  
Connection: keep-alive

然后逐块输出：

data: {"id":"...","object":"chat.completion.chunk","choices":[{"delta":{"content":"你"}}]}  
  
data: {"id":"...","object":"chat.completion.chunk","choices":[{"delta":{"content":"好"}}]}  
  
data: [DONE]

## 实现要点

- C++ worker 内部用 `SendMessageAsync`
- HTTP 层把 callback 收到的文本块转成 SSE chunk
- 最后统一发 `[DONE]`
- 如果中途出错，SSE 流里返回错误事件并结束连接

## 目标效果

- Python `requests` / `httpx` 能消费
- OpenClaw 后续可直接接流式输出
- 本地 CLI 也能实时显示 token

---

# 六、前文记忆设计

LiteRT-LM 的 `Conversation` 本身就是**有状态会话对象**，适合拿来做 session 级记忆。官方文档明确说它是单个 stateful conversation 的高层 API。

## 第一阶段记忆实现

采用 **会话内记忆 + 可选磁盘持久化** 的两层方案。

### 1）会话内记忆

服务端维护：

session_id -> Conversation

每个请求都带一个 `session_id`。  
如果该 session 已存在，就继续在同一个 `Conversation` 上发送消息；如果不存在，就新建一个。

这样模型会自然保留前文上下文，不需要你每次手工把全部历史重新拼接到 prompt 里。

### 2）磁盘持久化

同时把消息历史写到：

~/litert-agent-data/sessions/<session_id>.jsonl

保存内容包括：

- role
- content
- mode
- timestamp

这样服务重启后可以恢复会话。

## 为什么这样做

- **短期记忆**：依赖 `Conversation` 的状态
- **重启恢复**：依赖消息日志
- **后续升级**：第二阶段再做摘要记忆或检索记忆

---

# 七、chat / thinking 双模式设计

Gemma 4 官方规定：thinking 通过在 **system instruction** 中加入 `<|think|>` 开启，而且这是**会话级设置**。

## 模式设计

每个会话只能有一个 mode：

- `chat`
- `thinking`

在创建会话时决定，不允许中途无感切换。

### chat 模式

system instruction 例如：

You are a helpful local assistant.

### thinking 模式

system instruction 例如：

<|think|>You are a helpful local assistant.

## 重要规则

Gemma 4 官方明确要求：  
在**标准多轮对话**里，上一轮模型输出的 raw thoughts **不能原样带回下一轮历史**；需要把 thoughts 去掉，只保留最终回答。对于长流程 agent，官方建议可把 thoughts **摘要化** 后再作为普通文本放回上下文。

所以第一阶段的策略是：

- thinking 模式下，服务端内部解析模型输出
- 将输出拆成：
    - `thought`
    - `answer`
- **历史里只存 `answer`**
- `thought` 只用于本次返回，不进入标准会话记忆

这样能避免 thinking 模式越聊越乱。

---

# 八、HTTP 接口设计

## 1）健康检查

`GET /health`

返回：

{  
  "status": "ok",  
  "model_loaded": true,  
  "backend": "gpu"  
}

---

## 2）模型列表

`GET /v1/models`

返回：

{  
  "object": "list",  
  "data": [  
    {  
      "id": "litert-gemma-4-e2b",  
      "object": "model",  
      "owned_by": "local"  
    }  
  ]  
}

---

## 3）聊天接口

`POST /v1/chat/completions`

### 请求示例

{  
  "model": "litert-gemma-4-e2b",  
  "messages": [  
    {"role": "system", "content": "你是一个本地助手。"},  
    {"role": "user", "content": "你好，请介绍一下你自己。"}  
  ],  
  "stream": true,  
  "metadata": {  
    "session_id": "demo-001",  
    "mode": "chat"  
  }  
}

### 兼容性说明

OpenAI 标准请求里没有强制要求 `session_id`，所以这里放到 `metadata` 里。  
如果未来要兼容更多客户端，也可以允许：

- `X-Session-Id` header
- `X-Mode` header

双通道都支持。

### 返回示例（非流式）

{  
  "id": "chatcmpl-xxx",  
  "object": "chat.completion",  
  "created": 1710000000,  
  "model": "litert-gemma-4-e2b",  
  "choices": [  
    {  
      "index": 0,  
      "message": {  
        "role": "assistant",  
        "content": "你好，我是运行在本地 Jetson 上的 Gemma 4 助手。"  
      },  
      "finish_reason": "stop"  
    }  
  ]  
}

### 返回示例（thinking 模式扩展）

OpenAI 原版没有统一 thought 字段，所以第一阶段建议放到 `message.metadata`：

{  
  "role": "assistant",  
  "content": "最终回答",  
  "metadata": {  
    "mode": "thinking",  
    "thought": "中间推理内容"  
  }  
}

这样既兼容普通客户端，也保留 thought。

---

# 九、工程实现计划

## 阶段 1A：核心 worker

目标：在 C++ 中完成最小会话服务。

任务：

- 初始化 `Engine`
- 实现 `ConversationManager`
- `session_id -> Conversation`
- 支持 chat/thinking 两种会话创建
- 支持同步生成
- 支持异步流式生成

产物：

- `agent_worker.cpp`
- `conversation_store.h/.cc`
- `mode_manager.h/.cc`

---

## 阶段 1B：HTTP 网关

目标：暴露 OpenAI-compatible 接口。

任务：

- `/health`
- `/v1/models`
- `/v1/chat/completions`
- 非流式 JSON 返回
- 流式 SSE 返回
- API key 可选校验

产物：

- `gateway.cpp` 或 `gateway.py`
- `openai_schema.*`
- `sse_adapter.*`

这里我建议**第一版网关可以用 Python/FastAPI**，因为改协议更快；但 worker 仍然是 C++。

---

## 阶段 1C：会话记忆与持久化

目标：让会话在服务运行期间可记忆，并在重启后可恢复。

任务：

- JSONL 日志写入
- 会话重建
- thinking 模式下剥离 thoughts
- session TTL 与清理机制

产物：

- `session_logger.*`
- `session_recovery.*`

---

## 阶段 1D：联调与验收

目标：验证 Python、curl、OpenClaw 预接入能力。

任务：

- curl 验证非流式
- curl 验证 SSE
- Python SDK/requests 验证
- 多 session 并发验证
- chat/thinking 模式验证

---

# 十、验收标准

第一阶段完成后，应满足下面这些条件。

## 基础功能

- 服务能在 Jetson 上启动
- 模型常驻 GPU
- `/health` 正常
- `/v1/models` 正常
- `/v1/chat/completions` 正常

## 流式

- `stream=false` 时返回完整 JSON
- `stream=true` 时通过 SSE 逐块返回
- 结束时返回 `[DONE]`

## 记忆

- 同一 `session_id` 连续两轮对话能记住前文
- 不同 `session_id` 相互隔离
- 服务重启后可恢复指定会话历史

## 模式

- `chat` 模式只返回普通回答
- `thinking` 模式能通过 system instruction 开启 `<|think|>`
- thinking 模式的 raw thoughts 不进入标准历史

---

# 十一、主要风险与处理

## 1）流式块格式不稳定

处理办法：  
先统一在 worker 层做“文本增量块”输出，HTTP 层只做简单映射，不在网关里做复杂解析。

## 2）thinking 模式污染历史

处理办法：  
严格遵循 Gemma 4 官方建议，**标准多轮不把 raw thoughts 回灌历史**。

## 3）会话过多导致内存上涨

处理办法：  
给 session 加 TTL，例如 30 分钟无活动自动释放 Conversation；消息历史落盘。

## 4）OpenAI-compatible 与 OpenClaw 细节不完全一致

处理办法：  
第一阶段只先兼容核心字段；第二阶段再根据 OpenClaw 的实际请求做细节补齐。

---

# 十二、我对开发复杂度的建议

如果你问这一步“重不重”，我的判断是：

**中等，不算轻，但完全可控。**

真正的难点不在 LiteRT-LM 推理本身，因为官方 C++ API 已经把会话、流式、system prompt、工具预留这些核心能力准备好了。难点主要在：

- 会话管理
- HTTP 协议映射
- thinking 输出整理
- session 持久化

也就是说，这更像一个**中等规模的服务工程**，不是“研究型大工程”。

---

# 十三、下一步最合适的动作

这份计划书落地时，我建议你下一步就做 3 个文件的骨架：

- `agent_worker.cpp`
- `gateway.py`
- `session_store.{h,cc}`

然后优先打通这条链：

**单模型 + 非流式 + 单 session + chat 模式**

之后再加：

- SSE
- session 记忆
- thinking 模式

这样最稳。