#ifndef LITERT_HTTP_PROMPT_RENDERER_H_
#define LITERT_HTTP_PROMPT_RENDERER_H_

#include <string>

#include "litert_http/types.h"

namespace litert_http {

class PromptRenderer {
 public:
  static std::string RenderGemmaPrompt(const SessionData& session);
};

}  // namespace litert_http

#endif  // LITERT_HTTP_PROMPT_RENDERER_H_
