#include "litert_http/inference_backend.h"

#include <chrono>
#include <condition_variable>
#include <memory>
#include <mutex>
#include <string>
#include <unordered_map>
#include <utility>
#include <vector>

#include "c/engine.h"

#include "litert_http/utils.h"

namespace litert_http {
namespace {

using EngineSettingsPtr =
    std::unique_ptr<LiteRtLmEngineSettings,
                    decltype(&litert_lm_engine_settings_delete)>;
using EnginePtr =
    std::unique_ptr<LiteRtLmEngine, decltype(&litert_lm_engine_delete)>;
using ConversationConfigPtr =
    std::unique_ptr<LiteRtLmConversationConfig,
                    decltype(&litert_lm_conversation_config_delete)>;
using ConversationPtr =
    std::unique_ptr<LiteRtLmConversation,
                    decltype(&litert_lm_conversation_delete)>;

struct ConversationEntry {
  ConversationPtr conversation{nullptr, &litert_lm_conversation_delete};
  std::string mode;
  std::string system_prompt_hash;
};

struct StreamAccumulator {
  std::mutex mutex;
  std::condition_variable cv;
  bool done = false;
  bool cancel_requested = false;
  std::string error;
  std::string visible_text;
  LiteRtLmConversation* conversation = nullptr;
  ChunkCallback on_chunk;
};

Json::Value BuildTextPart(const std::string& text) {
  Json::Value part(Json::objectValue);
  part["type"] = "text";
  part["text"] = text;
  return part;
}

Json::Value BuildConversationMessage(const ChatMessage& message) {
  Json::Value root(Json::objectValue);
  root["role"] = message.role;
  Json::Value content(Json::arrayValue);
  content.append(BuildTextPart(message.content));
  root["content"] = content;
  return root;
}

Json::Value BuildConversationHistory(const std::vector<ChatMessage>& messages) {
  Json::Value history(Json::arrayValue);
  for (const auto& message : messages) {
    history.append(BuildConversationMessage(message));
  }
  return history;
}

std::string ExtractTextFromContent(const Json::Value& content) {
  if (content.isString()) {
    return content.asString();
  }
  if (content.isObject() && content.isMember("text") &&
      content["text"].isString()) {
    return content["text"].asString();
  }
  if (!content.isArray()) {
    return {};
  }

  std::vector<std::string> parts;
  for (const auto& part : content) {
    if (part.isObject() && part.isMember("text") && part["text"].isString()) {
      parts.push_back(part["text"].asString());
    } else if (part.isString()) {
      parts.push_back(part.asString());
    }
  }
  return JoinStrings(parts, "");
}

std::string ExtractVisibleText(const Json::Value& message) {
  if (!message.isObject() || !message.isMember("content")) {
    return {};
  }
  return ExtractTextFromContent(message["content"]);
}

std::string BuildExtraContext(const TurnInput& turn) {
  if (turn.mode != ChatMode::kThinking) {
    return {};
  }
  Json::Value extra_context(Json::objectValue);
  extra_context["enable_thinking"] = true;
  return JsonToString(extra_context);
}

void RequestCancel(StreamAccumulator* accumulator, const std::string& error) {
  if (accumulator == nullptr) {
    return;
  }

  bool should_cancel = false;
  {
    std::lock_guard<std::mutex> lock(accumulator->mutex);
    if (!accumulator->cancel_requested) {
      accumulator->cancel_requested = true;
      accumulator->error = error;
      should_cancel = true;
    }
  }

  if (should_cancel && accumulator->conversation != nullptr) {
    litert_lm_conversation_cancel_process(accumulator->conversation);
  }
}

void StreamCallback(void* callback_data, const char* chunk, bool is_final,
                    const char* error_msg) {
  auto* accumulator = static_cast<StreamAccumulator*>(callback_data);
  if (accumulator == nullptr) {
    return;
  }

  if (error_msg != nullptr) {
    {
      std::lock_guard<std::mutex> lock(accumulator->mutex);
      if (accumulator->error.empty()) {
        accumulator->error = error_msg;
      }
      accumulator->done = true;
    }
    accumulator->cv.notify_all();
    return;
  }

  if (is_final) {
    {
      std::lock_guard<std::mutex> lock(accumulator->mutex);
      accumulator->done = true;
    }
    accumulator->cv.notify_all();
    return;
  }

  if (chunk == nullptr) {
    return;
  }

  std::string parse_error;
  auto message = ParseJson(chunk, &parse_error);
  if (!message.has_value() || !message->isObject()) {
    RequestCancel(accumulator,
                  parse_error.empty()
                      ? "Failed to parse LiteRT stream chunk."
                      : parse_error);
    return;
  }

  const std::string visible_text = ExtractVisibleText(*message);
  {
    std::lock_guard<std::mutex> lock(accumulator->mutex);
    accumulator->visible_text += visible_text;
  }

  if (!visible_text.empty() && accumulator->on_chunk &&
      !accumulator->on_chunk(visible_text)) {
    RequestCancel(accumulator, "Streaming client disconnected.");
  }
}

}  // namespace

struct LitertCapiBackend::Impl {
  std::mutex mutex;
  EngineSettingsPtr settings{nullptr, &litert_lm_engine_settings_delete};
  EnginePtr engine{nullptr, &litert_lm_engine_delete};
  std::string loaded_base_model;
  std::unordered_map<std::string, ConversationEntry> conversations;

  void Reset() {
    conversations.clear();
    engine.reset();
    settings.reset();
    loaded_base_model.clear();
  }

  bool EnsureEngine(const ServiceConfig& config, const ModelSpec& model,
                    std::string* error) {
    if (engine && loaded_base_model == model.base_model_id) {
      return true;
    }

    Reset();
    litert_lm_set_min_log_level(2);

    settings.reset(litert_lm_engine_settings_create(
        model.model_path.c_str(), config.backend.c_str(),
        /*vision_backend_str=*/nullptr, /*audio_backend_str=*/nullptr));
    if (!settings) {
      if (error != nullptr) {
        *error = "Failed to create LiteRT engine settings.";
      }
      return false;
    }

    if (!config.cache_dir.empty()) {
      litert_lm_engine_settings_set_cache_dir(settings.get(),
                                              config.cache_dir.c_str());
    }

    engine.reset(litert_lm_engine_create(settings.get()));
    if (!engine) {
      if (error != nullptr) {
        *error = "Failed to create LiteRT engine.";
      }
      settings.reset();
      return false;
    }

    loaded_base_model = model.base_model_id;
    return true;
  }

  ConversationPtr CreateConversation(const TurnInput& turn, std::string* error) {
    const Json::Value system_message =
        turn.system_prompt.empty() ? Json::Value(Json::nullValue)
                                   : BuildTextPart(turn.system_prompt);
    const std::string system_json =
        system_message.isNull() ? std::string() : JsonToString(system_message);
    const Json::Value history = BuildConversationHistory(turn.history_messages);
    const std::string history_json =
        history.empty() ? std::string() : JsonToString(history);

    ConversationConfigPtr config(
        litert_lm_conversation_config_create(
            engine.get(),
            /*session_config=*/nullptr,
            system_json.empty() ? nullptr : system_json.c_str(),
            /*tools_json=*/nullptr,
            history_json.empty() ? nullptr : history_json.c_str(),
            /*enable_constrained_decoding=*/false),
        &litert_lm_conversation_config_delete);
    if (!config) {
      if (error != nullptr) {
        *error = "Failed to create LiteRT conversation config.";
      }
      return ConversationPtr(nullptr, &litert_lm_conversation_delete);
    }

    ConversationPtr conversation(
        litert_lm_conversation_create(engine.get(), config.get()),
        &litert_lm_conversation_delete);
    if (!conversation) {
      if (error != nullptr) {
        *error = "Failed to create LiteRT conversation.";
      }
      return ConversationPtr(nullptr, &litert_lm_conversation_delete);
    }
    return conversation;
  }

  LiteRtLmConversation* AcquireConversation(const TurnInput& turn,
                                            ConversationPtr* ephemeral,
                                            std::string* error) {
    const std::string mode = ModeToString(turn.mode);
    const std::string system_prompt_hash = HashString(turn.system_prompt);

    if (!turn.stateful || turn.session_id.empty()) {
      *ephemeral = CreateConversation(turn, error);
      return ephemeral->get();
    }

    auto found = conversations.find(turn.session_id);
    if (found == conversations.end()) {
      ConversationEntry entry;
      entry.conversation = CreateConversation(turn, error);
      if (!entry.conversation) {
        return nullptr;
      }
      entry.mode = mode;
      entry.system_prompt_hash = system_prompt_hash;
      found = conversations.emplace(turn.session_id, std::move(entry)).first;
    }

    if (found->second.mode != mode ||
        found->second.system_prompt_hash != system_prompt_hash) {
      if (error != nullptr) {
        *error =
            "Existing in-memory session does not match requested mode or system prompt.";
      }
      return nullptr;
    }

    return found->second.conversation.get();
  }

  void DropConversation(const std::string& session_id) {
    if (session_id.empty()) {
      return;
    }
    conversations.erase(session_id);
  }
};

LitertCapiBackend::LitertCapiBackend() : impl_(std::make_unique<Impl>()) {}

LitertCapiBackend::~LitertCapiBackend() = default;

InferenceResult LitertCapiBackend::Run(const ServiceConfig& config,
                                       const ModelSpec& model,
                                       const TurnInput& turn,
                                       const ChunkCallback& on_chunk) {
  const auto started_at = std::chrono::steady_clock::now();
  InferenceResult result;

  ConversationPtr ephemeral(nullptr, &litert_lm_conversation_delete);
  LiteRtLmConversation* conversation = nullptr;

  {
    std::lock_guard<std::mutex> lock(impl_->mutex);
    if (!impl_->EnsureEngine(config, model, &result.error)) {
      result.duration_ms = std::chrono::duration_cast<std::chrono::milliseconds>(
                               std::chrono::steady_clock::now() - started_at)
                               .count();
      return result;
    }

    conversation = impl_->AcquireConversation(turn, &ephemeral, &result.error);
    if (conversation == nullptr) {
      result.duration_ms = std::chrono::duration_cast<std::chrono::milliseconds>(
                               std::chrono::steady_clock::now() - started_at)
                               .count();
      return result;
    }
  }

  StreamAccumulator accumulator;
  accumulator.conversation = conversation;
  accumulator.on_chunk = on_chunk;

  const Json::Value message_json = BuildConversationMessage(turn.user_message);
  const std::string message_payload = JsonToString(message_json);
  const std::string extra_context = BuildExtraContext(turn);

  const int rc = litert_lm_conversation_send_message_stream(
      conversation, message_payload.c_str(),
      extra_context.empty() ? nullptr : extra_context.c_str(), &StreamCallback,
      &accumulator);
  if (rc != 0) {
    if (turn.stateful && !turn.session_id.empty()) {
      std::lock_guard<std::mutex> lock(impl_->mutex);
      impl_->DropConversation(turn.session_id);
    }
    result.error = "Failed to start LiteRT conversation stream.";
    result.duration_ms = std::chrono::duration_cast<std::chrono::milliseconds>(
                             std::chrono::steady_clock::now() - started_at)
                             .count();
    return result;
  }

  {
    std::unique_lock<std::mutex> lock(accumulator.mutex);
    accumulator.cv.wait(lock, [&accumulator] { return accumulator.done; });
    result.text = accumulator.visible_text;
    result.error = accumulator.error;
  }

  if (!result.error.empty() && turn.stateful && !turn.session_id.empty()) {
    std::lock_guard<std::mutex> lock(impl_->mutex);
    impl_->DropConversation(turn.session_id);
  }

  result.ok = result.error.empty();
  result.duration_ms = std::chrono::duration_cast<std::chrono::milliseconds>(
                           std::chrono::steady_clock::now() - started_at)
                           .count();
  return result;
}

std::unique_ptr<InferenceBackend> CreateDefaultBackend() {
  return std::make_unique<LitertCapiBackend>();
}

}  // namespace litert_http
