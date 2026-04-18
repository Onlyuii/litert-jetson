#ifndef LITERT_HTTP_INFERENCE_BACKEND_H_
#define LITERT_HTTP_INFERENCE_BACKEND_H_

#include <memory>
#include <string>

#include "litert_http/config.h"
#include "litert_http/types.h"

namespace litert_http {

class InferenceBackend {
 public:
  virtual ~InferenceBackend() = default;
  virtual InferenceResult Run(const ServiceConfig& config,
                              const ModelSpec& model,
                              const TurnInput& turn,
                              const ChunkCallback& on_chunk) = 0;
};

class LitertCapiBackend final : public InferenceBackend {
 public:
  LitertCapiBackend();
  ~LitertCapiBackend() override;

  InferenceResult Run(const ServiceConfig& config, const ModelSpec& model,
                      const TurnInput& turn,
                      const ChunkCallback& on_chunk) override;

 private:
  struct Impl;
  std::unique_ptr<Impl> impl_;
};

std::unique_ptr<InferenceBackend> CreateDefaultBackend();

}  // namespace litert_http

#endif  // LITERT_HTTP_INFERENCE_BACKEND_H_
