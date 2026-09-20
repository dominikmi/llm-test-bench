import asyncio
import time
import os
import json
import urllib.request
import urllib.error

# --- CONFIGURATION ---
BASE_URL = os.getenv("GALILEO_BASE_URL", "http://127.0.0.1:8080/v1").rstrip("/")
TARGET_MODEL = "coder-whittle-moe-27b-a18b:LATEST"
DRAFTER_MODEL_PATH = "/var/llama/models/Qwen2.5-Coder-4B-Q4_K_M.gguf"

async def fetch_completion(payload):
    """Performs the actual HTTP POST request using urllib."""
    url = f"{BASE_URL}/chat/completions"
    data = json.dumps(payload).encode('utf-8')
    
    # We use a loop to handle the blocking urllib call in a separate thread
    def sync_request():
        req = urllib.request.Request(
            url, 
            data=data, 
            headers={'Content-Type': 'application/json'},
            method='POST'
        )
        with urllib.request.urlopen(req, timeout=300) as response:
            return json.loads(response.read().decode('utf-8'))

    # Run the blocking sync_request in a thread to avoid freezing the loop
    return await asyncio.to_thread(sync_request)

async def run_benchmark():
    print(f"🚀 TARGET: {TARGET_MODEL}")
    print(f"🚀 DRAFTER: {DRAFTER_MODEL_PATH}")
    print("-" * 50)

    # --- TEST 1: WITH SPECULATIVE DECODING ---
    payload_spec = {
        "model": TARGET_MODEL,
        "messages": [
            {"role": "user", "content": "Write a complex Python function to calculate the Fibonacci sequence using recursion and memoization."}
        ],
        "max_tokens": 500,
        "speculative_draft": DRAFTER_MODEL_PATH,
        "speculative_draft_n": 4
    }

    print("🧪 Running with Speculative Decoding...")
    start_spec = time.perf_counter()
    try:
        response = await fetch_completion(payload_spec)
        end_spec = time.perf_counter()
        spec_time = end_spec - start_spec
        content = response.choices[0].message.content
        print(f"✅ Speculative Time: {spec_time:.2f}s")
        print(f"📝 Preview: {content[:80]}...")
    except Exception as e:
        print(f"❌ Speculative Test Failed: {e}")
        spec_time = None

    print("-" * 50)

    # --- TEST 2: WITHOUT SPECULATIVE DECODING (Baseline) ---
    payload_base = {
        "model": TARGET_MODEL,
        "messages": [
            {"role": "user", "content": "Write a complex Python function to calculate the Fibonacci sequence using recursion and memoization."}
        ],
        "max_tokens": 500
    }

    print("🧪 Running Baseline (No Drafter)...")
    start_base = time.perf_counter()
    try:
        response = await fetch_completion(payload_base)
        end_base = time.perf_counter()
        base_time = end_base - start_base
        print(f"✅ Baseline Time: {base_time:.2f}s")
    except Exception as e:
        print(f"❌ Baseline Test Failed: {e}")
        base_time = None

    # --- FINAL COMPARISON ---
    print("-" * 50)
    if spec_time and base_time:
        diff = base_time - spec_time
        improvement = (diff / base_time) * 100
        if improvement > 0:
            print(f"🚀 RESULT: Speculative Decoding was {improvement:.2f}% FASTER!")
        else:
            print(f"⚠️ RESULT: Speculative Decoding was SLOWER by {abs(improvement):.2f}%")
    else:
        print("❌ Could not complete comparison. Check server logs.")

if __name__ == "__main__":
    asyncio.run(run_benchmark())

