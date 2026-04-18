#ifndef LITERT_HTTP_CONFIG_H_
#define LITERT_HTTP_CONFIG_H_

#include <optional>
#include <string>

namespace litert_http {

struct ServiceConfig {
  std::string bind_host = "0.0.0.0";
  int port = 8080;
  std::string api_key;
  std::string backend = "gpu";
  std::string cache_dir;
  std::string model_2b_path;
  std::string model_4b_path;
  std::string session_db_path;
  std::string default_system_prompt = "You are a helpful local assistant.";
};

std::optional<ServiceConfig> LoadConfigFromEnvAndArgs(
    int argc, char** argv, std::string* error);

}  // namespace litert_http

#endif  // LITERT_HTTP_CONFIG_H_
