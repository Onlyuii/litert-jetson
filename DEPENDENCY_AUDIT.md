# LiteRT HTTP Service Dependency Audit

## 1. 当前代码结构

项目现在分成两层：

### A. `litert_http_service`

这是对外 HTTP 服务层，职责是：

- 读取配置
- 提供 `/health`、`/v1/models`、`/v1/chat/completions`
- Bearer API key 鉴权
- SSE / 纯文本流输出
- `session_id` 解析
- SQLite 会话持久化
- 调用下层推理 backend

对应文件：

- `src/main.cpp`
- `src/config.cpp`
- `src/http_server.cpp`
- `src/engine_manager.cpp`
- `src/conversation_manager.cpp`
- `src/session_store.cpp`
- `src/inference_backend.cpp`
- `src/utils.cpp`
- `include/litert_http/*.h`

### B. `litert_http_worker`

这是常驻推理层，职责是：

- 直接包含 LiteRT-LM 官方 `c/engine.h`
- 常驻创建 `LiteRtLmEngine`
- 为每个 `session_id` 持有 `LiteRtLmConversation`
- 通过 stdin/stdout 接收命令与返回流式结果

对应文件：

- `worker/litert_http_worker.cpp`

### C. Worker 专用构建补丁层

这是为了让 LiteRT-LM 的上游 CMake 能嵌入到当前项目里而加的补丁：

- `worker/CMakeLists.txt`
- `worker/cmake/packages/litert/litert_patcher.cmake`
- `worker/cmake/scripts/find_and_copy_cxxbridge.cmake`
- `worker/cmake/scripts/sync_generated_bridge_headers.cmake`
- `worker/cmake/scripts/patch_generated_compat.cmake`


## 2. 我们自己真正写的核心代码

### 必需代码

- `src/http_server.cpp`
  - 当前最大文件，负责 HTTP 解析、鉴权、SSE、请求校验、OpenAI 兼容输出。
- `src/inference_backend.cpp`
  - 负责启动/复用 worker 子进程，并把请求转成 worker 协议。
- `worker/litert_http_worker.cpp`
  - 负责直接调用 LiteRT-LM 官方 C API。
- `src/conversation_manager.cpp`
  - 负责 `session_id -> SessionData` 的会话规则和恢复逻辑。
- `src/session_store.cpp`
  - 负责 SQLite 持久化。
- `src/engine_manager.cpp`
  - 负责 2B/4B 逻辑模型、单活推理、健康状态。
- `src/config.cpp`
  - 负责环境变量和命令行配置。

### 当前可判定为遗留/未使用代码

- `src/prompt_renderer.cpp`
- `include/litert_http/prompt_renderer.h`

说明：

- 这套文件属于早期“拼 prompt 调 CLI”的方案遗留。
- 当前正式路径已经改成 worker + LiteRT-LM `Conversation` C API。
- 现在代码里没有任何地方再调用 `PromptRenderer::RenderGemmaPrompt()`。

### 当前可判定为遗留/未使用字段

- `ServiceConfig::litert_binary_path`

说明：

- 这个字段来自最早直接调用 `litert_lm_main` 的方案。
- 当前 `src/inference_backend.cpp` 已只使用 `worker_binary_path`。
- 所以这个配置字段和对应 `--binary` 参数现在属于遗留项。


## 3. 顶层服务的直接依赖

`litert_http_service` 当前的直接依赖非常少。

### 必需

- `SQLite3`
  - 用于 `SessionStore`
- `Threads`
  - 用于 HTTP 连接线程和内部锁
- `jsoncpp`
  - 用于 HTTP 请求/响应 JSON 解析与序列化

### 问题

- `jsoncpp` 只在 server 层用
- worker 层使用的是 `nlohmann/json`
- 也就是说当前项目内部同时存在两套 JSON 库

这不是编译阻塞点，但属于明显的整理目标。


## 4. Worker 的真实直接依赖

从 `worker/litert_http_worker.cpp` 本身看，真正直接使用的只有这些：

### 必需

- LiteRT-LM 官方 C API：`c/engine.h`
  - `litert_lm_engine_settings_create`
  - `litert_lm_engine_settings_set_cache_dir`
  - `litert_lm_engine_create`
  - `litert_lm_conversation_config_create`
  - `litert_lm_conversation_create`
  - `litert_lm_conversation_send_message_stream`
  - 相关 delete/free 接口
- `nlohmann/json`
  - worker stdin/stdout 协议
- C++ 标准库
  - `mutex` / `condition_variable` / `unordered_map` / `string`

### 当前没有直接使用的能力

从 worker 源码本身可见，当前没有显式使用：

- tools / function calling
- constrained decoding 配置
- image input
- audio input
- vision backend
- audio backend
- session sampler 参数自定义
- benchmark 输出
- LoRA 注入

也就是说，worker 代码目标其实是“纯文本、多轮、流式会话”。


## 5. Worker 实际被链接进来的库

根据当前 `worker/build/CMakeFiles/litert_http_worker.dir/link.txt`，worker 最终被链接进来的库大致分为：

### A. LiteRT-LM 本地 staging 库

数量约 `102` 个。

典型前缀包括：

- `runtime_conversation_*`
- `runtime_core_*`
- `runtime_engine_*`
- `runtime_executor_*`
- `runtime_util_*`
- `runtime_components_*`
- `schema_*`

### B. LiteRT 外部库

数量约 `32` 个。

包括：

- `liblitert_cc_api.a`
- `liblitert_runtime.a`
- `liblitert_core.a`
- `liblitert_cc_options.a`
- 以及一批 `tools/*`、`vendors/qualcomm/*`

### C. TFLite / TensorFlow 侧库

数量约 `52` 个。

包括：

- `libtensorflow-lite.a`
- `libxnnpack-delegate.a`
- `ruy_*`
- `XNNPACK`
- `cpuinfo`
- `pthreadpool`
- `farmhash`
- `fft2d`

### D. Abseil

数量约 `91` 个静态库。

### E. 其他第三方

- `protobuf`
- `sentencepiece`
- `re2`
- `tokenizers-cpp`
- `antlr4-runtime`
- `miniaudio`
- `minizip`
- `libpng`
- `zlib`
- Rust `litert_lm_deps`
- `libcxxbridge1.a`


## 6. 可以判定为“业务必需”的依赖

这里的“必需”是指：为了让当前目标成立，也就是“Gemma 4 文本多轮对话 + 流式 + C API Conversation”，大概率绕不开。

### 明确必需

- LiteRT-LM `c/engine.h` 对应实现
- LiteRT runtime / core / cc api
- TFLite / LiteRT backend 运行时
- Abseil
- Protobuf
- Rust bridge / `litert_lm_deps` / `cxxbridge`
- 至少一种 tokenizer 实现
- 基础压缩与资源库
  - `zlib`
  - `minizip`
- `sentencepiece` 或 HuggingFace tokenizer 中至少一种

### 服务层必需

- `SQLite3`
- `Threads`
- 一套 JSON 库


## 7. 很可能不是当前目标必需的依赖

这里我用“很可能”而不是“确定”，因为还没有对 LiteRT-LM 内部调用图做裁剪式验证。但从当前业务范围和库名字看，下面这些大概率不是“纯文本 Gemma 4 聊天服务”的硬需求。

### A. LiteRT 外部工具链库

当前 link.txt 里仍然出现：

- `tools/liblitert_tool_display.a`
- `tools/liblitert_dump.a`
- `tools/liblitert_npu_numerics_check.a`
- `tools/liblitert_tensor_utils.a`
- `tools/liblitert_apply_plugin.a`
- `tools/flags/*`

判断：

- 这些更像 CLI / 调试 / 插件工具支持
- 对 `litert_http_worker` 来说大概率不需要
- 它们现在被带进来，说明上游 aggregate 仍然过宽

### B. Qualcomm vendor 相关库

当前 link.txt 里仍然出现：

- `vendors/qualcomm/libqnn_*`
- `vendors/qualcomm/core/*`

判断：

- 你的目标平台是 Jetson Orin Nano
- 当前 backend 是 LiteRT-LM + Jetson GPU
- Qualcomm QNN 路线显然不是当前业务必需

这类依赖是当前最明确的“应裁掉对象”之一。

### C. 音频相关库

当前 staging 库里有：

- `runtime_components_preprocessor_audio_preprocessor_miniaudio`
- `runtime_executor_audio_*`
- `miniaudio`

判断：

- 当前 worker 没有音频输入能力
- HTTP 协议也没有音频消息格式
- 所以这部分对第一阶段文本服务大概率不需要

### D. 视觉相关库

当前 staging 库里有：

- `runtime_executor_vision_*`
- `stb_image_preprocessor`
- `libpng`

判断：

- 当前接口只支持文本消息
- worker 也没有构造 image message
- 所以这部分不是当前第一阶段目标必需

### E. Tool use / constrained decoding 相关库

当前 staging 库里有：

- `runtime_components_tool_use_*`
- `antlr_*_tool_call_parser`
- `runtime_components_constrained_decoding_*`
- `llguidance_schema_utils`
- Rust `minijinja_template`

判断：

- 当前 worker 调 `conversation_config_create(... tools_json=nullptr, enable_constrained_decoding=false)`
- 也就是说，我们并没有真的打开 tool use 或 constrained decoding
- 这类库很可能是上游 `Conversation` 默认路径拖带进来的

这是第二个非常值得重点核查的裁剪方向。

### F. 多模型/多家族数据处理器

当前 staging 库里有：

- `gemma3_data_processor`
- `qwen3_data_processor`
- `function_gemma_data_processor`
- `generic_data_processor`

判断：

- 我们现在只跑 Gemma 4
- `qwen3_*` 这类名字看起来不是当前业务硬需求


## 8. 当前最可疑、最容易出问题的依赖区

这些不是“功能上不一定需要”，而是“从构建角度看，已经反复出问题，值得重点收缩”。

### 1. LiteRT-LM 的 aggregate / shim 体系

问题表现：

- `LITERTLM_DEPS` 把很多库通过 `shim` 和字符串化的库路径拼起来
- 容易出现链接顺序错乱
- 容易把库列表当成单个字符串传给链接器
- 容易漏掉真实静态库

这就是前面 `absl`、`JoinPath`、`LiteRt*` 未定义符号的主要来源之一。

### 2. LiteRT 外部构建被带入太多工具/厂商库

问题表现：

- 虽然我们在 patch 里禁了 `vendors` 和 `tools`
- 但最终链接里仍然出现大量 `tools/*` 和 `vendors/qualcomm/*`

说明：

- 不是 patch 没写，而是上游 target map / aggregate 仍在引用这些归档库
- 这类“逻辑上裁了、链接上没裁干净”的状态最危险

### 3. Rust / cxxbridge 生成链

问题表现：

- `cargo` 版本
- `cxxbridge-cmd`
- `libcxxbridge1.a` 复制路径
- `.h` / `.rs.h` 生成位置

这块已经通过补丁修过多次，说明上游生成链对“嵌入到子工程构建目录”并不稳定。

### 4. 生成代码与头文件目录不一致

问题表现：

- `llm_metadata.pb.h` 找不到
- `minijinja_template.rs.h` 找不到

说明：

- 上游默认假设的生成目录布局，和我们 `worker/build/litert_lm` 这种嵌套二进制目录不完全兼容


## 9. 当前明确存在的项目级整理问题

### A. 重复 JSON 库

- server 用 `jsoncpp`
- worker 用 `nlohmann/json`

建议：

- 后续统一成一套
- 如果倾向现代 C++，建议优先 `nlohmann/json`

### B. 死代码

- `prompt_renderer.*`
- `litert_binary_path`

建议：

- 后续单独清理掉，避免继续误导结构认知

### C. `src/http_server.cpp` 过大

当前约 `908` 行，是全项目最大的单文件。

建议后续拆分：

- 请求解析
- OpenAI 协议转换
- SSE 输出
- 路由处理

### D. worker 构建承担了太多上游修补逻辑

当前 `worker/CMakeLists.txt` 已经不是“简单链接一个上游库”，而是在：

- 覆盖 package 目录
- 覆盖 scripts 目录
- 修补 LiteRT patcher
- 修补 generated code
- 手动重建最终链接列表

这说明：

- 当前 worker 方案在工程上是可行的
- 但构建耦合度已经很高


## 10. 现在最合理的依赖分类

### 第一类：必须保留

- `litert_http_service` 自身源码
- `litert_http_worker.cpp`
- `SQLite3`
- `Threads`
- 一套 JSON 库
- LiteRT-LM C API 及其最小运行时
- TFLite / LiteRT 核心运行时
- Abseil / Protobuf / Rust bridge
- tokenizer 相关最小集合

### 第二类：大概率可以裁掉

- LiteRT `tools/*`
- LiteRT `tools/flags/*`
- LiteRT `vendors/qualcomm/*`
- 音频 executor / preprocessor
- vision executor / image preprocessor
- tool use / parser / constrained decoding 相关
- 非 Gemma 的 model data processor

### 第三类：当前最不稳定、最值得重点排查

- `LITERTLM_DEPS` / aggregate / shim 体系
- Rust / cxxbridge 生成产物路径
- generated protobuf / generated rust bridge 头文件路径
- LiteRT 上游 patch 后仍残留的工具/厂商库引用


## 11. 下一步建议顺序

建议不要继续盲修编译，而是按下面顺序推进。

### 第一步

先做“最小运行目标定义”：

- 只保留文本聊天
- 只保留 Gemma 4
- 只保留 GPU backend
- 不做 tools
- 不做 image/audio

### 第二步

围绕这个最小目标，定义“最小依赖集合”：

- 哪些 LiteRT-LM 本地库必须留
- 哪些外部 aggregate 必须留
- 哪些工具/vendor/多模态库必须删

### 第三步

决定路线：

- 路线 A：继续嵌入上游 `cmake/packages/litert_lm`
- 路线 B：改成只编一个更小的官方 C API 子集或直接链接已有 runtime 产物

如果目标是稳定交付，我更建议优先评估路线 B。


## 12. 当前结论

一句话总结：

- 现在真正复杂的不是 HTTP 服务代码
- 真正复杂的是 `worker` 把 LiteRT-LM 整个上游构建系统重新嵌进来了
- 当前编译痛点主要不是“我们业务代码写错”，而是“依赖集合过宽 + aggregate/shim 体系过脆”

所以接下来最值得做的，不是继续追下一条编译错误，而是先把 worker 目标收缩成“文本 Gemma 4 常驻会话”的最小依赖闭包。
