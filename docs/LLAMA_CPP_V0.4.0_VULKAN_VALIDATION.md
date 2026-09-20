# Validate llama.cpp v0.4.0 Vulkan on the llama.cpp host

Use this runbook before deploying v0.4.0. It compares b10549 and v0.4.0 on the llama.cpp host's AMD Radeon 780M/RADV backend and produces an explicit `DEPLOY`, `DEPLOY WITH PER-MODEL OVERRIDES`, or `DO NOT DEPLOY` decision.

The tests cover backend correctness, flash attention, lazy tensor loading, IQ3_S batching, MoE routing, CPU expert offload, model loading, memory, and device-loss detection.

## Relevant v0.4.0 changes

The following changes matter to this host:

- Vulkan IQ3_S mat-vec handling for batch sizes greater than four.
- Vulkan flash-attention dequantization-path fix.
- `mul_mat_id` and expert/row-ID shader improvements for MoE models.
- Conditional use of `VK_KHR_shader_bfloat16`.
- Vulkan graph view-alias dependency fixes.
- Qwen3.8-Flash-Next top-k and lightning-indexer Vulkan support.
- `--lazy-mode` for on-demand tensor reading.
- `--n-cpu-ffn` and existing `--n-cpu-moe` for hybrid CPU/GPU placement.

These optimizations are compiled into the Vulkan backend. There is no separate production performance CMake switch beyond `GGML_VULKAN=ON`.

Do not enable Vulkan debug, result checking, shader debug data, or validation in the production build.

## 1. Set variables

Run on the llama.cpp host as `user`.

```bash
set -euo pipefail

OLD_ROOT="/home/user/llama-updates/llama.cpp-b10549"
NEW_ROOT="/home/user/llama-updates/llama.cpp-v0.4.0"
VERIFY_BUILD="$NEW_ROOT/build-vulkan-verify"
VULKAN_ICD="/usr/share/vulkan/icd.d/radeon_icd.json"
TIMESTAMP="$(date -u +%Y%m%dT%H%M%SZ)"
RESULTS="/home/user/llama-updates/vulkan-validation-$TIMESTAMP"

mkdir -p "$RESULTS"
```

## 2. Capture hardware and driver capabilities

```bash
{
  echo '=== HOST ==='
  hostnamectl
  echo
  echo '=== CPU ==='
  lscpu
  echo
  echo '=== MEMORY ==='
  free -h
  echo
  echo '=== VULKAN SUMMARY ==='
  VK_ICD_FILENAMES="$VULKAN_ICD" vulkaninfo --summary
  echo
  echo '=== GLSLC ==='
  glslc --version
  echo
  echo '=== RELEVANT VULKAN FEATURES ==='
  VK_ICD_FILENAMES="$VULKAN_ICD" vulkaninfo 2>/dev/null |
    grep -E 'deviceName|driverName|driverInfo|apiVersion|subgroupSize|shaderInt16|shaderFloat16|shaderInt64|bufferDeviceAddress|cooperativeMatrix|shaderBFloat16|memoryBudget' |
    head -160
  echo
  echo '=== RELEVANT EXTENSIONS ==='
  VK_ICD_FILENAMES="$VULKAN_ICD" vulkaninfo 2>/dev/null |
    grep -E 'VK_KHR_cooperative_matrix|VK_KHR_shader_bfloat16|VK_KHR_shader_float16_int8|VK_KHR_buffer_device_address|VK_EXT_memory_budget|VK_EXT_subgroup_size_control|VK_EXT_shader_atomic_float' |
    sort -u
} | tee "$RESULTS/hardware.txt"
```

Required observations:

- AMD Radeon 780M/RADV must be the first hardware Vulkan device.
- llvmpipe may appear as another device, but tests must explicitly select `Vulkan0`.
- `glslc` must be available.
- Record whether BF16 and cooperative matrices are supported; do not assume they are.

## 3. Configure a separate verification build

This build enables tests but leaves expensive per-operation Vulkan result checking and validation layers disabled. Production uses the separate `$NEW_ROOT/build` directory from the installation runbook.

```bash
cmake \
  -S "$NEW_ROOT" \
  -B "$VERIFY_BUILD" \
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
  -DGGML_VULKAN_RUN_TESTS=ON \
  -DGGML_BUILD_TESTS=ON \
  -DGGML_BUILD_EXAMPLES=OFF \
  -DLLAMA_BUILD_IS_DEV=OFF \
  -DLLAMA_BUILD_SERVER=ON \
  -DLLAMA_BUILD_TOOLS=ON \
  -DLLAMA_BUILD_TESTS=ON \
  -DLLAMA_BUILD_EXAMPLES=OFF \
  -DLLAMA_BUILD_APP=ON \
  -DLLAMA_BUILD_UI=ON \
  -DLLAMA_USE_PREBUILT_UI=ON \
  -DLLAMA_OPENSSL=ON \
  -DLLAMA_SUBPROCESS=ON

cmake --build "$VERIFY_BUILD" \
  --config Release \
  --parallel "$(nproc)"
```

## 4. Verify test and benchmark CLI capabilities

```bash
VERIFY_BENCH="$VERIFY_BUILD/bin/llama-bench"
VERIFY_TEST="$VERIFY_BUILD/bin/test-backend-ops"
VERIFY_SERVER="$VERIFY_BUILD/bin/llama-server"

for binary in "$VERIFY_BENCH" "$VERIFY_TEST" "$VERIFY_SERVER"; do
  test -x "$binary" || {
    echo "Missing binary: $binary" >&2
    exit 1
  }
done

"$VERIFY_BENCH" --help | tee "$RESULTS/llama-bench-help.txt"
"$VERIFY_SERVER" --help | tee "$RESULTS/llama-server-help.txt"

for option in flash-attn lazy-mode device n-cpu-moe; do
  grep -q -- "--$option" "$RESULTS/llama-bench-help.txt" || {
    echo "llama-bench is missing --$option" >&2
    exit 1
  }
done

grep -q -- '--n-cpu-ffn' "$RESULTS/llama-server-help.txt" || {
  echo 'llama-server is missing --n-cpu-ffn' >&2
  exit 1
}
```

Expected v0.4.0 benchmark syntax includes:

```text
-fa, --flash-attn <on|off|auto>
-lzm, --lazy-mode <on|auto|off>
-dev, --device <dev0/dev1/...>
-ncmoe, --n-cpu-moe <n>
```

## 5. List candidate devices

```bash
VK_ICD_FILENAMES="$VULKAN_ICD" \
"$VERIFY_BENCH" --list-devices |
tee "$RESULTS/devices.txt"
```

The hardware device must be `Vulkan0`. Stop if `Vulkan0` is llvmpipe.

## 6. Run Vulkan backend correctness tests

The full backend suite may take time. It must finish without failed Vulkan operations or device loss.

```bash
VK_ICD_FILENAMES="$VULKAN_ICD" \
"$VERIFY_TEST" test \
  -b Vulkan0 \
  2>&1 |
tee "$RESULTS/test-backend-ops-vulkan0.log"
```

If the complete suite is prohibitively long, do not silently skip it. Record the interruption and run at least the operations used heavily by the target models, based on the test tool's own help:

```bash
"$VERIFY_TEST" --help | tee "$RESULTS/test-backend-ops-help.txt"
```

The final decision cannot be an unconditional `DEPLOY` if backend correctness tests report failures.

## 7. Define representative models

```bash
MODEL_IQ3="/var/llama/models/qwen3.8-27b-gsq-rco/Qwen3.8-27B-GSQ-RCO-IQ3_S-mtp.gguf"
MODEL_SMALL="/var/llama/models/gemma-4-12B-coder/gemma4-coding-Q8_0.gguf"
MODEL_LAGUNA="/var/llama/models/laguna/Laguna-XS-2.1-Q4_K_M.gguf"
MODEL_CODER_NEXT="/var/llama/models/qwen3-coder-next/Qwen3-Coder-Next-UD-Q2_K_XL.gguf"

for model in \
  "$MODEL_IQ3" \
  "$MODEL_SMALL" \
  "$MODEL_LAGUNA" \
  "$MODEL_CODER_NEXT"; do
  test -r "$model" || {
    echo "Missing model: $model" >&2
    exit 1
  }
done
```

The model set represents:

- IQ3_S batching and MTP;
- a smaller dense/coder baseline;
- a 33B/3B-active MoE;
- a larger Q2_K_XL coding MoE.

## 8. Establish a maintenance window

`llama-bench` must not compete with the production router for shared DDR5 bandwidth or memory. Ensure no Mac benchmark or OpenCode session is active.

```bash
systemctl --no-pager --full status llama-server.service
ss -tnp | grep ':8080' || true
```

When no client request is active, stop production:

```bash
sudo systemctl stop llama-server.service
```

Create an automatic recovery trap so an interrupted shell restarts b10549:

```bash
restore_service() {
  sudo systemctl start llama-server.service || true
}
trap restore_service EXIT
```

Confirm memory has settled before benchmarking:

```bash
for attempt in $(seq 1 30); do
  available_kib="$(awk '/MemAvailable:/ {print $2}' /proc/meminfo)"
  if [ "$available_kib" -ge 73400320 ]; then
    break
  fi
  sleep 2
done

free -h | tee "$RESULTS/memory-before.txt"
```

The 70 GiB threshold prevents benchmarking while a router child is still releasing model memory.

## 9. Capture b10549 baseline

```bash
OLD_BENCH="$OLD_ROOT/build/bin/llama-bench"
test -x "$OLD_BENCH"
"$OLD_BENCH" --help >"$RESULTS/old-llama-bench-help.txt"
```

Confirm the old flash-attention syntax shown in its help. If it accepts `on|off`, run:

```bash
for model_name in iq3 small laguna coder-next; do
  case "$model_name" in
    iq3) model="$MODEL_IQ3" ;;
    small) model="$MODEL_SMALL" ;;
    laguna) model="$MODEL_LAGUNA" ;;
    coder-next) model="$MODEL_CODER_NEXT" ;;
  esac

  for flash_attention in on off; do
    echo "OLD model=$model_name FA=$flash_attention"
    /usr/bin/time \
      -v \
      -o "$RESULTS/old-${model_name}-fa-${flash_attention}.time" \
      env VK_ICD_FILENAMES="$VULKAN_ICD" \
      "$OLD_BENCH" \
        -m "$model" \
        -p 512 \
        -n 128 \
        -b 512 \
        -ub 512 \
        -ctk q8_0 \
        -ctv q8_0 \
        -t 8 \
        -ngl 99 \
        -dev Vulkan0 \
        -fa "$flash_attention" \
        -r 3 \
        -o jsonl \
        --progress \
        >"$RESULTS/old-${model_name}-fa-${flash_attention}.jsonl" \
        2>"$RESULTS/old-${model_name}-fa-${flash_attention}.stderr"
  done
done
```

If b10549 documents `0|1` instead, use `1` for on and `0` for off. Do not guess; follow `$RESULTS/old-llama-bench-help.txt`.

## 10. Test v0.4.0 flash attention and lazy mode

Run the same matrix with FA on/off and lazy loading on/off:

```bash
for model_name in iq3 small laguna coder-next; do
  case "$model_name" in
    iq3) model="$MODEL_IQ3" ;;
    small) model="$MODEL_SMALL" ;;
    laguna) model="$MODEL_LAGUNA" ;;
    coder-next) model="$MODEL_CODER_NEXT" ;;
  esac

  for flash_attention in on off; do
    for lazy_mode in off on; do
      echo "NEW model=$model_name FA=$flash_attention LAZY=$lazy_mode"
      /usr/bin/time \
        -v \
        -o "$RESULTS/new-${model_name}-fa-${flash_attention}-lazy-${lazy_mode}.time" \
        env VK_ICD_FILENAMES="$VULKAN_ICD" \
        "$VERIFY_BENCH" \
          -m "$model" \
          -p 512 \
          -n 128 \
          -b 512 \
          -ub 512 \
          -ctk q8_0 \
          -ctv q8_0 \
          -t 8 \
          -ngl 99 \
          -dev Vulkan0 \
          -fa "$flash_attention" \
          -lzm "$lazy_mode" \
          -r 3 \
          -o jsonl \
          --progress \
          >"$RESULTS/new-${model_name}-fa-${flash_attention}-lazy-${lazy_mode}.jsonl" \
          2>"$RESULTS/new-${model_name}-fa-${flash_attention}-lazy-${lazy_mode}.stderr"
    done
  done
done
```

This yields separate results for PP512 and TG128 while matching production's Q8 KV cache, 512 batch, 512 microbatch, eight CPU threads, and full Vulkan offload.

## 11. Test MoE CPU expert placement

`--n-cpu-moe` is directly supported by `llama-bench`. Test only MoE representatives:

```bash
for model_name in laguna coder-next; do
  case "$model_name" in
    laguna) model="$MODEL_LAGUNA" ;;
    coder-next) model="$MODEL_CODER_NEXT" ;;
  esac

  for cpu_moe in 0 4 8; do
    echo "NEW model=$model_name CPU_MOE=$cpu_moe"
    /usr/bin/time \
      -v \
      -o "$RESULTS/new-${model_name}-cpu-moe-${cpu_moe}.time" \
      env VK_ICD_FILENAMES="$VULKAN_ICD" \
      "$VERIFY_BENCH" \
        -m "$model" \
        -p 512 \
        -n 128 \
        -b 512 \
        -ub 512 \
        -ctk q8_0 \
        -ctv q8_0 \
        -t 8 \
        -ngl 99 \
        -dev Vulkan0 \
        -fa on \
        -lzm off \
        -ncmoe "$cpu_moe" \
        -r 3 \
        -o jsonl \
        --progress \
        >"$RESULTS/new-${model_name}-cpu-moe-${cpu_moe}.jsonl" \
        2>"$RESULTS/new-${model_name}-cpu-moe-${cpu_moe}.stderr"
  done
done
```

On a UMA APU, CPU and Vulkan share DDR5. More CPU placement is not automatically faster. Keep `n-cpu-moe=0` unless a nonzero value improves repeatable TG128 without unacceptable PP512 or memory regressions.

## 12. Treat `--n-cpu-ffn` as experimental

v0.4.0 exposes `--n-cpu-ffn` through server/common CLI options, but it is not part of the documented v0.4.0 `llama-bench` parameter set. Do not infer production settings from a different flag.

If hybrid FFN placement is needed to fit a model, run a separate temporary-server/API experiment after the basic Vulkan decision. Otherwise retain the default:

```text
n-cpu-ffn = 0
```

## 13. Check kernel and benchmark logs for failures

```bash
journalctl \
  -k \
  --since "30 minutes ago" \
  --no-pager |
grep -Ei \
  'amdgpu|radv|gpu reset|device lost|ring timeout|page fault|vm fault' |
tee "$RESULTS/kernel-gpu-errors.txt" || true

grep -R -n -E \
  'DeviceLost|ErrorDeviceLost|VK_ERROR|failed|mismatch|NaN|out of memory|OOM' \
  "$RESULTS" |
tee "$RESULTS/benchmark-errors.txt" || true
```

Any GPU reset, `DeviceLost`, repeatable numerical mismatch, or model-specific malformed output is a deployment blocker until isolated.

## 14. Summarize JSONL performance

```bash
python3 - "$RESULTS" <<'PY'
from __future__ import annotations

import json
import statistics
import sys
from pathlib import Path

root = Path(sys.argv[1])
rows: list[dict[str, object]] = []

for path in sorted(root.glob("*.jsonl")):
    measurements: list[float] = []
    tests: list[str] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        payload = json.loads(line)
        entries = payload if isinstance(payload, list) else [payload]
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            value = entry.get("avg_ts")
            if isinstance(value, int | float):
                measurements.append(float(value))
                tests.append(str(entry.get("test", "unknown")))
    rows.append({
        "file": path.name,
        "tests": sorted(set(tests)),
        "mean_tokens_per_second": (
            round(statistics.mean(measurements), 3) if measurements else None
        ),
        "measurements": measurements,
    })

summary_path = root / "summary.json"
summary_path.write_text(json.dumps(rows, indent=2) + "\n", encoding="utf-8")

for row in rows:
    print(
        f"{row['file']:<55} "
        f"tests={','.join(row['tests'])} "
        f"mean={row['mean_tokens_per_second']}"
    )
PY
```

`llama-bench` normally emits separate PP and TG entries. Inspect `summary.json` and the original JSONL rather than averaging PP and TG together for the final decision.

## 15. Decision rules

### `DO NOT DEPLOY`

Choose this if any condition is true:

- Vulkan backend correctness tests fail.
- RADV reports GPU reset, VM fault, or device loss.
- Any production model no longer loads.
- JSON schema or tool-call output becomes malformed.
- Candidate TG128 is more than 10% slower on two or more representative models with equivalent settings.
- Candidate repeatedly hangs where b10549 completes.

### `DEPLOY WITH PER-MODEL OVERRIDES`

Choose this when v0.4.0 is stable overall but one setting varies by model, for example:

- FA improves Laguna and Qwen3-Coder-Next but regresses one dense model.
- Lazy loading reduces peak memory but hurts one model's repeated latency.
- CPU MoE placement helps only the largest MoE model.

Keep global defaults conservative and override only demonstrated exceptions in `presets.ini`.

### `DEPLOY`

Choose this only when:

- backend tests pass;
- no GPU/device-loss errors appear;
- every representative model loads and generates valid output;
- median/mean PP512 and TG128 are no worse than 5% below b10549;
- at least one relevant model or stability metric improves;
- production-like API smoke tests pass.

## 16. Decide runtime settings

Use these rules:

### Flash attention

Keep globally enabled only if FA-on is stable and not more than 5% slower for every routinely used model. Otherwise remove the global setting and configure FA per model.

### Lazy mode

Enable only if it materially reduces load time or peak RSS and repeated PP/TG regress by no more than 5%. For predictable benchmark and agent latency, default to off when the model already fits comfortably.

### CPU MoE

Use a nonzero value only when it improves TG128 by at least 5% across three repetitions or enables a model that otherwise cannot fit. Shared-memory bandwidth contention makes `0` the expected default on the llama.cpp host.

### CPU FFN

Keep at `0` unless a separate server/API test demonstrates a fit or speed benefit.

## 17. Record the decision

```bash
cat >"$RESULTS/DECISION.md" <<'EOF'
# llama.cpp v0.4.0 Vulkan decision

Decision: FILL_IN_DEPLOY_DEPLOY_WITH_OVERRIDES_OR_DO_NOT_DEPLOY

## Environment

- GPU: AMD Radeon 780M
- Driver: RADV / Mesa 25.2.8
- Old build: b10549
- Candidate: v0.4.0

## Correctness

- Backend tests:
- GPU reset/device loss:
- Model-load failures:
- Output/schema failures:

## Performance

- IQ3_S PP512/TG128:
- Gemma 4 12B Q8_0 PP512/TG128:
- Laguna MoE PP512/TG128:
- Qwen3-Coder-Next PP512/TG128:

## Selected settings

- flash-attn:
- lazy-mode:
- n-cpu-moe:
- n-cpu-ffn:

## Rationale

FILL_IN
EOF

printf 'Validation artifacts: %s\n' "$RESULTS"
```

Do not proceed to production until `DECISION.md` is completed.

## 18. Restore production after testing

The recovery trap starts the currently configured service, which is still b10549 before deployment:

```bash
restore_service
trap - EXIT

systemctl --no-pager --full status llama-server.service
curl -fsS \
  --connect-timeout 2 \
  --max-time 10 \
  http://127.0.0.1:8080/v1/models \
  >/dev/null
```

If the decision is `DEPLOY` or `DEPLOY WITH PER-MODEL OVERRIDES`, continue at the production deployment section of `LLAMA_CPP_V0.4.0_INSTALL.md`. If it is `DO NOT DEPLOY`, leave b10549 active and retain all validation artifacts for diagnosis.
