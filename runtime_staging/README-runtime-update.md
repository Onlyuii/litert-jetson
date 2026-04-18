## Entry Point Note

The files under `bin/` are user-facing launchers. They source `scripts/env.sh`
before executing the real Bazel-built binaries under `libexec/`.

This means you can run:

```bash
./bin/litert_lm_advanced_main --model_path=/abs/path/model.litertlm --backend=gpu
```

without manually exporting `LD_LIBRARY_PATH`.
