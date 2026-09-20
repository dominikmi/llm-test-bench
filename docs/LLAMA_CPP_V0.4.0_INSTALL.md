# Install llama.cpp v0.4.0 on the llama.cpp host

This runbook installs stable `llama.cpp` v0.4.0 alongside the existing b10549 build, validates router mode on port 8081, and deploys it through a reversible systemd override. It does not delete or modify the b10549 build.

Complete `LLAMA_CPP_V0.4.0_VULKAN_VALIDATION.md` before the production deployment section.

## Known environment

- Host: llama.cpp server
- OS: Ubuntu 24.04
- CPU: AMD Ryzen 9 PRO 8945HS
- GPU: AMD Radeon 780M, RADV/Phoenix, Vulkan
- Existing binary: `/home/user/llama-updates/llama.cpp-b10549/build/bin/llama-server`
- New source: `/home/user/llama-updates/llama.cpp-v0.4.0`
- Presets: `/home/user/llama-models/presets.ini`
- Service: `/etc/systemd/system/llama-server.service`
- Production port: `8080`
- Validation port: `8081`
- Vulkan ICD: `/usr/share/vulkan/icd.d/radeon_icd.json`
- Upstream tag: `v0.4.0`
- Expected dereferenced tag commit: `5266f24da75dc449bd56cbed7addb9c8e4a6a73e`

## 1. Set shell variables

Run every command in this document on the llama.cpp host as `user` unless it starts with `sudo`.

```bash
set -euo pipefail

RELEASE="v0.4.0"
EXPECTED_COMMIT="5266f24da75dc449bd56cbed7addb9c8e4a6a73e"
OLD_ROOT="/home/user/llama-updates/llama.cpp-b10549"
NEW_ROOT="/home/user/llama-updates/llama.cpp-v0.4.0"
PRESETS="/home/user/llama-models/presets.ini"
VULKAN_ICD="/usr/share/vulkan/icd.d/radeon_icd.json"
NEW_SERVER="$NEW_ROOT/build/bin/llama-server"
```

## 2. Check required tools and packages

```bash
for command in git cmake make cc c++ glslc vulkaninfo curl python3; do
  command -v "$command" || {
    echo "Missing command: $command" >&2
    exit 1
  }
done

dpkg-query -W \
  git \
  cmake \
  build-essential \
  libopenblas-dev \
  libvulkan-dev \
  glslc \
  libssl-dev
```

If a package is missing, install only the missing dependencies:

```bash
sudo apt-get update
sudo apt-get install \
  git \
  cmake \
  build-essential \
  libopenblas-dev \
  libvulkan-dev \
  glslc \
  libssl-dev
```

## 3. Confirm disk space and the current service

```bash
df -h /home/user/llama-updates
systemctl --no-pager --full status llama-server.service
"$OLD_ROOT/build/bin/llama-server" --version
```

Do not stop the production service yet.

## 4. Clone and verify the release

Stop if `$NEW_ROOT` already exists. Do not overwrite or delete an existing candidate tree.

```bash
if [ -e "$NEW_ROOT" ]; then
  echo "Candidate path already exists: $NEW_ROOT" >&2
  exit 1
fi

git clone \
  --branch "$RELEASE" \
  --depth 1 \
  https://github.com/ggml-org/llama.cpp.git \
  "$NEW_ROOT"

ACTUAL_COMMIT="$(git -C "$NEW_ROOT" rev-parse HEAD)"
printf 'Expected commit: %s\nActual commit:   %s\n' \
  "$EXPECTED_COMMIT" \
  "$ACTUAL_COMMIT"

test "$ACTUAL_COMMIT" = "$EXPECTED_COMMIT"
test "$(git -C "$NEW_ROOT" describe --tags --exact-match)" = "$RELEASE"
test -z "$(git -C "$NEW_ROOT" status --short)"
```

## 5. Configure the production build

This reproduces the meaningful b10549 profile: Release, native CPU, OpenBLAS, Vulkan, shared libraries, server/router subprocess support, OpenSSL, tools, and embedded UI. Debug validation and tests remain disabled in the production build.

`LLAMA_BUILD_IS_DEV=OFF` ensures the binary identifies itself as release 0.4.0. `LLAMA_SUBPROCESS=ON` is required by router mode.

```bash
cmake \
  -S "$NEW_ROOT" \
  -B "$NEW_ROOT/build" \
  -DCMAKE_BUILD_TYPE=Release \
  -DBUILD_SHARED_LIBS=ON \
  -DGGML_NATIVE=ON \
  -DGGML_CPU=ON \
  -DGGML_CPU_REPACK=ON \
  -DGGML_OPENMP=ON \
  -DGGML_BLAS=ON \
  -DGGML_BLAS_VENDOR=OpenBLAS \
  -DGGML_VULKAN=ON \
  -DGGML_VULKAN_CHECK_RESULTS=OFF \
  -DGGML_VULKAN_DEBUG=OFF \
  -DGGML_VULKAN_MEMORY_DEBUG=OFF \
  -DGGML_VULKAN_SHADER_DEBUG_INFO=OFF \
  -DGGML_VULKAN_VALIDATE=OFF \
  -DGGML_VULKAN_RUN_TESTS=OFF \
  -DGGML_BUILD_TESTS=OFF \
  -DGGML_BUILD_EXAMPLES=OFF \
  -DLLAMA_BUILD_IS_DEV=OFF \
  -DLLAMA_BUILD_SERVER=ON \
  -DLLAMA_BUILD_TOOLS=ON \
  -DLLAMA_BUILD_TESTS=OFF \
  -DLLAMA_BUILD_EXAMPLES=OFF \
  -DLLAMA_BUILD_APP=ON \
  -DLLAMA_BUILD_UI=ON \
  -DLLAMA_USE_PREBUILT_UI=ON \
  -DLLAMA_OPENSSL=ON \
  -DLLAMA_SUBPROCESS=ON
```

Confirm the effective cache before compilation:

```bash
grep -E \
  '^(CMAKE_BUILD_TYPE|BUILD_SHARED_LIBS|GGML_NATIVE|GGML_CPU|GGML_CPU_REPACK|GGML_OPENMP|GGML_BLAS|GGML_BLAS_VENDOR|GGML_VULKAN|GGML_VULKAN_CHECK_RESULTS|GGML_VULKAN_VALIDATE|GGML_VULKAN_RUN_TESTS|LLAMA_BUILD_IS_DEV|LLAMA_BUILD_SERVER|LLAMA_BUILD_TOOLS|LLAMA_BUILD_TESTS|LLAMA_BUILD_UI|LLAMA_OPENSSL|LLAMA_SUBPROCESS)' \
  "$NEW_ROOT/build/CMakeCache.txt" |
sort
```

## 6. Compile

```bash
cmake --build "$NEW_ROOT/build" \
  --config Release \
  --parallel "$(nproc)"
```

## 7. Validate artifacts and shared libraries

```bash
test -x "$NEW_SERVER"
test -x "$NEW_ROOT/build/bin/llama-cli"
test -x "$NEW_ROOT/build/bin/llama-bench"

"$NEW_SERVER" --version

ldd "$NEW_SERVER" | tee /tmp/llama-v0.4.0-ldd.txt
if grep -q 'not found' /tmp/llama-v0.4.0-ldd.txt; then
  echo "A required shared library is missing" >&2
  exit 1
fi
```

The version output must identify `0.4.0` and the expected Git commit rather than `commit unknown`.

## 8. Verify required server options

```bash
"$NEW_SERVER" --help >/tmp/llama-v0.4.0-help.txt

for option in \
  models-preset \
  models-max \
  sleep-idle-seconds \
  flash-attn \
  cache-type-k \
  lazy-mode \
  n-cpu-ffn; do
  grep -q -- "--$option" /tmp/llama-v0.4.0-help.txt || {
    echo "Missing required option: --$option" >&2
    exit 1
  }
done
```

## 9. Validate router mode on port 8081

This test does not load a model and does not modify the production service.

```bash
TEST_LOG="/tmp/llama-server-v0.4.0-router.log"
TEST_MODELS="/tmp/llama-server-v0.4.0-models.json"

VK_ICD_FILENAMES="$VULKAN_ICD" \
"$NEW_SERVER" \
  --models-preset "$PRESETS" \
  --host 127.0.0.1 \
  --port 8081 \
  --models-max 1 \
  --sleep-idle-seconds -1 \
  >"$TEST_LOG" 2>&1 &
TEST_PID=$!

cleanup_test_server() {
  if kill -0 "$TEST_PID" 2>/dev/null; then
    kill "$TEST_PID"
    wait "$TEST_PID" || true
  fi
}
trap cleanup_test_server EXIT

READY=0
for attempt in $(seq 1 30); do
  if curl -fsS \
    --connect-timeout 2 \
    --max-time 5 \
    http://127.0.0.1:8081/v1/models \
    >"$TEST_MODELS"; then
    READY=1
    break
  fi

  if ! kill -0 "$TEST_PID" 2>/dev/null; then
    echo "Candidate router exited unexpectedly" >&2
    cat "$TEST_LOG"
    exit 1
  fi

  sleep 1
done

test "$READY" -eq 1

python3 - <<'PY'
import json
from pathlib import Path

payload = json.loads(
    Path("/tmp/llama-server-v0.4.0-models.json").read_text(encoding="utf-8")
)
models = {entry["id"] for entry in payload["data"]}
required = {
    "coder-gemma4-12b-coder:LATEST",
    "coder-gemma4-26b-qat:LATEST",
    "coder-laguna-xs-2.1:LATEST",
    "coder-qwen3-coder-next:LATEST",
    "coder-qwen3.6-35b-mtp:LATEST",
    "coder-qwen3.8-27b-gsq-rco:LATEST",
    "coder-whittle-moe-27b-a18b:LATEST",
}
missing = required - models
print(f"Discovered presets: {len(models)}")
if missing:
    raise SystemExit(f"Missing aliases: {sorted(missing)}")
PY

cleanup_test_server
trap - EXIT

cat "$TEST_LOG"
```

Do not deploy if the candidate reports a preset parsing error, exits unexpectedly, or omits required model aliases.

## 10. Complete Vulkan validation

Run every required test and decision gate in:

```text
LLAMA_CPP_V0.4.0_VULKAN_VALIDATION.md
```

Deploy only if that runbook concludes `DEPLOY`.

## 11. Confirm a maintenance window

Restarting the service terminates the active router and child model process. Ensure that no model benchmark or OpenCode task is active.

```bash
systemctl --no-pager --full status llama-server.service
ss -tnp | grep ':8080' || true
```

Stop here if a benchmark client has an established connection.

## 12. Back up the service configuration

```bash
TIMESTAMP="$(date -u +%Y%m%dT%H%M%SZ)"
SERVICE_BACKUP="/etc/systemd/system/llama-server.service.${TIMESTAMP}.bak"

sudo cp --preserve=all \
  /etc/systemd/system/llama-server.service \
  "$SERVICE_BACKUP"

printf 'Service backup: %s\n' "$SERVICE_BACKUP"
```

## 13. Deploy through a systemd override

The original service remains unchanged. The override replaces only `ExecStart`.

```bash
sudo install -d \
  -o root \
  -g root \
  -m 0755 \
  /etc/systemd/system/llama-server.service.d

sudo tee \
  /etc/systemd/system/llama-server.service.d/10-llama-v0.4.0.conf \
  >/dev/null <<'EOF'
[Service]
ExecStart=
ExecStart=/home/user/llama-updates/llama.cpp-v0.4.0/build/bin/llama-server --models-preset /home/user/llama-models/presets.ini --host 0.0.0.0 --port 8080 --models-max 1 --sleep-idle-seconds -1
EOF

sudo systemctl daemon-reload
sudo systemd-analyze verify llama-server.service
systemctl cat llama-server.service
```

Verify that the effective service contains exactly one non-empty `ExecStart`, pointing to v0.4.0.

## 14. Restart production

```bash
sudo systemctl restart llama-server.service

for attempt in $(seq 1 60); do
  if curl -fsS \
    --connect-timeout 2 \
    --max-time 5 \
    http://127.0.0.1:8080/v1/models \
    >/tmp/llama-v0.4.0-production-models.json; then
    break
  fi
  sleep 1
done

systemctl --no-pager --full status llama-server.service
journalctl \
  -u llama-server.service \
  --since '-5 minutes' \
  --no-pager
```

Confirm that the status shows:

```text
/home/user/llama-updates/llama.cpp-v0.4.0/build/bin/llama-server
```

## 15. Production smoke test

Use the Gemma 4 12B coder model for production validation:

```bash
curl \
  --fail-with-body \
  --silent \
  --show-error \
  --max-time 300 \
  http://127.0.0.1:8080/v1/chat/completions \
  -H 'Content-Type: application/json' \
  -d '{
    "model": "coder-gemma4-12b-coder:LATEST",
    "messages": [
      {
        "role": "user",
        "content": "Reply with exactly V040_OK."
      }
    ],
    "temperature": 0,
    "max_tokens": 64,
    "thinking_budget_tokens": 0,
    "timings_per_token": true
  }' |
tee /tmp/llama-v0.4.0-smoke.json |
python3 -m json.tool
```

Inspect model loading and Vulkan selection:

```bash
journalctl \
  -u llama-server.service \
  --since '-10 minutes' \
  --no-pager |
grep -E \
  'version|build|Vulkan|RADV|PHOENIX|loading model|loaded model|offload|error|failed|DeviceLost|timings'
```

The log must show RADV/Phoenix rather than llvmpipe, and must not show `DeviceLost`, `ErrorDeviceLost`, model-load failure, or missing shared libraries.

## 16. Roll back if production validation fails

This rollback disables the override without deleting it.

```bash
sudo mv \
  /etc/systemd/system/llama-server.service.d/10-llama-v0.4.0.conf \
  /etc/systemd/system/llama-server.service.d/10-llama-v0.4.0.conf.disabled

sudo systemctl daemon-reload
sudo systemctl restart llama-server.service

systemctl --no-pager --full status llama-server.service
systemctl cat llama-server.service
```

Confirm that `ExecStart` again points to:

```text
/home/user/llama-updates/llama.cpp-b10549/build/bin/llama-server
```

The v0.4.0 tree remains available for investigation and does not need to be deleted.
