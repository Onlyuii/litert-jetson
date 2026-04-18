#ifndef LITERT_HTTP_HTTP_SERVER_H_
#define LITERT_HTTP_HTTP_SERVER_H_

#include "litert_http/conversation_manager.h"
#include "litert_http/engine_manager.h"

namespace litert_http {

class HttpServer {
 public:
  HttpServer(const EngineManager* engine_manager, EngineManager* mutable_engine,
             ConversationManager* conversation_manager);

  int Run();

 private:
  const EngineManager* engine_manager_;
  EngineManager* mutable_engine_;
  ConversationManager* conversation_manager_;
};

}  // namespace litert_http

#endif  // LITERT_HTTP_HTTP_SERVER_H_
