"""Probe DeepSeek API concurrency limit for a single key.

⚠️ **本脚本针对的是已弃用的 DeepSeek 服务商，保留作历史参考。**
项目于 2026-09 全面弃用 DeepSeek（见 src/config.py 注释），
当前生产使用 GLM + Qwen。本脚本仍引用 DEEPSEEK_API_KEY 等已删除的配置，
直接运行会因缺少环境变量而无法工作。

Fires N concurrent requests (deepseek-chat, 1-token output) at the API and
records how many succeed / rate-limit / fail, to find where the key's
RPM/TPM limit kicks in. This is the ceiling for how many books Westward Echo
can compile in parallel — translation is LLM-bound.

Usage:
    /opt/homebrew/bin/python3.11 scripts/probe_deepseek_concurrency.py [--concurrency 1,2,4,8,16,32]

Cost: each request is max_tokens=1, ~1 token output — negligible.

【中文·这个脚本干什么】
探测 DeepSeek API 单个密钥的并发上限：同时发 N 个请求，看从哪个并发数开始
被限流（返回 429/402/403）。这个上限决定了系统能同时翻译多少本书。

【中文·怎么运行】
```bash
python3.11 scripts/probe_deepseek_concurrency.py --concurrency 1,2,4,8,16,32
```

【中文·运行前要准备什么】
需要在 .env 中配置 DEEPSEEK_API_KEY（⚠️ 该配置已被项目弃用）。

【中文·⚠️ 会不会花真钱、会不会写生产数据库】
会调用 DeepSeek API，但每次只生成 1 个 token，费用可忽略不计。
不会写生产数据库。

【中文·会产生/修改什么文件】
不产生任何文件，结果打印到终端。
"""

import argparse
# 【中文】concurrent.futures 是 Python 标准库的"并发"工具，
# 可以同时发出多个网络请求而不是排队一个个来。
import concurrent.futures
import os
import time

import httpx
from dotenv import load_dotenv

load_dotenv()

# 【中文】⚠️ 以下三个环境变量引用的是已弃用的 DeepSeek 配置。
# 项目于 2026-09 切换到 GLM + Qwen 后，这些变量不再被生产代码使用。
API_KEY = os.getenv("DEEPSEEK_API_KEY", "")
BASE_URL = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com").rstrip("/")
MODEL = os.getenv("DEEPSEEK_CHAT_MODEL", "deepseek-chat")
URL = f"{BASE_URL}/chat/completions"


def one_request(i: int, timeout: float = 120.0, max_tokens: int = 1):
    """发送单个 API 请求并记录结果。

    【中文】这是并发探测的最小单元：发一个只生成 1 token 的请求，
    记录返回状态码和耗时。参数 i 是请求编号，用于区分哪个请求失败了。
    """
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
        # 【中文】超时不算崩溃，记为 "timeout" 状态继续统计。
        return {"i": i, "status": "timeout", "elapsed": time.monotonic() - t0}
    except Exception as e:
        return {"i": i, "status": f"err:{type(e).__name__}", "elapsed": time.monotonic() - t0}


def run_batch(concurrency: int, requests_per_batch: int = 12, max_tokens: int = 1):
    """Fire `requests_per_batch` requests with `concurrency` workers.

    【中文】用线程池同时发 requests_per_batch 个请求，最多 concurrency 个并行。
    统计成功（200）、被限流（429/402/403）、其他失败的数量。
    """
    t0 = time.monotonic()
    results = []
    # 【中文】ThreadPoolExecutor 是"线程池"：创建 concurrency 个工作线程，
    # 把请求任务分配给它们并行执行。
    with concurrent.futures.ThreadPoolExecutor(max_workers=concurrency) as ex:
        futures = [ex.submit(one_request, i, 120.0, max_tokens) for i in range(requests_per_batch)]
        for f in concurrent.futures.as_completed(futures):
            results.append(f.result())
    wall = time.monotonic() - t0
    ok = sum(1 for r in results if r["status"] == 200)
    # 【中文】429 = Too Many Requests, 402 = Payment Required,
    # 403 = Forbidden，这三个都算"被限流"。
    limited = sum(1 for r in results if r["status"] in (429, 402, 403))
    other = sum(1 for r in results if r["status"] not in (200, 429, 402, 403))
    return {
        "concurrency": concurrency,
        "requests": requests_per_batch,
        "ok": ok,
        "rate_limited": limited,
        "other_fail": other,
        "wall_seconds": round(wall, 1),
        # 【中文】记录第一个失败的请求作为样本，方便排查原因。
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
        # 【中文】每轮之间等 2 秒，避免上一轮的限流窗口还没过就触发下一轮，
        # 导致结果不准。
        time.sleep(2)  # small gap between batches so rate-limit windows don't overlap


if __name__ == "__main__":
    main()
