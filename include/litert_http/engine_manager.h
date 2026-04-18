#ifndef LITERT_HTTP_ENGINE_MANAGER_H_
#define LITERT_HTTP_ENGINE_MANAGER_H_

#include <atomic>
#include <memory>
#include <mutex>
#include <optional>
#include <string>
#include <vector>

#include "litert_http/config.h"
#include "litert_http/inference_backend.h"
#include "litert_http/types.h"

namespace litert_http {

class EngineManager {
 public:
  EngineManager(ServiceConfig config, std::unique_ptr<InferenceBackend> backend);

  const ServiceConfig& config() const { return config_; }

  HealthSnapshot GetHealth() const;
  std::vector<ModelSpec> ListModels() const;
  std::optional<ModelSpec> FindModel(const std::string& model_id) const;

  InferenceResult RunInference(const ModelSpec& model, const TurnInput& turn,
                               const ChunkCallback& on_chunk);

 private:
  ServiceConfig config_;
  std::unique_ptr<InferenceBackend> backend_;
  std::vector<ModelSpec> models_;

  mutable std::mutex status_mutex_;
  std::mutex inference_mutex_;
  std::string current_model_;
  std::string current_base_model_;
  std::string state_ = "cold";
  std::string last_error_;
  std::atomic<int> waiting_requests_{0};
  std::atomic<int> active_requests_{0};
};

}  // namespace litert_http

#endif  // LITERT_HTTP_ENGINE_MANAGER_H_
