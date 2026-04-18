# LiteRT-LM Runtime

This directory is a cleaned runtime bundle built from the already-compiled
artifacts under `/home/zeyaoz/LiteRT-LM`.

## Layout

- `bin/`: primary C++ executables
- `tools/`: schema and packaging tools
- `lib/`: Linux ARM64 runtime `.so` libraries required for GPU execution
- `examples/jvm/`: Kotlin/JVM example jars
- `models/`: model entry points used by wrapper scripts
- `scripts/`: environment setup and helper launchers
- `var/`: cache, logs, sockets, temp files

## Main Commands

Run the interactive multi-turn CLI on GPU:

```bash
./bin/run_litert_lm_advanced_main_gpu
```

Run the basic single-turn CLI on GPU:

```bash
./bin/run_litert_lm_main_gpu
```

Override the default model or pass extra flags:

```bash
./bin/run_litert_lm_advanced_main_gpu \
  --model_path=/abs/path/to/model.litertlm \
  --backend=gpu \
  --multi_turns=true
```

## Notes

- The wrapper scripts source `scripts/env.sh`, which adds `lib/` to
  `LD_LIBRARY_PATH`.
- `models/gemma-4-E2B-it/model.litertlm` is expected to exist. In this bundle,
  it is typically a symlink to the original model file.
- The JVM example jars are included for completeness, but the example source
  code currently defaults to CPU backend unless you modify the upstream example
  code and rebuild.

## Litert-Server Suggestions

- Treat this directory as a runtime bundle, not your main development repo.
- Build the server in a separate project and invoke the LiteRT-LM C++ API
  directly rather than shelling out per request.
- Keep per-conversation state in memory and map `conversation_id` to
  `Conversation`.
- Put logs in `var/log/`, sockets or pid files in `var/run/`, and caches in
  `var/cache/`.
- Start with one resident process, one model, and low concurrency on Jetson.
