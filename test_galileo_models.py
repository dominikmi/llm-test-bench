"""Sequential integration tests for coder models served by Galileo."""

from __future__ import annotations

import json
import os
import time
import unittest
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Final

from benchmark_paths import MODEL_SMOKE_RESULTS_DIR, ensure_parent_directories

BASE_URL: Final = os.getenv(
    "GALILEO_BASE_URL", "http://127.0.0.1:8080/v1"
).rstrip("/")
API_KEY: Final = os.getenv("GALILEO_API_KEY", "sk-noauth")
TIMEOUT_SECONDS: Final = float(os.getenv("GALILEO_TIMEOUT_SECONDS", "1800"))
RESULTS_PATH: Final = Path(
    os.getenv(
        "GALILEO_RESULTS_PATH",
        str(MODEL_SMOKE_RESULTS_DIR / "galileo-model-results.json"),
    )
)
MODELS: Final = (
    "coder-gemma4-26B-A4B-it:LATEST",
    "coder-laguna-xs-2.1:LATEST",
    "coder-north-mini-code-1.0:LATEST",
    "coder-ornith:LATEST",
    "coder-qwen3-coder-next:LATEST",
    "coder-qwen3.6-35b-mtp:LATEST",
)


@dataclass(frozen=True, slots=True)
class ModelResult:
    """Measured result of one model smoke test."""

    model: str
    elapsed_seconds: float
    prompt_tokens: int
    completion_tokens: int
    completion_tokens_per_second: float
    response_text: str


class GalileoClient:
    """Minimal client for Galileo's OpenAI-compatible API."""

    def __init__(self, base_url: str, api_key: str, timeout_seconds: float) -> None:
        self._base_url = base_url
        self._api_key = api_key
        self._timeout_seconds = timeout_seconds

    def models(self) -> set[str]:
        """Return model identifiers advertised by Galileo."""
        response = self._request("GET", "/models")
        return {
            str(model["id"])
            for model in response.get("data", [])
            if isinstance(model, dict) and "id" in model
        }

    def smoke_test(self, model: str) -> ModelResult:
        """Request a short deterministic completion and capture performance."""
        payload = {
            "model": model,
            "messages": [
                {
                    "role": "user",
                    "content": (
                        "Reply with exactly MODEL_OK and no other text. "
                        "Do not use markdown."
                    ),
                }
            ],
            "temperature": 0,
            "max_tokens": 32,
            "stream": False,
        }
        started = time.perf_counter()
        response = self._request("POST", "/chat/completions", payload)
        elapsed_seconds = time.perf_counter() - started
        response_text = str(response["choices"][0]["message"].get("content") or "")
        usage = response.get("usage", {})
        prompt_tokens = int(usage.get("prompt_tokens", 0))
        completion_tokens = int(usage.get("completion_tokens", 0))
        token_rate = completion_tokens / elapsed_seconds if elapsed_seconds else 0.0
        return ModelResult(
            model=model,
            elapsed_seconds=round(elapsed_seconds, 3),
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            completion_tokens_per_second=round(token_rate, 3),
            response_text=response_text.strip(),
        )

    def _request(
        self,
        method: str,
        path: str,
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        body = json.dumps(payload).encode() if payload is not None else None
        request = urllib.request.Request(
            f"{self._base_url}{path}",
            data=body,
            method=method,
            headers={
                "Authorization": f"Bearer {self._api_key}",
                "Content-Type": "application/json",
            },
        )
        try:
            with urllib.request.urlopen(
                request, timeout=self._timeout_seconds
            ) as response:
                parsed = json.load(response)
        except urllib.error.HTTPError as error:
            details = error.read().decode(errors="replace")
            raise RuntimeError(
                f"Galileo returned HTTP {error.code} for {path}: {details}"
            ) from error
        except urllib.error.URLError as error:
            raise RuntimeError(f"Cannot reach Galileo at {self._base_url}: {error}") from error
        if not isinstance(parsed, dict):
            raise TypeError(f"Expected an object from {path}, got {type(parsed).__name__}")
        return parsed


class TestGalileoCoderModels(unittest.TestCase):
    """Validate model discovery and sequential generation for every coder preset."""

    client: GalileoClient

    @classmethod
    def setUpClass(cls) -> None:
        """Create one API client for the suite."""
        cls.client = GalileoClient(BASE_URL, API_KEY, TIMEOUT_SECONDS)

    def test_models_are_advertised(self) -> None:
        """Verify all expected coder aliases are exposed by the router."""
        available_models = self.client.models()
        self.assertEqual(set(), set(MODELS) - available_models)

    def test_models_generate_sequentially(self) -> None:
        """Load each model serially and verify a deterministic response."""
        results: list[ModelResult] = []
        failures: list[str] = []
        for model in MODELS:
            try:
                result = self.client.smoke_test(model)
                results.append(result)
                if "MODEL_OK" not in result.response_text.upper():
                    failures.append(
                        f"{model}: unexpected response {result.response_text!r}"
                    )
                print(
                    f"{model}: {result.elapsed_seconds:.3f}s, "
                    f"{result.completion_tokens} completion tokens, "
                    f"{result.completion_tokens_per_second:.3f} end-to-end tok/s"
                )
            except (
                IndexError,
                KeyError,
                RuntimeError,
                TypeError,
                ValueError,
            ) as error:
                failures.append(f"{model}: {type(error).__name__}: {error}")
        ensure_parent_directories(RESULTS_PATH)
        RESULTS_PATH.write_text(
            json.dumps([asdict(result) for result in results], indent=2) + "\n",
            encoding="utf-8",
        )
        self.assertEqual([], failures, "\n".join(failures))


if __name__ == "__main__":
    unittest.main(verbosity=2)
