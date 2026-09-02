"""Small bounded load smoke that reports latency percentiles and enforces budgets."""
from __future__ import annotations

import argparse
import math
import statistics
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.request import Request, urlopen


def request_once(url: str, byte_limit: int, timeout: float) -> tuple[float, int]:
    started = time.perf_counter()
    request = Request(url, headers={"User-Agent": "FPLScoutLoadSmoke/1.0"})
    with urlopen(request, timeout=timeout) as response:
        body = response.read(byte_limit + 1)
        if response.status != 200:
            raise RuntimeError(f"HTTP {response.status}")
        if len(body) > byte_limit:
            raise RuntimeError(f"payload exceeded {byte_limit} bytes")
    return (time.perf_counter() - started) * 1000, len(body)


def percentile(values: list[float], fraction: float) -> float:
    ordered = sorted(values)
    return ordered[max(0, math.ceil(len(ordered) * fraction) - 1)]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("url")
    parser.add_argument("--requests", type=int, default=20)
    parser.add_argument("--concurrency", type=int, default=4)
    parser.add_argument("--p95-ms", type=float, default=5000)
    parser.add_argument("--byte-limit", type=int, default=1_500_000)
    parser.add_argument("--timeout", type=float, default=30)
    args = parser.parse_args()
    if not 1 <= args.requests <= 100 or not 1 <= args.concurrency <= 10:
        parser.error("requests must be 1-100 and concurrency 1-10")

    latencies: list[float] = []
    sizes: list[int] = []
    errors: list[str] = []
    with ThreadPoolExecutor(max_workers=args.concurrency) as executor:
        futures = [executor.submit(request_once, args.url, args.byte_limit, args.timeout) for _ in range(args.requests)]
        for future in as_completed(futures):
            try:
                latency, size = future.result()
                latencies.append(latency)
                sizes.append(size)
            except Exception as error:
                errors.append(str(error))
    if errors:
        raise RuntimeError(f"{len(errors)}/{args.requests} requests failed: {errors[:3]}")
    p50 = percentile(latencies, 0.50)
    p95 = percentile(latencies, 0.95)
    print(
        f"url={args.url} n={len(latencies)} concurrency={args.concurrency} "
        f"p50_ms={p50:.1f} p95_ms={p95:.1f} mean_ms={statistics.mean(latencies):.1f} "
        f"max_bytes={max(sizes)} errors=0"
    )
    if p95 > args.p95_ms:
        raise RuntimeError(f"p95 {p95:.1f}ms exceeded {args.p95_ms:.1f}ms budget")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
