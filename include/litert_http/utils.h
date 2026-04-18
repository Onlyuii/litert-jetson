#ifndef LITERT_HTTP_UTILS_H_
#define LITERT_HTTP_UTILS_H_

#include <cstdint>
#include <optional>
#include <string>
#include <vector>

#include <json/json.h>

namespace litert_http {

std::string ExpandUserPath(const std::string& path);
std::string ToLower(std::string value);
std::string Trim(const std::string& value);
std::string JoinStrings(const std::vector<std::string>& items,
                        const std::string& delimiter);
std::string HashString(const std::string& input);
int64_t NowUnixSeconds();
bool IsValidSessionId(const std::string& session_id);

std::optional<Json::Value> ParseJson(const std::string& text,
                                     std::string* error);
std::string JsonToString(const Json::Value& value, bool pretty = false);

}  // namespace litert_http

#endif  // LITERT_HTTP_UTILS_H_
