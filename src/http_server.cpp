#include "litert_http/http_server.h"

#include <arpa/inet.h>
#include <netdb.h>
#include <sys/socket.h>
#include <sys/types.h>
#include <unistd.h>

#include <atomic>
#include <cerrno>
#include <cstring>
#include <iostream>
#include <optional>
#include <sstream>
#include <string>
#include <thread>
#include <unordered_map>
#include <vector>

#include <json/json.h>

#include "litert_http/utils.h"

namespace litert_http {
namespace {

struct HttpRequest {
  std::string method;
  std::string target;
  std::unordered_map<std::string, std::string> headers;
  std::string body;
};

struct HttpResponse {
  int status = 200;
  std::string content_type = "application/json";
  std::string body;
  std::vector<std::pair<std::string, std::string>> headers;
};

struct ParsedMessages {
  std::vector<ChatMessage> non_system_messages;
  std::string system_prompt;
  bool has_explicit_system_prompt = false;
};

enum class StreamFormat {
  kOpenAiSse,
  kPlainText,
};

std::string StatusText(int status) {
  switch (status) {
    case 200:
      return "OK";
    case 400:
      return "Bad Request";
    case 401:
      return "Unauthorized";
    case 404:
      return "Not Found";
    case 405:
      return "Method Not Allowed";
    case 409:
      return "Conflict";
    case 500:
      return "Internal Server Error";
    case 503:
      return "Service Unavailable";
    default:
      return "Error";
  }
}

bool WriteAll(int fd, const std::string& data) {
  size_t written = 0;
  while (written < data.size()) {
    const ssize_t sent =
        send(fd, data.data() + written, data.size() - written, MSG_NOSIGNAL);
    if (sent < 0) {
      if (errno == EINTR) {
        continue;
      }
      return false;
    }
    written += static_cast<size_t>(sent);
  }
  return true;
}

bool SendResponse(int fd, const HttpResponse& response) {
  std::ostringstream wire;
  wire << "HTTP/1.1 " << response.status << ' ' << StatusText(response.status)
       << "\r\n";
  wire << "Content-Type: " << response.content_type << "\r\n";
  wire << "Content-Length: " << response.body.size() << "\r\n";
  wire << "Connection: close\r\n";
  for (const auto& header : response.headers) {
    wire << header.first << ": " << header.second << "\r\n";
  }
  wire << "\r\n";
  wire << response.body;
  return WriteAll(fd, wire.str());
}

HttpResponse MakeJsonResponse(int status, const Json::Value& body) {
  HttpResponse response;
  response.status = status;
  response.content_type = "application/json";
  response.body = JsonToString(body);
  return response;
}

Json::Value MakeErrorJson(const std::string& message, const std::string& type,
                          int code) {
  Json::Value root(Json::objectValue);
  Json::Value error(Json::objectValue);
  error["message"] = message;
  error["type"] = type;
  error["code"] = code;
  root["error"] = error;
  return root;
}

std::optional<HttpRequest> ReadRequest(int fd, std::string* error) {
  std::string data;
  std::array<char, 4096> buffer{};

  while (data.find("\r\n\r\n") == std::string::npos) {
    const ssize_t read_bytes = recv(fd, buffer.data(), buffer.size(), 0);
    if (read_bytes < 0) {
      if (errno == EINTR) {
        continue;
      }
      if (error != nullptr) {
        *error = "Failed to read request.";
      }
      return std::nullopt;
    }
    if (read_bytes == 0) {
      if (error != nullptr) {
        *error = "Connection closed before headers completed.";
      }
      return std::nullopt;
    }
    data.append(buffer.data(), static_cast<size_t>(read_bytes));
    if (data.size() > 16 * 1024 * 1024) {
      if (error != nullptr) {
        *error = "Request too large.";
      }
      return std::nullopt;
    }
  }

  const size_t header_end = data.find("\r\n\r\n");
  std::string header_blob = data.substr(0, header_end);
  std::string body = data.substr(header_end + 4);
  std::istringstream header_stream(header_blob);
  std::string request_line;
  if (!std::getline(header_stream, request_line)) {
    if (error != nullptr) {
      *error = "Malformed request line.";
    }
    return std::nullopt;
  }
  if (!request_line.empty() && request_line.back() == '\r') {
    request_line.pop_back();
  }
  std::istringstream request_line_stream(request_line);
  HttpRequest request;
  std::string http_version;
  request_line_stream >> request.method >> request.target >> http_version;
  if (request.method.empty() || request.target.empty()) {
    if (error != nullptr) {
      *error = "Invalid HTTP request line.";
    }
    return std::nullopt;
  }

  std::string line;
  while (std::getline(header_stream, line)) {
    if (!line.empty() && line.back() == '\r') {
      line.pop_back();
    }
    if (line.empty()) {
      continue;
    }
    const size_t colon = line.find(':');
    if (colon == std::string::npos) {
      continue;
    }
    const std::string key = ToLower(Trim(line.substr(0, colon)));
    const std::string value = Trim(line.substr(colon + 1));
    request.headers[key] = value;
  }

  size_t content_length = 0;
  if (request.headers.contains("content-length")) {
    try {
      content_length =
          static_cast<size_t>(std::stoul(request.headers["content-length"]));
    } catch (...) {
      if (error != nullptr) {
        *error = "Invalid Content-Length.";
      }
      return std::nullopt;
    }
  }

  while (body.size() < content_length) {
    const ssize_t read_bytes = recv(fd, buffer.data(), buffer.size(), 0);
    if (read_bytes < 0) {
      if (errno == EINTR) {
        continue;
      }
      if (error != nullptr) {
        *error = "Failed to read request body.";
      }
      return std::nullopt;
    }
    if (read_bytes == 0) {
      if (error != nullptr) {
        *error = "Connection closed before body completed.";
      }
      return std::nullopt;
    }
    body.append(buffer.data(), static_cast<size_t>(read_bytes));
  }

  request.body = body.substr(0, content_length);
  return request;
}

std::string PathOnly(const std::string& target) {
  const size_t question = target.find('?');
  return question == std::string::npos ? target : target.substr(0, question);
}

std::string ExtractTextContent(const Json::Value& content, std::string* error) {
  if (content.isString()) {
    return content.asString();
  }
  if (content.isObject() && content.isMember("text") &&
      content["text"].isString()) {
    return content["text"].asString();
  }
  if (content.isArray()) {
    std::vector<std::string> parts;
    for (const auto& part : content) {
      if (!part.isObject()) {
        if (error != nullptr) {
          *error = "Message content array must contain objects.";
        }
        return {};
      }
      const std::string type =
          part.isMember("type") && part["type"].isString() ? part["type"].asString()
                                                            : "";
      if (type != "text" || !part.isMember("text") || !part["text"].isString()) {
        if (error != nullptr) {
          *error = "Only text content is supported in this service.";
        }
        return {};
      }
      parts.push_back(part["text"].asString());
    }
    return JoinStrings(parts, "");
  }
  if (error != nullptr) {
    *error = "Unsupported message content format.";
  }
  return {};
}

std::optional<ParsedMessages> ParseMessages(const Json::Value& root,
                                            const std::string& default_system,
                                            std::string* error) {
  if (!root.isArray()) {
    if (error != nullptr) {
      *error = "`messages` must be an array.";
    }
    return std::nullopt;
  }
  ParsedMessages parsed;
  std::vector<std::string> system_parts;
  for (const auto& item : root) {
    if (!item.isObject() || !item.isMember("role") || !item["role"].isString() ||
        !item.isMember("content")) {
      if (error != nullptr) {
        *error = "Each message must contain string `role` and `content`.";
      }
      return std::nullopt;
    }
    const std::string role = item["role"].asString();
    std::string content_error;
    const std::string text = ExtractTextContent(item["content"], &content_error);
    if (!content_error.empty()) {
      if (error != nullptr) {
        *error = content_error;
      }
      return std::nullopt;
    }
    if (role == "system") {
      system_parts.push_back(text);
      parsed.has_explicit_system_prompt = true;
      continue;
    }
    if (role != "user" && role != "assistant") {
      if (error != nullptr) {
        *error = "Only system, user, and assistant roles are supported.";
      }
      return std::nullopt;
    }
    parsed.non_system_messages.push_back(ChatMessage{.role = role, .content = text});
  }
  parsed.system_prompt =
      system_parts.empty() ? default_system : JoinStrings(system_parts, "\n\n");
  return parsed;
}

bool IsAuthorized(const HttpRequest& request, const std::string& api_key) {
  if (api_key.empty()) {
    return true;
  }
  auto it = request.headers.find("authorization");
  if (it == request.headers.end()) {
    return false;
  }
  const std::string prefix = "Bearer ";
  return it->second.rfind(prefix, 0) == 0 && it->second.substr(prefix.size()) == api_key;
}

std::string MakeId(const std::string& prefix) {
  static std::atomic<uint64_t> counter{0};
  std::ostringstream stream;
  stream << prefix << '-' << NowUnixSeconds() << '-' << counter.fetch_add(1);
  return stream.str();
}

Json::Value BuildModelsResponse(const std::vector<ModelSpec>& models) {
  Json::Value root(Json::objectValue);
  root["object"] = "list";
  Json::Value data(Json::arrayValue);
  for (const auto& model : models) {
    Json::Value item(Json::objectValue);
    item["id"] = model.id;
    item["object"] = "model";
    item["owned_by"] = "local";
    data.append(item);
  }
  root["data"] = data;
  return root;
}

Json::Value BuildHealthResponse(const HealthSnapshot& snapshot) {
  Json::Value root(Json::objectValue);
  root["status"] = snapshot.status;
  root["backend"] = snapshot.backend;
  root["model_state"] = snapshot.model_state;
  root["current_model"] = snapshot.current_model;
  root["queue_depth"] = snapshot.queue_depth;
  root["active_requests"] = snapshot.active_requests;
  root["model_loaded"] = snapshot.model_loaded;
  root["last_error"] = snapshot.last_error;
  return root;
}

Json::Value BuildCompletionResponse(const std::string& id, int64_t created,
                                    const std::string& model,
                                    const std::string& content) {
  Json::Value root(Json::objectValue);
  root["id"] = id;
  root["object"] = "chat.completion";
  root["created"] = static_cast<Json::Int64>(created);
  root["model"] = model;
  Json::Value message(Json::objectValue);
  message["role"] = "assistant";
  message["content"] = content;
  Json::Value choice(Json::objectValue);
  choice["index"] = 0;
  choice["message"] = message;
  choice["finish_reason"] = "stop";
  Json::Value choices(Json::arrayValue);
  choices.append(choice);
  root["choices"] = choices;
  return root;
}

Json::Value BuildChunkResponse(const std::string& id, int64_t created,
                               const std::string& model,
                               const Json::Value& delta,
                               const Json::Value& finish_reason) {
  Json::Value root(Json::objectValue);
  root["id"] = id;
  root["object"] = "chat.completion.chunk";
  root["created"] = static_cast<Json::Int64>(created);
  root["model"] = model;
  Json::Value choice(Json::objectValue);
  choice["index"] = 0;
  choice["delta"] = delta;
  choice["finish_reason"] = finish_reason;
  Json::Value choices(Json::arrayValue);
  choices.append(choice);
  root["choices"] = choices;
  return root;
}

bool SendSseData(int fd, const Json::Value& value) {
  return WriteAll(fd, "data: " + JsonToString(value) + "\n\n");
}

bool SendSseDone(int fd) { return WriteAll(fd, "data: [DONE]\n\n"); }

bool SendStreamingHeaders(
    int fd, const std::string& content_type,
    const std::vector<std::pair<std::string, std::string>>& headers = {}) {
  std::ostringstream wire;
  wire << "HTTP/1.1 200 OK\r\n";
  wire << "Content-Type: " << content_type << "\r\n";
  wire << "Cache-Control: no-cache\r\n";
  wire << "Connection: close\r\n";
  wire << "X-Accel-Buffering: no\r\n";
  for (const auto& header : headers) {
    wire << header.first << ": " << header.second << "\r\n";
  }
  wire << "\r\n";
  return WriteAll(fd, wire.str());
}

StreamFormat DetermineStreamFormat(const HttpRequest& request,
                                   const Json::Value& body) {
  if (body.isMember("stream_format") && body["stream_format"].isString()) {
    const std::string requested = ToLower(body["stream_format"].asString());
    if (requested == "text" || requested == "plain" || requested == "raw") {
      return StreamFormat::kPlainText;
    }
  }
  if (body.isMember("output_format") && body["output_format"].isString()) {
    const std::string requested = ToLower(body["output_format"].asString());
    if (requested == "text" || requested == "plain" || requested == "raw") {
      return StreamFormat::kPlainText;
    }
  }
  const auto accept_it = request.headers.find("accept");
  if (accept_it != request.headers.end() &&
      accept_it->second.find("text/plain") != std::string::npos) {
    return StreamFormat::kPlainText;
  }
  return StreamFormat::kOpenAiSse;
}

std::optional<std::string> ExtractBodySessionId(const Json::Value& body,
                                                std::string* error) {
  std::optional<std::string> session_id;
  if (body.isMember("session_id")) {
    if (!body["session_id"].isString()) {
      if (error != nullptr) {
        *error = "`session_id` must be a string.";
      }
      return std::nullopt;
    }
    session_id = body["session_id"].asString();
  }

  if (body.isMember("metadata")) {
    if (!body["metadata"].isObject()) {
      if (error != nullptr) {
        *error = "`metadata` must be an object when provided.";
      }
      return std::nullopt;
    }
    const Json::Value& metadata = body["metadata"];
    if (metadata.isMember("session_id")) {
      if (!metadata["session_id"].isString()) {
        if (error != nullptr) {
          *error = "`metadata.session_id` must be a string.";
        }
        return std::nullopt;
      }
      const std::string metadata_session_id = metadata["session_id"].asString();
      if (session_id.has_value() && *session_id != metadata_session_id) {
        if (error != nullptr) {
          *error =
              "`session_id` and `metadata.session_id` must match when both are set.";
        }
        return std::nullopt;
      }
      session_id = metadata_session_id;
    }
  }

  return session_id;
}

std::optional<std::string> ResolveSessionId(const HttpRequest& request,
                                            const Json::Value& body,
                                            std::string* error) {
  std::optional<std::string> body_session_id = ExtractBodySessionId(body, error);
  if (!body_session_id.has_value() && error != nullptr && !error->empty()) {
    return std::nullopt;
  }

  const auto session_header = request.headers.find("x-session-id");
  if (session_header == request.headers.end()) {
    return body_session_id;
  }

  if (body_session_id.has_value() && *body_session_id != session_header->second) {
    if (error != nullptr) {
      *error =
          "`X-Session-Id` and body `session_id` must match when both are set.";
    }
    return std::nullopt;
  }
  return session_header->second;
}

std::optional<TurnInput> BuildTurnInput(const SessionData& session,
                                        std::string* error) {
  if (session.messages.empty() || session.messages.back().role != "user") {
    if (error != nullptr) {
      *error = "Each turn must end with exactly one user message.";
    }
    return std::nullopt;
  }

  TurnInput turn;
  turn.session_id = session.session_id;
  turn.system_prompt = session.system_prompt;
  turn.mode = session.mode;
  turn.stateful = !session.session_id.empty();
  turn.user_message = session.messages.back();
  turn.history_messages.assign(session.messages.begin(), session.messages.end() - 1);
  return turn;
}

}  // namespace

HttpServer::HttpServer(const EngineManager* engine_manager,
                       EngineManager* mutable_engine,
                       ConversationManager* conversation_manager)
    : engine_manager_(engine_manager),
      mutable_engine_(mutable_engine),
      conversation_manager_(conversation_manager) {}

int HttpServer::Run() {
  const ServiceConfig& config = engine_manager_->config();
  addrinfo hints{};
  hints.ai_family = AF_INET;
  hints.ai_socktype = SOCK_STREAM;
  hints.ai_flags = AI_PASSIVE;

  addrinfo* result = nullptr;
  const std::string port = std::to_string(config.port);
  const int rc = getaddrinfo(config.bind_host.c_str(), port.c_str(), &hints, &result);
  if (rc != 0) {
    std::cerr << "getaddrinfo failed: " << gai_strerror(rc) << '\n';
    return 1;
  }

  int server_fd = -1;
  std::string bind_error = "unknown error";
  for (addrinfo* rp = result; rp != nullptr; rp = rp->ai_next) {
    server_fd = socket(rp->ai_family, rp->ai_socktype, rp->ai_protocol);
    if (server_fd == -1) {
      bind_error = std::strerror(errno);
      continue;
    }
    int yes = 1;
    setsockopt(server_fd, SOL_SOCKET, SO_REUSEADDR, &yes, sizeof(yes));
    if (bind(server_fd, rp->ai_addr, rp->ai_addrlen) == 0) {
      break;
    }
    bind_error = std::strerror(errno);
    close(server_fd);
    server_fd = -1;
  }
  freeaddrinfo(result);

  if (server_fd == -1) {
    std::cerr << "Failed to bind to " << config.bind_host << ':' << config.port
              << ": " << bind_error << '\n';
    return 1;
  }

  if (listen(server_fd, 64) != 0) {
    std::cerr << "listen failed: " << std::strerror(errno) << '\n';
    close(server_fd);
    return 1;
  }

  std::cout << "LiteRT HTTP service listening on " << config.bind_host << ':'
            << config.port << '\n';

  while (true) {
    sockaddr_storage client_addr{};
    socklen_t client_len = sizeof(client_addr);
    const int client_fd =
        accept(server_fd, reinterpret_cast<sockaddr*>(&client_addr), &client_len);
    if (client_fd < 0) {
      if (errno == EINTR) {
        continue;
      }
      std::cerr << "accept failed: " << std::strerror(errno) << '\n';
      continue;
    }

    std::thread([this, client_fd]() {
      std::string request_error;
      auto request = ReadRequest(client_fd, &request_error);
      if (!request.has_value()) {
        if (!request_error.empty()) {
          SendResponse(client_fd,
                       MakeJsonResponse(400, MakeErrorJson(request_error,
                                                           "invalid_request_error",
                                                           400)));
        }
        close(client_fd);
        return;
      }

      const std::string path = PathOnly(request->target);
      const bool needs_auth = path.rfind("/v1/", 0) == 0;
      if (needs_auth && !IsAuthorized(*request, engine_manager_->config().api_key)) {
        SendResponse(client_fd, MakeJsonResponse(
                                    401, MakeErrorJson("Invalid bearer token.",
                                                       "authentication_error",
                                                       401)));
        close(client_fd);
        return;
      }

      if (request->method == "GET" && path == "/health") {
        SendResponse(client_fd,
                     MakeJsonResponse(200,
                                      BuildHealthResponse(engine_manager_->GetHealth())));
        close(client_fd);
        return;
      }

      if (request->method == "GET" && path == "/v1/models") {
        SendResponse(client_fd,
                     MakeJsonResponse(200,
                                      BuildModelsResponse(engine_manager_->ListModels())));
        close(client_fd);
        return;
      }

      if (request->method == "POST" && path == "/v1/chat/completions") {
        std::string json_error;
        auto body = ParseJson(request->body, &json_error);
        if (!body.has_value() || !body->isObject()) {
          SendResponse(client_fd, MakeJsonResponse(
                                      400, MakeErrorJson(json_error.empty()
                                                             ? "Invalid JSON body."
                                                             : json_error,
                                                         "invalid_request_error",
                                                         400)));
          close(client_fd);
          return;
        }

        if (!body->isMember("model") || !(*body)["model"].isString()) {
          SendResponse(client_fd, MakeJsonResponse(
                                      400, MakeErrorJson("`model` must be a string.",
                                                         "invalid_request_error",
                                                         400)));
          close(client_fd);
          return;
        }

        if (!body->isMember("messages")) {
          SendResponse(client_fd, MakeJsonResponse(
                                      400,
                                      MakeErrorJson("`messages` is required.",
                                                    "invalid_request_error", 400)));
          close(client_fd);
          return;
        }

        const std::string model_id = (*body)["model"].asString();
        auto model = mutable_engine_->FindModel(model_id);
        if (!model.has_value()) {
          SendResponse(client_fd, MakeJsonResponse(
                                      400,
                                      MakeErrorJson("Unknown model: " + model_id,
                                                    "invalid_request_error", 400)));
          close(client_fd);
          return;
        }

        bool stream = false;
        if (body->isMember("stream")) {
          if (!(*body)["stream"].isBool()) {
            SendResponse(client_fd, MakeJsonResponse(
                                        400,
                                        MakeErrorJson("`stream` must be a boolean.",
                                                      "invalid_request_error", 400)));
            close(client_fd);
            return;
          }
          stream = (*body)["stream"].asBool();
        }

        const StreamFormat stream_format = DetermineStreamFormat(*request, *body);

        std::string parse_error;
        auto parsed_messages = ParseMessages((*body)["messages"],
                                             engine_manager_->config().default_system_prompt,
                                             &parse_error);
        if (!parsed_messages.has_value()) {
          SendResponse(client_fd, MakeJsonResponse(
                                      400, MakeErrorJson(parse_error,
                                                         "invalid_request_error",
                                                         400)));
          close(client_fd);
          return;
        }

        std::string session_error;
        auto resolved_session_id = ResolveSessionId(*request, *body, &session_error);
        if (!resolved_session_id.has_value() && !session_error.empty()) {
          SendResponse(client_fd, MakeJsonResponse(
                                      400, MakeErrorJson(session_error,
                                                         "invalid_request_error",
                                                         400)));
          close(client_fd);
          return;
        }
        const std::string session_id =
            resolved_session_id.has_value() ? *resolved_session_id : "";

        SessionData session;
        if (!session_id.empty()) {
          TurnPreparation preparation = conversation_manager_->PrepareTurn(
              session_id, *model, parsed_messages->system_prompt,
              parsed_messages->non_system_messages,
              parsed_messages->has_explicit_system_prompt);
          if (!preparation.ok) {
            SendResponse(client_fd, MakeJsonResponse(
                                        400,
                                        MakeErrorJson(preparation.error,
                                                      "invalid_request_error",
                                                      400)));
            close(client_fd);
            return;
          }
          session = std::move(preparation.session);
        } else {
          if (parsed_messages->non_system_messages.empty() ||
              parsed_messages->non_system_messages.back().role != "user") {
            SendResponse(client_fd, MakeJsonResponse(
                                        400,
                                        MakeErrorJson(
                                            "Stateless requests must end with a user "
                                            "message.",
                                            "invalid_request_error", 400)));
            close(client_fd);
            return;
          }
          session.logical_model_id = model->id;
          session.base_model_id = model->base_model_id;
          session.mode = model->mode;
          session.system_prompt = parsed_messages->system_prompt;
          session.system_prompt_hash = HashString(parsed_messages->system_prompt);
          session.last_access_at = NowUnixSeconds();
          session.messages = parsed_messages->non_system_messages;
        }

        std::string turn_error;
        auto turn = BuildTurnInput(session, &turn_error);
        if (!turn.has_value()) {
          SendResponse(client_fd, MakeJsonResponse(
                                      400, MakeErrorJson(turn_error,
                                                         "invalid_request_error",
                                                         400)));
          close(client_fd);
          return;
        }
        const std::string completion_id = MakeId("chatcmpl");
        const int64_t created = NowUnixSeconds();
        const std::vector<std::pair<std::string, std::string>> stream_headers =
            session_id.empty()
                ? std::vector<std::pair<std::string, std::string>>{}
                : std::vector<std::pair<std::string, std::string>>{
                      {"X-Session-Id", session_id}};

        if (stream) {
          if (stream_format == StreamFormat::kOpenAiSse) {
            if (!SendStreamingHeaders(client_fd,
                                      "text/event-stream; charset=utf-8",
                                      stream_headers)) {
              close(client_fd);
              return;
            }
            Json::Value role_delta(Json::objectValue);
            role_delta["role"] = "assistant";
            if (!SendSseData(client_fd, BuildChunkResponse(completion_id, created,
                                                           model->id, role_delta,
                                                           Json::nullValue))) {
              close(client_fd);
              return;
            }
          } else {
            if (!SendStreamingHeaders(client_fd, "text/plain; charset=utf-8",
                                      stream_headers)) {
              close(client_fd);
              return;
            }
          }

          InferenceResult inference = mutable_engine_->RunInference(
              *model, *turn, [&](const std::string& chunk) {
                if (chunk.empty()) {
                  return true;
                }
                if (stream_format == StreamFormat::kPlainText) {
                  return WriteAll(client_fd, chunk);
                }
                Json::Value delta(Json::objectValue);
                delta["content"] = chunk;
                return SendSseData(client_fd,
                                   BuildChunkResponse(completion_id, created,
                                                      model->id, delta,
                                                      Json::nullValue));
              });

          if (!inference.ok) {
            if (stream_format == StreamFormat::kPlainText) {
              WriteAll(client_fd, "\n[ERROR] " + inference.error + "\n");
            } else {
              SendSseData(client_fd,
                          MakeErrorJson(inference.error, "server_error", 500));
            }
            close(client_fd);
            return;
          }

          if (!session_id.empty()) {
            std::string commit_error;
            if (!conversation_manager_->CommitTurn(session_id, session,
                                                   inference.text, &commit_error)) {
              if (stream_format == StreamFormat::kPlainText) {
                WriteAll(client_fd, "\n[ERROR] " + commit_error + "\n");
              } else {
                SendSseData(client_fd,
                            MakeErrorJson(commit_error, "server_error", 500));
              }
              close(client_fd);
              return;
            }
          }

          if (stream_format == StreamFormat::kOpenAiSse) {
            Json::Value final_delta(Json::objectValue);
            SendSseData(client_fd, BuildChunkResponse(completion_id, created,
                                                      model->id, final_delta,
                                                      Json::Value("stop")));
            SendSseDone(client_fd);
          }
          close(client_fd);
          return;
        }

        InferenceResult inference =
            mutable_engine_->RunInference(*model, *turn, nullptr);
        if (!inference.ok) {
          SendResponse(client_fd, MakeJsonResponse(
                                      500,
                                      MakeErrorJson(inference.error, "server_error",
                                                    500)));
          close(client_fd);
          return;
        }

        if (!session_id.empty()) {
          std::string commit_error;
          if (!conversation_manager_->CommitTurn(session_id, session,
                                                 inference.text, &commit_error)) {
            SendResponse(client_fd, MakeJsonResponse(
                                        500,
                                        MakeErrorJson(commit_error, "server_error",
                                                      500)));
            close(client_fd);
            return;
          }
        }

        Json::Value response =
            BuildCompletionResponse(completion_id, created, model->id, inference.text);
        if (!session_id.empty()) {
          response["session_id"] = session_id;
        }
        HttpResponse http_response = MakeJsonResponse(200, response);
        if (!session_id.empty()) {
          http_response.headers.push_back({"X-Session-Id", session_id});
        }
        SendResponse(client_fd, http_response);
        close(client_fd);
        return;
      }

      SendResponse(client_fd, MakeJsonResponse(
                                  404,
                                  MakeErrorJson("Route not found.",
                                                "invalid_request_error", 404)));
      close(client_fd);
    }).detach();
  }
}

}  // namespace litert_http
