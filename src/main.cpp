#include <csignal>
#include <iostream>
#include <memory>

#include "litert_http/config.h"
#include "litert_http/conversation_manager.h"
#include "litert_http/engine_manager.h"
#include "litert_http/http_server.h"
#include "litert_http/inference_backend.h"
#include "litert_http/session_store.h"

int main(int argc, char** argv) {
  std::signal(SIGPIPE, SIG_IGN);

  std::string config_error;
  auto config = litert_http::LoadConfigFromEnvAndArgs(argc, argv, &config_error);
  if (!config.has_value()) {
    std::cerr << config_error << '\n';
    return config_error.rfind("Usage:", 0) == 0 ? 0 : 1;
  }

  litert_http::SessionStore store(config->session_db_path);
  if (!store.ok()) {
    std::cerr << "Failed to initialize session store: " << store.error() << '\n';
    return 1;
  }

  auto backend = litert_http::CreateDefaultBackend();
  litert_http::EngineManager engine_manager(*config, std::move(backend));
  litert_http::ConversationManager conversation_manager(&store);
  litert_http::HttpServer server(&engine_manager, &engine_manager,
                                 &conversation_manager);
  return server.Run();
}
