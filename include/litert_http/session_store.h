#ifndef LITERT_HTTP_SESSION_STORE_H_
#define LITERT_HTTP_SESSION_STORE_H_

#include <mutex>
#include <optional>
#include <string>

#include <sqlite3.h>

#include "litert_http/types.h"

namespace litert_http {

class SessionStore {
 public:
  explicit SessionStore(std::string db_path);
  ~SessionStore();

  SessionStore(const SessionStore&) = delete;
  SessionStore& operator=(const SessionStore&) = delete;

  bool ok() const { return initialized_; }
  const std::string& error() const { return error_; }
  const std::string& db_path() const { return db_path_; }

  bool SaveSession(const SessionData& session, std::string* error);
  std::optional<SessionData> LoadSession(const std::string& session_id,
                                         std::string* error);

 private:
  bool Open();
  bool InitializeSchema();
  bool Exec(const std::string& sql);

  std::string db_path_;
  sqlite3* db_ = nullptr;
  bool initialized_ = false;
  std::string error_;
  mutable std::mutex mutex_;
};

}  // namespace litert_http

#endif  // LITERT_HTTP_SESSION_STORE_H_
