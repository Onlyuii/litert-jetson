#include "litert_http/config.h"

#include <cstdlib>
#include <filesystem>
#include <optional>
#include <string>

#include "litert_http/utils.h"

namespace litert_http {
namespace {

std::string GetEnvOrDefault(const char* name, const std::string& fallback) {
  const char* value = std::getenv(name);
  if (value == nullptr || std::string(value).empty()) {
    return fallback;
  }
  return value;
}

bool ParsePort(const std::string& value, int* port) {
  try {
    const int parsed = std::stoi(value);
    if (parsed <= 0 || parsed > 65535) {
      return false;
    }
    *port = parsed;
    return true;
  } catch (...) {
    return false;
  }
}

}  // namespace

std::optional<ServiceConfig> LoadConfigFromEnvAndArgs(
    int argc, char** argv, std::string* error) {
  ServiceConfig config;
  config.cache_dir = ExpandUserPath(
      GetEnvOrDefault("LITERT_LM_CACHE_DIR",
                      "~/litert-agent-data/litert-cache"));
  config.model_2b_path = ExpandUserPath(
      GetEnvOrDefault("LITERT_LM_MODEL_2B",
                      "~/litert-lm-models/gemma-4-E2B-it/model.litertlm"));
  config.model_4b_path = ExpandUserPath(GetEnvOrDefault(
      "LITERT_LM_MODEL_4B",
      "~/litert-lm-models/gemma-4-E4B-it/gemma-4-E4B-it.litertlm"));
  config.session_db_path = ExpandUserPath(
      GetEnvOrDefault("LITERT_HTTP_DB_PATH",
                      "~/litert-agent-data/sessions.db"));
  config.backend = GetEnvOrDefault("LITERT_LM_BACKEND", "gpu");
  config.bind_host = GetEnvOrDefault("LITERT_HTTP_BIND", "0.0.0.0");
  config.api_key = GetEnvOrDefault("LITERT_HTTP_API_KEY", "");
  config.default_system_prompt =
      GetEnvOrDefault("LITERT_HTTP_SYSTEM_PROMPT",
                      "You are a helpful local assistant.");

  const std::string port_env = GetEnvOrDefault("LITERT_HTTP_PORT", "8080");
  if (!ParsePort(port_env, &config.port)) {
    if (error != nullptr) {
      *error = "Invalid LITERT_HTTP_PORT value: " + port_env;
    }
    return std::nullopt;
  }

  for (int i = 1; i < argc; ++i) {
    const std::string arg = argv[i];
    if (arg == "--bind" && i + 1 < argc) {
      config.bind_host = argv[++i];
    } else if (arg == "--port" && i + 1 < argc) {
      if (!ParsePort(argv[++i], &config.port)) {
        if (error != nullptr) {
          *error = "Invalid --port value.";
        }
        return std::nullopt;
      }
    } else if (arg == "--api-key" && i + 1 < argc) {
      config.api_key = argv[++i];
    } else if (arg == "--backend" && i + 1 < argc) {
      config.backend = argv[++i];
    } else if (arg == "--model-2b" && i + 1 < argc) {
      config.model_2b_path = ExpandUserPath(argv[++i]);
    } else if (arg == "--model-4b" && i + 1 < argc) {
      config.model_4b_path = ExpandUserPath(argv[++i]);
    } else if (arg == "--cache-dir" && i + 1 < argc) {
      config.cache_dir = ExpandUserPath(argv[++i]);
    } else if (arg == "--db-path" && i + 1 < argc) {
      config.session_db_path = ExpandUserPath(argv[++i]);
    } else if (arg == "--system-prompt" && i + 1 < argc) {
      config.default_system_prompt = argv[++i];
    } else if (arg == "--help" || arg == "-h") {
      if (error != nullptr) {
        *error =
            "Usage: litert_http_service [--bind HOST] [--port PORT] "
            "[--api-key KEY] [--backend gpu|cpu] [--model-2b PATH] "
            "[--model-4b PATH] "
            "[--cache-dir PATH] [--db-path PATH]";
      }
      return std::nullopt;
    } else {
      if (error != nullptr) {
        *error = "Unknown argument: " + arg;
      }
      return std::nullopt;
    }
  }

  if (config.api_key.empty()) {
    if (error != nullptr) {
      *error =
          "API key is required. Set LITERT_HTTP_API_KEY or pass --api-key.";
    }
    return std::nullopt;
  }

  if (!std::filesystem::exists(config.model_2b_path)) {
    if (error != nullptr) {
      *error = "2B model file not found: " + config.model_2b_path;
    }
    return std::nullopt;
  }

  if (!std::filesystem::exists(config.model_4b_path)) {
    if (error != nullptr) {
      *error = "4B model file not found: " + config.model_4b_path;
    }
    return std::nullopt;
  }

  std::filesystem::create_directories(
      std::filesystem::path(config.session_db_path).parent_path());
  std::filesystem::create_directories(config.cache_dir);

  return config;
}

}  // namespace litert_http
