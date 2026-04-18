#include "litert_http/session_store.h"

#include <sqlite3.h>

#include <sstream>

namespace litert_http {
namespace {

bool BindText(sqlite3_stmt* stmt, int index, const std::string& value) {
  return sqlite3_bind_text(stmt, index, value.c_str(),
                           static_cast<int>(value.size()),
                           SQLITE_TRANSIENT) == SQLITE_OK;
}

}  // namespace

SessionStore::SessionStore(std::string db_path) : db_path_(std::move(db_path)) {
  initialized_ = Open() && InitializeSchema();
}

SessionStore::~SessionStore() {
  if (db_ != nullptr) {
    sqlite3_close(db_);
    db_ = nullptr;
  }
}

bool SessionStore::Open() {
  if (sqlite3_open(db_path_.c_str(), &db_) != SQLITE_OK) {
    error_ = sqlite3_errmsg(db_);
    return false;
  }
  return true;
}

bool SessionStore::Exec(const std::string& sql) {
  char* errmsg = nullptr;
  const int rc = sqlite3_exec(db_, sql.c_str(), nullptr, nullptr, &errmsg);
  if (rc != SQLITE_OK) {
    error_ = errmsg == nullptr ? "SQLite exec failed." : errmsg;
    sqlite3_free(errmsg);
    return false;
  }
  return true;
}

bool SessionStore::InitializeSchema() {
  return Exec(
      "PRAGMA journal_mode=WAL;"
      "CREATE TABLE IF NOT EXISTS sessions ("
      "  session_id TEXT PRIMARY KEY,"
      "  logical_model_id TEXT NOT NULL,"
      "  base_model_id TEXT NOT NULL,"
      "  mode TEXT NOT NULL,"
      "  system_prompt TEXT NOT NULL,"
      "  system_prompt_hash TEXT NOT NULL,"
      "  last_access_at INTEGER NOT NULL"
      ");"
      "CREATE TABLE IF NOT EXISTS messages ("
      "  session_id TEXT NOT NULL,"
      "  seq INTEGER NOT NULL,"
      "  role TEXT NOT NULL,"
      "  content TEXT NOT NULL,"
      "  PRIMARY KEY (session_id, seq)"
      ");");
}

bool SessionStore::SaveSession(const SessionData& session, std::string* error) {
  std::lock_guard<std::mutex> lock(mutex_);
  if (!initialized_) {
    if (error != nullptr) {
      *error = error_;
    }
    return false;
  }

  sqlite3_exec(db_, "BEGIN IMMEDIATE TRANSACTION", nullptr, nullptr, nullptr);

  sqlite3_stmt* session_stmt = nullptr;
  const char* upsert_sql =
      "INSERT INTO sessions(session_id, logical_model_id, base_model_id, mode,"
      " system_prompt, system_prompt_hash, last_access_at)"
      " VALUES(?, ?, ?, ?, ?, ?, ?)"
      " ON CONFLICT(session_id) DO UPDATE SET"
      " logical_model_id=excluded.logical_model_id,"
      " base_model_id=excluded.base_model_id,"
      " mode=excluded.mode,"
      " system_prompt=excluded.system_prompt,"
      " system_prompt_hash=excluded.system_prompt_hash,"
      " last_access_at=excluded.last_access_at;";
  if (sqlite3_prepare_v2(db_, upsert_sql, -1, &session_stmt, nullptr) !=
      SQLITE_OK) {
    if (error != nullptr) {
      *error = sqlite3_errmsg(db_);
    }
    sqlite3_exec(db_, "ROLLBACK", nullptr, nullptr, nullptr);
    return false;
  }

  BindText(session_stmt, 1, session.session_id);
  BindText(session_stmt, 2, session.logical_model_id);
  BindText(session_stmt, 3, session.base_model_id);
  BindText(session_stmt, 4, ModeToString(session.mode));
  BindText(session_stmt, 5, session.system_prompt);
  BindText(session_stmt, 6, session.system_prompt_hash);
  sqlite3_bind_int64(session_stmt, 7, session.last_access_at);

  if (sqlite3_step(session_stmt) != SQLITE_DONE) {
    if (error != nullptr) {
      *error = sqlite3_errmsg(db_);
    }
    sqlite3_finalize(session_stmt);
    sqlite3_exec(db_, "ROLLBACK", nullptr, nullptr, nullptr);
    return false;
  }
  sqlite3_finalize(session_stmt);

  sqlite3_stmt* delete_stmt = nullptr;
  if (sqlite3_prepare_v2(db_, "DELETE FROM messages WHERE session_id = ?;", -1,
                         &delete_stmt, nullptr) != SQLITE_OK) {
    if (error != nullptr) {
      *error = sqlite3_errmsg(db_);
    }
    sqlite3_exec(db_, "ROLLBACK", nullptr, nullptr, nullptr);
    return false;
  }
  BindText(delete_stmt, 1, session.session_id);
  if (sqlite3_step(delete_stmt) != SQLITE_DONE) {
    if (error != nullptr) {
      *error = sqlite3_errmsg(db_);
    }
    sqlite3_finalize(delete_stmt);
    sqlite3_exec(db_, "ROLLBACK", nullptr, nullptr, nullptr);
    return false;
  }
  sqlite3_finalize(delete_stmt);

  sqlite3_stmt* insert_stmt = nullptr;
  const char* insert_sql =
      "INSERT INTO messages(session_id, seq, role, content) VALUES(?, ?, ?, ?);";
  if (sqlite3_prepare_v2(db_, insert_sql, -1, &insert_stmt, nullptr) !=
      SQLITE_OK) {
    if (error != nullptr) {
      *error = sqlite3_errmsg(db_);
    }
    sqlite3_exec(db_, "ROLLBACK", nullptr, nullptr, nullptr);
    return false;
  }

  for (size_t i = 0; i < session.messages.size(); ++i) {
    sqlite3_reset(insert_stmt);
    sqlite3_clear_bindings(insert_stmt);
    BindText(insert_stmt, 1, session.session_id);
    sqlite3_bind_int(insert_stmt, 2, static_cast<int>(i));
    BindText(insert_stmt, 3, session.messages[i].role);
    BindText(insert_stmt, 4, session.messages[i].content);
    if (sqlite3_step(insert_stmt) != SQLITE_DONE) {
      if (error != nullptr) {
        *error = sqlite3_errmsg(db_);
      }
      sqlite3_finalize(insert_stmt);
      sqlite3_exec(db_, "ROLLBACK", nullptr, nullptr, nullptr);
      return false;
    }
  }
  sqlite3_finalize(insert_stmt);
  sqlite3_exec(db_, "COMMIT", nullptr, nullptr, nullptr);
  return true;
}

std::optional<SessionData> SessionStore::LoadSession(const std::string& session_id,
                                                     std::string* error) {
  std::lock_guard<std::mutex> lock(mutex_);
  if (!initialized_) {
    if (error != nullptr) {
      *error = error_;
    }
    return std::nullopt;
  }

  sqlite3_stmt* session_stmt = nullptr;
  const char* sql =
      "SELECT logical_model_id, base_model_id, mode, system_prompt,"
      " system_prompt_hash, last_access_at"
      " FROM sessions WHERE session_id = ?;";
  if (sqlite3_prepare_v2(db_, sql, -1, &session_stmt, nullptr) != SQLITE_OK) {
    if (error != nullptr) {
      *error = sqlite3_errmsg(db_);
    }
    return std::nullopt;
  }
  BindText(session_stmt, 1, session_id);
  const int rc = sqlite3_step(session_stmt);
  if (rc == SQLITE_DONE) {
    sqlite3_finalize(session_stmt);
    return std::nullopt;
  }
  if (rc != SQLITE_ROW) {
    if (error != nullptr) {
      *error = sqlite3_errmsg(db_);
    }
    sqlite3_finalize(session_stmt);
    return std::nullopt;
  }

  SessionData session;
  session.session_id = session_id;
  session.logical_model_id =
      reinterpret_cast<const char*>(sqlite3_column_text(session_stmt, 0));
  session.base_model_id =
      reinterpret_cast<const char*>(sqlite3_column_text(session_stmt, 1));
  session.mode = StringToMode(
      reinterpret_cast<const char*>(sqlite3_column_text(session_stmt, 2)));
  session.system_prompt =
      reinterpret_cast<const char*>(sqlite3_column_text(session_stmt, 3));
  session.system_prompt_hash =
      reinterpret_cast<const char*>(sqlite3_column_text(session_stmt, 4));
  session.last_access_at = sqlite3_column_int64(session_stmt, 5);
  sqlite3_finalize(session_stmt);

  sqlite3_stmt* message_stmt = nullptr;
  const char* messages_sql =
      "SELECT role, content FROM messages WHERE session_id = ? ORDER BY seq ASC;";
  if (sqlite3_prepare_v2(db_, messages_sql, -1, &message_stmt, nullptr) !=
      SQLITE_OK) {
    if (error != nullptr) {
      *error = sqlite3_errmsg(db_);
    }
    return std::nullopt;
  }
  BindText(message_stmt, 1, session_id);
  while (sqlite3_step(message_stmt) == SQLITE_ROW) {
    ChatMessage message;
    message.role =
        reinterpret_cast<const char*>(sqlite3_column_text(message_stmt, 0));
    message.content =
        reinterpret_cast<const char*>(sqlite3_column_text(message_stmt, 1));
    session.messages.push_back(std::move(message));
  }
  sqlite3_finalize(message_stmt);
  return session;
}

}  // namespace litert_http
