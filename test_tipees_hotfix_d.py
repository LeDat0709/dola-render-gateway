"""TIPEES Hotfix D test riêng — chạy nhanh, đứng độc lập không bị bug C chặn."""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import browser_pool
from browser_pool import _rs, _reset_rate_state, _pace


async def _measure_jitter_async(key, n_samples=20):
    slots = []
    for _ in range(n_samples):
        await _pace(key)
        slots.append(_rs(key)["slot"])
    diffs = [slots[i + 1] - slots[i] for i in range(len(slots) - 1)]
    return max(diffs) - min(diffs), diffs


async def d1_scales_with_learned_extra():
    browser_pool.config.SUBMIT_GAP_SEC = 1.0
    browser_pool.config.SUBMIT_JITTER_SEC = 1.0
    browser_pool.config.MAX_JOBS_PER_IP = 10
    _reset_rate_state()

    s = _rs("proxyA")
    s["learned_extra"] = 0.0
    s["slot"] = 0.0
    jitter_idle, diffs_idle = await _measure_jitter_async("proxyA", 10)

    _reset_rate_state()
    s = _rs("proxyA")
    s["learned_extra"] = 8.0
    s["slot"] = 0.0
    jitter_busy, diffs_busy = await _measure_jitter_async("proxyA", 10)

    print(f"  D.1 idle (learned=0):  jitter = {jitter_idle:.3f}s, diffs range [{min(diffs_idle):.3f}, {max(diffs_idle):.3f}]")
    print(f"  D.1 busy (learned=8):  jitter = {jitter_busy:.3f}s, diffs range [{min(diffs_busy):.3f}, {max(diffs_busy):.3f}]")
    assert jitter_busy > jitter_idle * 2, f"jitter_busy ({jitter_busy:.2f}) phải > 2× jitter_idle ({jitter_idle:.2f})"
    # 2.0 thay vì 2.5 vì random.uniform có thể rơi thấp; quan trọng là > idle×2
    assert jitter_busy > 2.0, f"jitter với learned_extra=8 phải > 2.0s, mà = {jitter_busy:.2f}"
    _reset_rate_state()
    return True


async def d2_scales_with_busy_ip():
    browser_pool.config.SUBMIT_GAP_SEC = 1.0
    browser_pool.config.SUBMIT_JITTER_SEC = 1.0
    browser_pool.config.MAX_JOBS_PER_IP = 4
    _reset_rate_state()

    s = _rs("proxyB")
    s["learned_extra"] = 0.0
    s["slot"] = 0.0
    browser_pool._egress_busy["proxyB"] = 0
    jitter_idle, _ = await _measure_jitter_async("proxyB", 10)

    _reset_rate_state()
    s = _rs("proxyB")
    s["learned_extra"] = 0.0
    s["slot"] = 0.0
    browser_pool._egress_busy["proxyB"] = 3
    jitter_full, _ = await _measure_jitter_async("proxyB", 10)

    print(f"  D.2 idle IP (busy=0/4): jitter = {jitter_idle:.3f}s")
    print(f"  D.2 full IP (busy=3/4): jitter = {jitter_full:.3f}s")
    assert jitter_full > jitter_idle, f"jitter_full ({jitter_full:.3f}) phải > jitter_idle ({jitter_idle:.3f})"
    browser_pool._egress_busy.pop("proxyB", None)
    _reset_rate_state()
    return True


async def d3_clamped_at_boost_max():
    browser_pool.config.SUBMIT_GAP_SEC = 0.1
    browser_pool.config.SUBMIT_JITTER_SEC = 1.0
    browser_pool.config.MAX_JOBS_PER_IP = 10
    _reset_rate_state()

    s = _rs("proxyC")
    s["learned_extra"] = browser_pool._ADAPTIVE_EXTRA_MAX
    s["slot"] = 0.0
    browser_pool._egress_busy["proxyC"] = browser_pool.config.MAX_JOBS_PER_IP
    jitter_max, diffs = await _measure_jitter_async("proxyC", 5)
    expected_max = browser_pool._JITTER_BOOST_MAX * browser_pool.config.SUBMIT_JITTER_SEC
    print(f"  D.3 clamp: jitter = {jitter_max:.3f}s (max={expected_max:.3f}s, scale=min(4, 4.8)=4)")
    assert jitter_max <= expected_max * 1.5, f"jitter ({jitter_max:.2f}) phải ≤ {expected_max * 1.5:.2f}"
    browser_pool._egress_busy.pop("proxyC", None)
    _reset_rate_state()
    return True


async def main():
    print("=== TIPEES Hotfix D — Jitter Adaptive ===\n", flush=True)
    print("[D.1] jitter scales with learned_extra:", flush=True)
    await d1_scales_with_learned_extra()
    print("  ✓ D.1 PASS\n", flush=True)

    print("[D.2] jitter scales with busy IP:", flush=True)
    await d2_scales_with_busy_ip()
    print("  ✓ D.2 PASS\n", flush=True)

    print("[D.3] jitter clamped at _JITTER_BOOST_MAX:", flush=True)
    await d3_clamped_at_boost_max()
    print("  ✓ D.3 PASS\n", flush=True)

    print("✅ Tất cả 3 test Hotfix D PASS")


if __name__ == "__main__":
    asyncio.run(main())
