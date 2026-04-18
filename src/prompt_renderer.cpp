#include "litert_http/prompt_renderer.h"

#include <sstream>

namespace litert_http {
namespace {

std::string MapRoleForGemma(const std::string& role) {
  if (role == "assistant") {
    return "model";
  }
  return role;
}

}  // namespace

std::string PromptRenderer::RenderGemmaPrompt(const SessionData& session) {
  std::ostringstream prompt;
  std::string system_prompt = session.system_prompt;
  if (session.mode == ChatMode::kThinking) {
    system_prompt = "<|think|>" + system_prompt;
  }

  if (!system_prompt.empty()) {
    prompt << "<ctrl99>system\n" << system_prompt << "<ctrl100>\n";
  }

  for (const auto& message : session.messages) {
    prompt << "<ctrl99>" << MapRoleForGemma(message.role) << "\n"
           << message.content << "<ctrl100>\n";
  }

  prompt << "<ctrl99>model\n";
  return prompt.str();
}

}  // namespace litert_http
