#include "litert_http/utils.h"

#include <chrono>
#include <cctype>
#include <cstdlib>
#include <functional>
#include <iomanip>
#include <sstream>

namespace litert_http {

std::string ExpandUserPath(const std::string& path) {
  if (path.empty() || path[0] != '~') {
    return path;
  }
  const char* home = std::getenv("HOME");
  if (home == nullptr) {
    return path;
  }
  if (path.size() == 1) {
    return std::string(home);
  }
  if (path[1] == '/') {
    return std::string(home) + path.substr(1);
  }
  return path;
}

std::string ToLower(std::string value) {
  for (char& ch : value) {
    ch = static_cast<char>(std::tolower(static_cast<unsigned char>(ch)));
  }
  return value;
}

std::string Trim(const std::string& value) {
  size_t start = 0;
  while (start < value.size() &&
         std::isspace(static_cast<unsigned char>(value[start]))) {
    ++start;
  }
  size_t end = value.size();
  while (end > start &&
         std::isspace(static_cast<unsigned char>(value[end - 1]))) {
    --end;
  }
  return value.substr(start, end - start);
}

std::string JoinStrings(const std::vector<std::string>& items,
                        const std::string& delimiter) {
  std::ostringstream stream;
  for (size_t i = 0; i < items.size(); ++i) {
    if (i > 0) {
      stream << delimiter;
    }
    stream << items[i];
  }
  return stream.str();
}

std::string HashString(const std::string& input) {
  std::hash<std::string> hasher;
  std::ostringstream stream;
  stream << std::hex << hasher(input);
  return stream.str();
}

int64_t NowUnixSeconds() {
  const auto now = std::chrono::system_clock::now();
  return std::chrono::duration_cast<std::chrono::seconds>(
             now.time_since_epoch())
      .count();
}

bool IsValidSessionId(const std::string& session_id) {
  if (session_id.empty() || session_id.size() > 128) {
    return false;
  }
  for (const char ch : session_id) {
    const bool allowed = std::isalnum(static_cast<unsigned char>(ch)) != 0 ||
                         ch == '-' || ch == '_' || ch == '.';
    if (!allowed) {
      return false;
    }
  }
  return true;
}

std::optional<Json::Value> ParseJson(const std::string& text,
                                     std::string* error) {
  Json::CharReaderBuilder builder;
  builder["collectComments"] = false;
  Json::Value root;
  std::string errs;
  std::unique_ptr<Json::CharReader> reader(builder.newCharReader());
  const bool ok =
      reader->parse(text.data(), text.data() + text.size(), &root, &errs);
  if (!ok) {
    if (error != nullptr) {
      *error = errs.empty() ? "Failed to parse JSON." : errs;
    }
    return std::nullopt;
  }
  return root;
}

std::string JsonToString(const Json::Value& value, bool pretty) {
  if (pretty) {
    Json::StreamWriterBuilder builder;
    builder["indentation"] = "  ";
    builder["emitUTF8"] = true;
    return Json::writeString(builder, value);
  }
  Json::StreamWriterBuilder builder;
  builder["indentation"] = "";
  builder["commentStyle"] = "None";
  builder["emitUTF8"] = true;
  return Json::writeString(builder, value);
}

}  // namespace litert_http
