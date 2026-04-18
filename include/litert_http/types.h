#ifndef LITERT_HTTP_TYPES_H_
#define LITERT_HTTP_TYPES_H_

#include <cstdint>
#include <functional>
#include <string>
#include <vector>

namespace litert_http {

enum class ChatMode {
  kChat,
  kThinking,
};

struct ChatMessage {
  std::string role;
  std::string content;
};

struct ModelSpec {
  std::string id;
  std::string base_model_id;
  std::string model_path;
  ChatMode mode = ChatMode::kChat;
};

struct SessionData {
  std::string session_id;
  std::string logical_model_id;
  std::string base_model_id;
  ChatMode mode = ChatMode::kChat;
  std::string system_prompt;
  std::string system_prompt_hash;
  int64_t last_access_at = 0;
  std::vector<ChatMessage> messages;
};

struct TurnInput {
  std::string session_id;
  std::string system_prompt;
  ChatMode mode = ChatMode::kChat;
  std::vector<ChatMessage> history_messages;
  ChatMessage user_message;
  bool stateful = false;
};

struct InferenceResult {
  bool ok = false;
  int exit_code = 0;
  int64_t duration_ms = 0;
  std::string text;
  std::string error;
};

struct HealthSnapshot {
  std::string status = "ok";
  std::string backend;
  std::string model_state = "cold";
  std::string current_model;
  std::string last_error;
  int queue_depth = 0;
  int active_requests = 0;
  bool model_loaded = false;
};

using ChunkCallback = std::function<bool(const std::string&)>;

inline std::string ModeToString(ChatMode mode) {
  return mode == ChatMode::kThinking ? "thinking" : "chat";
}

inline ChatMode StringToMode(const std::string& mode) {
  return mode == "thinking" ? ChatMode::kThinking : ChatMode::kChat;
}

}  // namespace litert_http

#endif  // LITERT_HTTP_TYPES_H_
