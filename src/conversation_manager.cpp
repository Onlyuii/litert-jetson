#include "litert_http/conversation_manager.h"

#include <vector>

#include "litert_http/utils.h"

namespace litert_http {

ConversationManager::ConversationManager(SessionStore* store) : store_(store) {}

TurnPreparation ConversationManager::PrepareTurn(
    const std::string& session_id, const ModelSpec& model,
    const std::string& system_prompt,
    const std::vector<ChatMessage>& request_messages,
    bool has_explicit_system_prompt) {
  TurnPreparation result;
  if (!IsValidSessionId(session_id)) {
    result.error = "Invalid X-Session-Id.";
    return result;
  }

  std::lock_guard<std::mutex> lock(mutex_);
  auto cached = cache_.find(session_id);
  if (cached == cache_.end()) {
    std::string load_error;
    auto stored = store_->LoadSession(session_id, &load_error);
    if (!load_error.empty()) {
      result.error = load_error;
      return result;
    }
    if (stored.has_value()) {
      cached = cache_.emplace(session_id, *stored).first;
    }
  }

  if (cached == cache_.end()) {
    if (request_messages.empty()) {
      result.error = "New stateful session requires at least one message.";
      return result;
    }
    std::vector<ChatMessage> non_system = request_messages;
    if (non_system.back().role != "user") {
      result.error = "New stateful session must end with a user message.";
      return result;
    }
    SessionData session;
    session.session_id = session_id;
    session.logical_model_id = model.id;
    session.base_model_id = model.base_model_id;
    session.mode = model.mode;
    session.system_prompt = system_prompt;
    session.system_prompt_hash = HashString(system_prompt);
    session.last_access_at = NowUnixSeconds();
    session.messages = std::move(non_system);
    result.ok = true;
    result.newly_created = true;
    result.session = std::move(session);
    return result;
  }

  if (cached->second.logical_model_id != model.id ||
      cached->second.base_model_id != model.base_model_id ||
      cached->second.mode != model.mode) {
    result.error =
        "Existing session model or mode does not match this request.";
    return result;
  }

  if (has_explicit_system_prompt) {
    result.error =
        "Existing stateful session cannot override the stored system prompt.";
    return result;
  }

  if (request_messages.size() != 1 || request_messages[0].role != "user") {
    result.error =
        "Existing stateful session only accepts one new user message.";
    return result;
  }

  SessionData prepared = cached->second;
  prepared.messages.push_back(request_messages[0]);
  prepared.last_access_at = NowUnixSeconds();
  result.ok = true;
  result.session = std::move(prepared);
  return result;
}

bool ConversationManager::CommitTurn(const std::string& session_id,
                                     const SessionData& prepared,
                                     const std::string& assistant_reply,
                                     std::string* error) {
  std::lock_guard<std::mutex> lock(mutex_);
  SessionData committed = prepared;
  committed.messages.push_back(ChatMessage{
      .role = "assistant",
      .content = assistant_reply,
  });
  committed.last_access_at = NowUnixSeconds();

  if (!store_->SaveSession(committed, error)) {
    return false;
  }
  cache_[session_id] = std::move(committed);
  return true;
}

}  // namespace litert_http
