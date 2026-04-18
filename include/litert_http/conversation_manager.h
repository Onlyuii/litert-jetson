#ifndef LITERT_HTTP_CONVERSATION_MANAGER_H_
#define LITERT_HTTP_CONVERSATION_MANAGER_H_

#include <mutex>
#include <optional>
#include <string>
#include <unordered_map>
#include <vector>

#include "litert_http/session_store.h"
#include "litert_http/types.h"

namespace litert_http {

struct TurnPreparation {
  bool ok = false;
  bool newly_created = false;
  std::string error;
  SessionData session;
};

class ConversationManager {
 public:
  explicit ConversationManager(SessionStore* store);

  TurnPreparation PrepareTurn(const std::string& session_id,
                              const ModelSpec& model,
                              const std::string& system_prompt,
                              const std::vector<ChatMessage>& request_messages,
                              bool has_explicit_system_prompt);

  bool CommitTurn(const std::string& session_id, const SessionData& prepared,
                  const std::string& assistant_reply, std::string* error);

 private:
  SessionStore* store_;
  std::mutex mutex_;
  std::unordered_map<std::string, SessionData> cache_;
};

}  // namespace litert_http

#endif  // LITERT_HTTP_CONVERSATION_MANAGER_H_
