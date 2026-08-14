"""Probe DeepSeek API concurrency limit for a single key.

Fires N concurrent requests (deepseek-chat, 1-token output) at the API and
records how many succeed / rate-limit / fail, to find where the key's
RPM/TPM limit kicks in. This is the ceiling for how many books Westward Echo
can compile in parallel — translation is LLM-bound.

Usage:
    /opt/homebrew/bin/python3.11 scripts/probe_deepseek_concurrency.py [--concurrency 1,2,4,8,16,32]

Cost: each request is max_tokens=1, ~1 token output — negligible.
"""

import argparse
import concurrent.futures
import os
import time

import httpx
from dotenv import load_dotenv

load_dotenv()

API_KEY = os.getenv("DEEPSEEK_API_KEY", "")
BASE_URL = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com").rstrip("/")
MODEL = os.getenv("DEEPSEEK_CHAT_MODEL", "deepseek-chat")
URL = f"{BASE_URL}/chat/completions"


def one_request(i: int, timeout: float = 120.0, max_tokens: int = 1):
    payload = {
        "model": MODEL,
        "messages": [{"role": "user", "content": "写一段约" + str(max_tokens) + "字的英文故事。"}],
        "max_tokens": max_tokens,
        "temperature": 0,
    }
    headers = {
        "Authorization": f"Bearer {API_KEY}",
        "Content-Type": "application/json",
    }
    t0 = time.monotonic()
    try:
        with httpx.Client(timeout=timeout) as client:
            r = client.post(URL, json=payload, headers=headers)
        elapsed = time.monotonic() - t0
        return {"i": i, "status": r.status_code, "elapsed": elapsed,
                "body": r.text[:200]}
    except httpx.TimeoutException:
        return {"i": i, "status": "timeout", "elapsed": time.monotonic() - t0}
    except Exception as e:
        return {"i": i, "status": f"err:{type(e).__name__}", "elapsed": time.monotonic() - t0}


def run_batch(concurrency: int, requests_per_batch: int = 12, max_tokens: int = 1):
    """Fire `requests_per_batch` requests with `concurrency` workers."""
    t0 = time.monotonic()
    results = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=concurrency) as ex:
        futures = [ex.submit(one_request, i, 120.0, max_tokens) for i in range(requests_per_batch)]
        for f in concurrent.futures.as_completed(futures):
            results.append(f.result())
    wall = time.monotonic() - t0
    ok = sum(1 for r in results if r["status"] == 200)
    limited = sum(1 for r in results if r["status"] in (429, 402, 403))
    other = sum(1 for r in results if r["status"] not in (200, 429, 402, 403))
    return {
        "concurrency": concurrency,
        "requests": requests_per_batch,
        "ok": ok,
        "rate_limited": limited,
        "other_fail": other,
        "wall_seconds": round(wall, 1),
        "sample_fail": next((r for r in results if r["status"] != 200), None),
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--concurrency", default="1,2,4,8,16,32",
                        help="comma-separated concurrency levels to probe")
    parser.add_argument("--max-tokens", type=int, default=1,
                        help="max_tokens per request (simulate translation output size)")
    args = parser.parse_args()

    if not API_KEY:
        print("DEEPSEEK_API_KEY not set in .env")
        return

    levels = [int(x) for x in args.concurrency.split(",")]
    print(f"Probing DeepSeek ({MODEL}) single-key concurrency limit, max_tokens={args.max_tokens}\n")

    print(f"{'并发数':>4} | {'请求':>4} | {'成功':>4} | {'限流':>4} | {'其他失败':>6} | {'墙钟秒':>6}")
    print("-" * 56)
    for level in levels:
        r = run_batch(level, max_tokens=args.max_tokens)
        print(f"{r['concurrency']:>4} | {r['requests']:>4} | {r['ok']:>4} | "
              f"{r['rate_limited']:>4} | {r['other_fail']:>6} | {r['wall_seconds']:>6}")
        if r["sample_fail"]:
            sf = r["sample_fail"]
            print(f"        ↳ 失败样本: HTTP {sf['status']} {sf['body'][:120]}")
        time.sleep(2)  # small gap between batches so rate-limit windows don't overlap


if __name__ == "__main__":
    main()
