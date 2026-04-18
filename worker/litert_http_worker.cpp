#include "c/engine.h"

#include <condition_variable>
#include <cstdlib>
#include <functional>
#include <iostream>
#include <memory>
#include <mutex>
#include <optional>
#include <sstream>
#include <string>
#include <unordered_map>
#include <utility>

#include "nlohmann/json.hpp"

namespace {

using Json = nlohmann::ordered_json;

using EngineSettingsPtr =
    std::unique_ptr<LiteRtLmEngineSettings,
                    decltype(&litert_lm_engine_settings_delete)>;
using EnginePtr =
    std::unique_ptr<LiteRtLmEngine, decltype(&litert_lm_engine_delete)>;
using SessionConfigPtr =
    std::unique_ptr<LiteRtLmSessionConfig,
                    decltype(&litert_lm_session_config_delete)>;
using ConversationConfigPtr =
    std::unique_ptr<LiteRtLmConversationConfig,
                    decltype(&litert_lm_conversation_config_delete)>;
using ConversationPtr =
    std::unique_ptr<LiteRtLmConversation,
                    decltype(&litert_lm_conversation_delete)>;

struct WorkerConfig {
  std::string model_path;
  std::string backend = "gpu";
  std::string cache_dir;
};

struct StreamAccumulator {
  std::mutex mutex;
  std::condition_variable cv;
  bool done = false;
  std::string error;
  std::string visible_text;
  Json channels = Json::object();
  std::function<void(const std::string&)> on_visible_text;
};

struct ConversationEntry {
  ConversationPtr conversation{
      nullptr, &litert_lm_conversation_delete};
  std::string mode;
  std::string system_prompt;
};

std::string JoinText(const Json& content) {
  if (content.is_string()) {
    return content.get<std::string>();
  }
  if (content.is_object() && content.contains("text") &&
      content["text"].is_string()) {
    return content["text"].get<std::string>();
  }
  if (!content.is_array()) {
    return {};
  }

  std::ostringstream stream;
  for (const auto& part : content) {
    if (part.is_object() && part.contains("text") && part["text"].is_string()) {
      stream << part["text"].get<std::string>();
    } else if (part.is_string()) {
      stream << part.get<std::string>();
    }
  }
  return stream.str();
}

std::string ExtractVisibleText(const Json& message) {
  if (!message.is_object() || !message.contains("content")) {
    return {};
  }
  return JoinText(message["content"]);
}

void MergeChannels(const Json& message, Json* channels) {
  if (!message.is_object() || !message.contains("channels") ||
      !message["channels"].is_object()) {
    return;
  }
  for (const auto& item : message["channels"].items()) {
    if (!item.value().is_string()) {
      continue;
    }
    const std::string name = item.key();
    const std::string text = item.value().get<std::string>();
    if (channels->contains(name) && (*channels)[name].is_string()) {
      (*channels)[name] = (*channels)[name].get<std::string>() + text;
    } else {
      (*channels)[name] = text;
    }
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
      accumulator->error = error_msg;
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

  Json message = Json::parse(chunk, nullptr, false);
  if (message.is_discarded()) {
    {
      std::lock_guard<std::mutex> lock(accumulator->mutex);
      accumulator->error = "Failed to parse LiteRT stream message JSON.";
      accumulator->done = true;
    }
    accumulator->cv.notify_all();
    return;
  }

  const std::string visible_text = ExtractVisibleText(message);
  {
    std::lock_guard<std::mutex> lock(accumulator->mutex);
    accumulator->visible_text += visible_text;
    MergeChannels(message, &accumulator->channels);
  }

  if (!visible_text.empty() && accumulator->on_visible_text) {
    accumulator->on_visible_text(visible_text);
  }
}

std::optional<WorkerConfig> ParseArgs(int argc, char** argv, std::string* error) {
  WorkerConfig config;
  for (int i = 1; i < argc; ++i) {
    std::string arg = argv[i];
    auto take_value = [&](const std::string& prefix) -> std::optional<std::string> {
      if (arg == prefix && i + 1 < argc) {
        return std::string(argv[++i]);
      }
      if (arg.rfind(prefix + "=", 0) == 0) {
        return arg.substr(prefix.size() + 1);
      }
      return std::nullopt;
    };

    if (auto value = take_value("--model-path")) {
      config.model_path = *value;
    } else if (auto value = take_value("--backend")) {
      config.backend = *value;
    } else if (auto value = take_value("--cache-dir")) {
      config.cache_dir = *value;
    } else if (arg == "-h" || arg == "--help") {
      if (error != nullptr) {
        *error = "Usage: litert_http_worker --model-path PATH "
                 "[--backend gpu|cpu] [--cache-dir PATH]";
      }
      return std::nullopt;
    } else {
      if (error != nullptr) {
        *error = "Unknown argument: " + arg;
      }
      return std::nullopt;
    }
  }

  if (config.model_path.empty()) {
    if (error != nullptr) {
      *error = "--model-path is required.";
    }
    return std::nullopt;
  }

  return config;
}

class Worker {
 public:
  explicit Worker(WorkerConfig config)
      : config_(std::move(config)),
        settings_(nullptr, &litert_lm_engine_settings_delete),
        engine_(nullptr, &litert_lm_engine_delete) {}

  bool Start(std::string* error) {
    litert_lm_set_min_log_level(2);
    settings_.reset(litert_lm_engine_settings_create(
        config_.model_path.c_str(), config_.backend.c_str(),
        /*vision_backend_str=*/nullptr, /*audio_backend_str=*/nullptr));
    if (!settings_) {
      if (error != nullptr) {
        *error = "Failed to create LiteRT engine settings.";
      }
      return false;
    }

    if (!config_.cache_dir.empty()) {
      litert_lm_engine_settings_set_cache_dir(settings_.get(),
                                              config_.cache_dir.c_str());
    }

    engine_.reset(litert_lm_engine_create(settings_.get()));
    if (!engine_) {
      if (error != nullptr) {
        *error = "Failed to create LiteRT engine.";
      }
      return false;
    }
    return true;
  }

  int RunLoop() {
    EmitEvent(Json{
        {"event", "ready"},
        {"backend", config_.backend},
        {"model_path", config_.model_path},
    });

    std::string line;
    while (std::getline(std::cin, line)) {
      if (line.empty()) {
        continue;
      }
      Json request = Json::parse(line, nullptr, false);
      if (request.is_discarded() || !request.is_object()) {
        EmitError("Invalid worker request JSON.");
        continue;
      }

      const std::string command =
          request.contains("command") && request["command"].is_string()
              ? request["command"].get<std::string>()
              : "";
      if (command != "chat") {
        EmitError("Unsupported worker command.");
        continue;
      }
      HandleChat(request);
    }
    return 0;
  }

 private:
  ConversationPtr CreateConversation(const std::string& system_prompt,
                                     const Json& history,
                                     std::string* error) {
    const Json system_message = Json{
        {"type", "text"},
        {"text", system_prompt},
    };
    const std::string system_json = system_prompt.empty() ? "" : system_message.dump();
    const std::string history_json =
        history.is_array() && !history.empty() ? history.dump() : "";

    ConversationConfigPtr config(
        litert_lm_conversation_config_create(
            engine_.get(),
            /*session_config=*/nullptr,
            system_json.empty() ? nullptr : system_json.c_str(),
            /*tools_json=*/nullptr,
            history_json.empty() ? nullptr : history_json.c_str(),
            /*enable_constrained_decoding=*/false),
        &litert_lm_conversation_config_delete);
    if (!config) {
      if (error != nullptr) {
        *error = "Failed to create conversation config.";
      }
      return ConversationPtr(nullptr, &litert_lm_conversation_delete);
    }

    ConversationPtr conversation(
        litert_lm_conversation_create(engine_.get(), config.get()),
        &litert_lm_conversation_delete);
    if (!conversation) {
      if (error != nullptr) {
        *error = "Failed to create conversation.";
      }
      return ConversationPtr(nullptr, &litert_lm_conversation_delete);
    }
    return conversation;
  }

  void HandleChat(const Json& request) {
    if (!request.contains("message") || !request["message"].is_object()) {
      EmitError("`message` is required.");
      return;
    }

    const std::string session_id =
        request.contains("session_id") && request["session_id"].is_string()
            ? request["session_id"].get<std::string>()
            : "";
    const bool stateful =
        request.contains("stateful") && request["stateful"].is_boolean()
            ? request["stateful"].get<bool>()
            : !session_id.empty();
    const std::string system_prompt =
        request.contains("system_prompt") && request["system_prompt"].is_string()
            ? request["system_prompt"].get<std::string>()
            : "";
    const std::string mode =
        request.contains("mode") && request["mode"].is_string()
            ? request["mode"].get<std::string>()
            : "chat";
    const Json history =
        request.contains("history") && request["history"].is_array()
            ? request["history"]
            : Json::array();
    const Json& message = request["message"];

    ConversationPtr ephemeral(nullptr, &litert_lm_conversation_delete);
    ConversationEntry* entry = nullptr;
    bool inserted = false;

    if (!stateful || session_id.empty()) {
      std::string error;
      ephemeral = CreateConversation(system_prompt, history, &error);
      if (!ephemeral) {
        EmitError(error);
        return;
      }
      entry = &ephemeral_entry_;
      entry->conversation = std::move(ephemeral);
      entry->system_prompt = system_prompt;
      entry->mode = mode;
    } else {
      auto found = conversations_.find(session_id);
      if (found == conversations_.end()) {
        std::string error;
        ConversationEntry created;
        created.conversation = CreateConversation(system_prompt, history, &error);
        if (!created.conversation) {
          EmitError(error);
          return;
        }
        created.system_prompt = system_prompt;
        created.mode = mode;
        found = conversations_.emplace(session_id, std::move(created)).first;
        inserted = true;
      }
      entry = &found->second;
      if (entry->mode != mode || entry->system_prompt != system_prompt) {
        if (inserted) {
          conversations_.erase(session_id);
        }
        EmitError("Existing worker session does not match requested mode or system prompt.");
        return;
      }
    }

    StreamAccumulator accumulator;
    accumulator.on_visible_text = [this](const std::string& chunk) {
      EmitEvent(Json{{"event", "chunk"}, {"text", chunk}});
    };

    const std::string message_json = message.dump();
    const int rc = litert_lm_conversation_send_message_stream(
        entry->conversation.get(), message_json.c_str(),
        /*extra_context=*/nullptr, &StreamCallback, &accumulator);
    if (rc != 0) {
      if (stateful && inserted) {
        conversations_.erase(session_id);
      }
      EmitError("Failed to start LiteRT conversation stream.");
      return;
    }

    {
      std::unique_lock<std::mutex> lock(accumulator.mutex);
      accumulator.cv.wait(lock, [&accumulator] { return accumulator.done; });
      if (!accumulator.error.empty()) {
        lock.unlock();
        if (stateful && inserted) {
          conversations_.erase(session_id);
        }
        EmitError(accumulator.error);
        return;
      }

      Json done{
          {"event", "done"},
          {"text", accumulator.visible_text},
      };
      if (!accumulator.channels.empty()) {
        done["channels"] = accumulator.channels;
      }
      lock.unlock();
      EmitEvent(done);
    }

    if (!stateful || session_id.empty()) {
      ephemeral_entry_ = ConversationEntry{};
    }
  }

  void EmitEvent(const Json& event) {
    std::lock_guard<std::mutex> lock(output_mutex_);
    std::cout << event.dump() << std::endl;
  }

  void EmitError(const std::string& message) {
    EmitEvent(Json{{"event", "error"}, {"message", message}});
  }

  WorkerConfig config_;
  EngineSettingsPtr settings_;
  EnginePtr engine_;
  std::unordered_map<std::string, ConversationEntry> conversations_;
  ConversationEntry ephemeral_entry_;
  std::mutex output_mutex_;
};

}  // namespace

int main(int argc, char** argv) {
  std::string error;
  auto config = ParseArgs(argc, argv, &error);
  if (!config.has_value()) {
    Json event{
        {"event", "error"},
        {"message", error.empty() ? "Invalid worker arguments." : error},
    };
    std::cout << event.dump() << std::endl;
    return error.rfind("Usage:", 0) == 0 ? 0 : 1;
  }

  Worker worker(*config);
  if (!worker.Start(&error)) {
    Json event{
        {"event", "error"},
        {"message", error.empty() ? "Failed to initialize worker." : error},
    };
    std::cout << event.dump() << std::endl;
    return 1;
  }

  return worker.RunLoop();
}
