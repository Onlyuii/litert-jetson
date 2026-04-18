# LiteRT-LM Function Call 调研与长期规划

日期：2026-04-18

本文档基于以下三部分材料整理：

- 官方文档与上游源码：`/home/zeyaoz/Desktop/Projects/LiteRT-LM`
- 当前运行时产物：`/home/zeyaoz/litert-lm-runtime`
- 当前 HTTP 服务封装：`/home/zeyaoz/litert-server`

本文档的目标，是把“LiteRT-LM 的 function call 机制是什么、当前本地 runtime/server 能做到什么、后续应该怎么规划”整理成一份可持续参考的设计说明，供后续长期开发使用。

## 一、原始问题整理

本次需要回答并沉淀的问题如下：

1. 原版 `LiteRT-LM` 是如何做到 function call 的？
2. 它的使用方式是什么？
3. function call 中的 `function` 指的是什么？
4. 这个 `function` 是一个函数、一个程序、一个 server，还是还有更多形态？
5. 基于当前的 `litert-lm-runtime` 和 `litert-server`，能否将它做成 Python 或 C++ API，并调用该语言的函数？
6. 未来如果希望使用当前的 `runtime/server` 来调用 Python 函数，或者某个程序，应该怎么做？

## 二、结论摘要

一句话总结：

`LiteRT-LM` 原版的 function call，不是“`litert_lm_advanced_main` 这个 CLI 自动替你执行外部程序”，而是通过 `Conversation API + tool schema + 应用侧执行器` 完成的。

更准确地说：

- LiteRT-LM 负责：
  - 让模型知道有哪些工具可用
  - 把消息组织成模型期望的 prompt 格式
  - 识别并解析模型输出中的工具调用
  - 维护 user/model/tool 三类消息的对话历史
- 应用程序负责：
  - 定义工具
  - 执行工具
  - 把工具执行结果返回给模型

所以，function call 中的 `function`，从框架语义上应理解为“一个可被模型调用的工具接口”，它不局限于某种编程语言里的函数符号。

它在工程上可以映射成：

- 一个 Python 函数
- 一个 C++ 函数
- 一个 shell 命令或可执行程序
- 一个 HTTP / RPC 服务
- 一个数据库查询
- 一个工作流节点
- 任何由应用层实现和调度的能力

## 三、原版 LiteRT-LM 的 function call 机制

### 3.1 核心 API：Conversation API

官方文档明确说明，tool calling 是在 `Conversation API` 中完成的，而不是一个简单的“单轮 CLI 推理接口”。

官方描述的基本模型是：

1. 创建一个重量级的 `Engine`
2. 基于 `Engine` 创建一个或多个轻量级 `Conversation`
3. 用 `Conversation` 持续发送消息
4. 在会话上下文中维护工具定义、提示模板、多轮消息和工具返回

这意味着：

- `Engine` 持有模型权重，适合常驻
- `Conversation` 持有会话状态，适合多轮对话
- function call 是多轮会话机制的一部分，而不是附加在单轮 CLI 之上的独立功能

### 3.2 原版工具调用流程

根据官方 C++ 文档，tool use 的标准流程如下：

1. 应用声明一组可用工具
2. 用户发送消息
3. 模型输出一个表示工具调用的字符串
4. LiteRT-LM 检测并解析这段输出，生成结构化 `tool_calls`
5. 应用读取 `tool_calls`，执行真实工具
6. 应用把工具结果作为 `role="tool"` 的消息发回模型
7. 模型再生成自然语言回答，或者继续发起下一次工具调用

换句话说，LiteRT-LM 负责“理解和组织工具调用协议”，但不负责“真的替你跑工具”。

### 3.3 tool declaration 是什么

原版 LiteRT-LM 并不是直接把某个函数指针交给模型，而是先声明“工具的可调用描述”。

官方 C++ 文档中，工具声明本质上是 JSON schema，通常包含：

- `name`
- `description`
- `parameters`
- 参数类型
- 参数是否必填

这和 OpenAI 风格的工具声明很接近。

所以模型首先看到的是“可调用接口描述”，而不是语言级函数本体。

### 3.4 模型输出的是什么

模型不会直接调用宿主机函数。它输出的是一种工具调用表达，LiteRT-LM 再把它解析成统一结构。

在官方文档示例里，解析后的结构大致是：

```json
{
  "tool_calls": [
    {
      "type": "function",
      "function": {
        "name": "get_weather",
        "arguments": {
          "location": "Paris"
        }
      }
    }
  ]
}
```

这说明 `function` 在协议层是：

- 一个工具名字
- 一组结构化参数

而不是一个“内存地址上的可执行函数对象”。

### 3.5 真实执行发生在哪一层

真实执行发生在应用侧，而不是 LiteRT-LM 内部。

这也是官方文档直接强调的重点：调用这个函数是应用程序自己的责任。

因此：

- 如果你想让模型调用 Python 函数，就由你的 Python 程序负责把 `tool_calls` 映射到对应函数并执行
- 如果你想让模型调用某个可执行程序，就由你的应用决定如何调用该程序
- 如果你想让模型调用一个远端服务，也由你的应用在收到 `tool_calls` 后发起 HTTP/RPC 请求

### 3.6 function 的形态不只一种

从上游设计看，`function` 不应该狭义理解成“某语言里的函数声明”。它更像一个抽象工具接口。

因此，它完全可以对应以下多种形态：

- 语言内函数
- 脚本入口
- 可执行文件
- 远程 API
- 服务网关
- 数据库读写动作
- 文件系统操作
- 更高层的业务动作

所以问题“它是一个函数、一个程序，还是一个 server”更准确的答案是：

它可以是以上任意一种，只要你的应用愿意把该能力封装成一个具名工具，并能接收结构化参数。

### 3.7 tool call 的语法形态不只一种

从上游源码可以看出，LiteRT-LM 对工具调用的解析并不只支持一种固定语法。当前解析器至少支持：

- `python`
- `json`
- `fc`

这些输出形式会被统一解析成结构化 `tool_calls`。这说明“function call”在底层并不是单一文本协议，而是“模型输出语法 + LiteRT-LM 解析器 + 统一消息结构”的组合。

### 3.8 为什么 Gemma4 也能支持 tool use

从 `llm_model_type.proto` 可以看到，Gemma4 的模型元数据里就包含：

- `code_fence_start`
- `code_fence_end`
- `syntax_type`
- `tool_code_regex`
- `function_response_start`
- `constraint_mode`

这意味着 LiteRT-LM 在做 tool use 时，不只是靠应用层“猜格式”，而是会参考模型自己的元数据去格式化提示词、解析工具调用、约束输出形式。

## 四、当前三个项目分别处于什么状态

### 4.1 上游源码项目：LiteRT-LM

路径：

`/home/zeyaoz/Desktop/Projects/LiteRT-LM`

这个项目拥有完整的上游能力，包括：

- `Conversation API`
- tool declaration
- tool call 解析
- 多轮对话
- multimodal 输入格式
- Python / Kotlin / C++ 等多层 API

对 function call 而言，真正的“原生能力实现”在这里。

### 4.2 当前运行时项目：litert-lm-runtime

路径：

`/home/zeyaoz/litert-lm-runtime`

这个目录是一个已经整理过的运行时 bundle，包含：

- `bin/`
- `lib/`
- `tools/`
- `examples/jvm/`
- `models/`
- `scripts/`

它适合：

- 直接运行 CLI
- 运行 JVM 示例
- 作为部署产物存在

但它目前不是一个标准的“完整 C++ SDK 包”，因为它并没有被整理成“头文件 + 库 + 官方安装布局”的开发包形式。

因此：

- 运行可执行文件没问题
- 作为底层运行时资产没问题
- 直接把它当作完整 C++ 开发 SDK 使用，不是最自然的路径

### 4.3 当前服务项目：litert-server

路径：

`/home/zeyaoz/litert-server`

这个项目当前做的事情是：

- 暴露 OpenAI 风格 HTTP 接口
- 维护 session
- 通过 Python 驱动一个后台 CLI worker
- 与 `litert_lm_advanced_main` 进行文本级交互

当前实现的关键特点是：

- 它不是直接调用 LiteRT-LM 原生 `Conversation` 对象
- 它不是直接消费结构化 `tool_calls`
- 它目前会主动忽略 OpenAI 请求体里的 `tools/functions` 相关字段

因此，当前的 `litert-server` 虽然已经能提供聊天接口，但它并没有把上游原生 function call 能力接出来。

## 五、当前 litert-server 为什么还不具备原生 function call

当前 `litert-server` 的问题不在“模型不支持”，而在“服务实现没有走到原生工具调用链路”。

核心原因有两个：

### 5.1 请求层主动忽略了 tool/function 字段

当前 `http_api.py` 会清洗掉以下字段：

- `tools`
- `tool_choice`
- `parallel_tool_calls`
- `functions`
- `function_call`

这意味着就算前端按 OpenAI 规范把工具定义发过来，服务端也不会继续传递和处理。

### 5.2 worker 层仍然是纯文本 prompt in / text out

当前 `repl.py` 和 `sessions.py` 这一层本质上做的是：

- 把消息拼成一段文本 prompt
- 发送给 CLI worker
- 读取 CLI 文本输出
- 再封装回 HTTP 响应

所以它现在并没有保留：

- 原生 `Message`
- 原生 `tool_calls`
- 原生 `role="tool"` 回注
- 原生 `Conversation` 对象状态机

因此，当前服务虽然“能聊天”，但还不能说“已经接通了上游原版的 function call”。

## 六、基于当前 runtime/server，能否做成 Python 或 C++ API

### 6.1 Python API：可以，而且最现实

如果目标是“在现在这套基础上快速演进”，那么 Python API 是最容易继续做的。

原因很简单：

- `litert-server` 本身就是 Python 项目
- 当前 session、streaming、HTTP 已经在 Python 层
- 如果要增加工具注册、工具分发、工具执行、工具结果回注，这些都很适合在 Python 层实现

因此，基于当前 `runtime/server`，做成一个更完整的 Python API 是完全可行的。

### 6.2 当前状态下的 Python function calling：可以做，但不是“已经具备”

需要明确区分两件事：

- “能不能做”
- “现在是不是已经有”

当前答案是：

- 能做
- 但现在还没有

要实现“模型调用 Python 函数”，需要在 `litert-server` 里自己补出以下机制：

1. 接收工具定义
2. 维护工具注册表
3. 识别模型请求调用哪个工具
4. 将参数映射到 Python 函数
5. 执行函数
6. 把结果重新喂回模型

### 6.3 C++ API：分两种情况

如果你说的 C++ API 是“让 C++ 程序访问这个模型服务”，那么当前最简单的方式就是：

- 直接调用 `litert-server` 暴露的 HTTP API

如果你说的 C++ API 是“在进程内原生调用 LiteRT-LM，并直接做 tool use”，那么更推荐：

- 回到上游 `LiteRT-LM` 的 C++ `Conversation API`

原因是当前的 `litert-lm-runtime` 更像部署产物，不是为原生二次开发精心整理的 SDK。

因此结论是：

- “面向 C++ 客户端调用服务”可以
- “继续只依赖当前 runtime/server 做成优雅的原生 C++ tool SDK”不太理想
- 如果要做真正原生、长期维护的 C++ tool use，应该直接基于上游 C++ API

## 七、未来如果希望用当前 runtime/server 调 Python 函数或某个程序，应该怎么做

这一部分建议分为两条路线。

### 7.1 路线 A：最省时间，继续基于当前 server 扩展

这是最适合当前阶段的方案。

目标是：

- 继续保留 `litert-lm-runtime` 里的 worker 和库
- 继续保留 `litert-server` 的 HTTP 服务外壳
- 在 Python 层补齐工具调用能力

建议实现步骤如下：

1. 不再忽略 `tools/functions` 请求字段
2. 设计工具注册表
3. 把工具声明转换成模型可理解的格式并写入上下文
4. 识别模型输出中的工具调用
5. 调度本地 Python 函数 / 外部程序 / HTTP 服务
6. 把工具结果包装成 `role="tool"` 再送回模型
7. 将最终回答返回给前端

可以把工具执行后端抽象成三类：

- Python callable
- subprocess / executable
- HTTP / RPC adapter

这样一个统一的 tool registry 就能同时支持：

- 调 Python 函数
- 调某个本地程序
- 调某个远程服务

#### 这种路线的优点

- 开发快
- 对现有部署方式改动小
- 非常适合先把 Python tool use 做出来

#### 这种路线的缺点

- 当前底层仍然是 CLI 包装
- 你会在 Python 层重做一部分“上游原本已有的工具调用逻辑”
- 长期维护性和鲁棒性弱于原生 API

### 7.2 路线 B：长期更干净，换成原生 Conversation 后端

如果目标是长期稳定支持以下能力：

- 常驻模型
- 多会话
- 工具调用
- Python 函数
- 外部程序
- 更标准的 OpenAI tool 协议兼容
- 更稳定的流式输出

那么更推荐的长期路线是：

- 保留你现在的 HTTP server 思路
- 但把底层 worker 从 `litert_lm_advanced_main` CLI 替换为原生 API

具体可选两条：

- Python 路线：改用官方 Python API
- C++ 路线：改用官方 C++ `Conversation API`

这样做的好处是：

- 不需要自行猜测 prompt 模板细节
- 不需要自己还原 tool call 文本协议
- 不需要自己维护太多 CLI 交互边界情况
- 更接近上游设计

### 7.3 如果未来重点是调用 Python 函数，推荐优先级

如果未来最重要的是“调用 Python 函数”，建议优先级如下：

1. 短期：先在 `litert-server` 里做 Python tool registry
2. 中期：把当前 CLI worker 替换成原生 Python Conversation API
3. 长期：视性能和维护要求，决定是否将后端下沉到原生 C++ 常驻服务

### 7.4 如果未来重点是调用某个程序

如果未来重点是“调用某个程序”，建议不要让模型直接拼 shell 命令，而应设计成受控工具：

- 工具名固定
- 参数 schema 固定
- 调用命令模板固定
- 参数严格校验
- 限制超时
- 捕获 stdout/stderr
- 做权限和白名单控制

这样能显著降低错误率和安全风险。

## 八、对当前架构的判断

当前这套本地结构：

- `LiteRT-LM`：拥有原生工具调用能力
- `litert-lm-runtime`：拥有运行所需二进制和库，也包含 JVM tool 示例产物
- `litert-server`：当前只是“把 CLI 包成 HTTP”，并没有原生 function call

因此，当前问题的关键不是“模型不能 function call”，而是“server 尚未把 function call 这条原生能力链路接出来”。

## 九、推荐的长期规划

### 9.1 短期目标

目标：

- 保持现有部署不大改
- 在 `litert-server` 中增加工具注册和调度
- 先支持 Python 函数调用
- 兼容 OpenAI 风格 `tools`

推荐动作：

1. 在 `litert-server` 中停止清洗 `tools/functions`
2. 增加 `tool registry`
3. 增加 `tool dispatcher`
4. 增加 `tool result -> role=tool` 回注机制
5. 先实现 Python callable 类型工具
6. 再实现 subprocess 类型工具

### 9.2 中期目标

目标：

- 让服务真正常驻
- 避免频繁依赖 CLI 文本边界
- 提高流式输出和中断恢复稳定性

推荐动作：

- 评估从当前 CLI backend 迁移到原生 Python API
- 让 session 和 model 都常驻内存
- 将 tool use 放回原生会话层处理

### 9.3 长期目标

目标：

- 构建一个稳定的 `litert-server`
- 对外提供 OpenAI 风格兼容接口
- 对内使用 LiteRT-LM 原生会话和工具调用能力
- 支持 Python 工具、程序工具、HTTP 工具

推荐动作：

- 形成独立的工具框架
- 给每个工具定义 schema、超时、错误处理、权限边界
- 增加模型路由、会话管理、日志与监控

## 十、当前最值得记住的判断

最关键的判断只有三条：

1. 原版 LiteRT-LM 已经支持 function call，但它是 `Conversation API` 的能力，不是 CLI 自带“真实执行外部函数”的能力。
2. 当前 `litert-server` 还没有接通这条原生能力链，因为它现在会忽略 `tools/functions`，并且只做文本级 CLI 包装。
3. 如果未来重点是“调用 Python 函数”，最现实的近期方案是在 `litert-server` 中自行增加 tool registry 和调度层；如果未来重点是“长期稳定和原生能力”，则应逐步迁移到底层原生 API。

## 十一、参考资料

### 11.1 官方文档

- README  
  `https://github.com/google-ai-edge/LiteRT-LM/blob/main/README.md`
- C++ Tool Use  
  `https://github.com/google-ai-edge/LiteRT-LM/blob/main/docs/api/cpp/tool-use.md`
- C++ Conversation API  
  `https://github.com/google-ai-edge/LiteRT-LM/blob/main/docs/api/cpp/conversation.md`
- Python Getting Started  
  `https://github.com/google-ai-edge/LiteRT-LM/blob/main/docs/api/python/getting_started.md`
- Kotlin Getting Started  
  `https://github.com/google-ai-edge/LiteRT-LM/blob/main/docs/api/kotlin/getting_started.md`

### 11.2 上游源码位置

- prompt 中注入工具声明  
  `/home/zeyaoz/Desktop/Projects/LiteRT-LM/runtime/conversation/prompt_utils.cc`
- Gemma4 解析工具调用  
  `/home/zeyaoz/Desktop/Projects/LiteRT-LM/runtime/conversation/model_data_processor/gemma4_data_processor.cc`
- tool call 解析器  
  `/home/zeyaoz/Desktop/Projects/LiteRT-LM/runtime/components/tool_use/parser_utils.cc`
- 模型 tool 元数据  
  `/home/zeyaoz/Desktop/Projects/LiteRT-LM/runtime/proto/llm_model_type.proto`
- Kotlin 工具示例  
  `/home/zeyaoz/Desktop/Projects/LiteRT-LM/kotlin/java/com/google/ai/edge/litertlm/example/ToolMain.kt`

### 11.3 当前本地项目位置

- runtime 说明  
  `/home/zeyaoz/litert-lm-runtime/README-runtime.md`
- 当前 server 对 tool 字段的清洗  
  `/home/zeyaoz/litert-server/litert_server/http_api.py`
- 当前 server 的 CLI 交互层  
  `/home/zeyaoz/litert-server/litert_server/repl.py`
- 当前 server 的 session 层  
  `/home/zeyaoz/litert-server/litert_server/sessions.py`

## 十二、建议的下一步

如果后续继续推进，建议下一个文档直接进入设计阶段，主题可以是：

`litert-server tool use 设计草案`

建议包含以下内容：

- OpenAI `tools` 请求体如何接收
- 本地 Python 函数如何注册成工具
- subprocess 工具如何做参数校验和安全控制
- 工具结果如何回写到多轮会话
- 流式输出如何兼容 Cherry Studio / OpenClaw
- 未来如何从 CLI backend 迁移到原生 API backend
