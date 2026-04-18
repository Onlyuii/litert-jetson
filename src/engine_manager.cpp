#include "litert_http/engine_manager.h"

#include <filesystem>

namespace litert_http {

EngineManager::EngineManager(ServiceConfig config,
                             std::unique_ptr<InferenceBackend> backend)
    : config_(std::move(config)), backend_(std::move(backend)) {
  models_ = {
      ModelSpec{
          .id = "gemma-4-2b-chat",
          .base_model_id = "gemma-4-2b",
          .model_path = config_.model_2b_path,
          .mode = ChatMode::kChat,
      },
      ModelSpec{
          .id = "gemma-4-2b-thinking",
          .base_model_id = "gemma-4-2b",
          .model_path = config_.model_2b_path,
          .mode = ChatMode::kThinking,
      },
      ModelSpec{
          .id = "gemma-4-4b-chat",
          .base_model_id = "gemma-4-4b",
          .model_path = config_.model_4b_path,
          .mode = ChatMode::kChat,
      },
      ModelSpec{
          .id = "gemma-4-4b-thinking",
          .base_model_id = "gemma-4-4b",
          .model_path = config_.model_4b_path,
          .mode = ChatMode::kThinking,
      },
  };
}

HealthSnapshot EngineManager::GetHealth() const {
  std::lock_guard<std::mutex> lock(status_mutex_);
  HealthSnapshot snapshot;
  snapshot.backend = config_.backend;
  snapshot.model_state = state_;
  snapshot.current_model = current_model_;
  snapshot.last_error = last_error_;
  snapshot.queue_depth = waiting_requests_.load();
  snapshot.active_requests = active_requests_.load();
  snapshot.model_loaded = !current_base_model_.empty() && state_ == "ready";
  return snapshot;
}

std::vector<ModelSpec> EngineManager::ListModels() const { return models_; }

std::optional<ModelSpec> EngineManager::FindModel(const std::string& model_id) const {
  for (const auto& model : models_) {
    if (model.id == model_id) {
      return model;
    }
  }
  return std::nullopt;
}

InferenceResult EngineManager::RunInference(const ModelSpec& model,
                                            const TurnInput& turn,
                                            const ChunkCallback& on_chunk) {
  waiting_requests_.fetch_add(1);
  std::unique_lock<std::mutex> infer_lock(inference_mutex_);
  waiting_requests_.fetch_sub(1);
  active_requests_.fetch_add(1);

  {
    std::lock_guard<std::mutex> lock(status_mutex_);
    if (current_base_model_.empty()) {
      state_ = "loading";
    } else if (current_base_model_ != model.base_model_id) {
      state_ = "switching";
    }
    current_model_ = model.base_model_id;
    last_error_.clear();
  }

  InferenceResult result = backend_->Run(config_, model, turn, on_chunk);

  {
    std::lock_guard<std::mutex> lock(status_mutex_);
    if (result.ok) {
      current_base_model_ = model.base_model_id;
      current_model_ = model.base_model_id;
      state_ = "ready";
      last_error_.clear();
    } else {
      state_ = "error";
      last_error_ = result.error;
    }
  }
  active_requests_.fetch_sub(1);
  return result;
}

}  // namespace litert_http
