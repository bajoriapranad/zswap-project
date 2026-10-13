#!/usr/bin/env python3
import sys
import time
import os
import subprocess
import json
from collections import deque

class TelemetryAggregator:
    def __init__(self, window_sec=5):
        self.window_sec = window_sec
        self.history = deque() # (timestamp, store_count, reject_total)

    def get_sys_memory_metrics(self):
        mem_info = {}
        with open("/proc/meminfo", "r") as f:
            for line in f:
                parts = line.split(":")
                if len(parts) == 2:
                    key = parts[0].strip()
                    val = parts[1].strip().split()[0]
                    try:
                        mem_info[key] = int(val)
                    except ValueError:
                        pass

        mem_total_kb = mem_info.get("MemTotal", 1)
        mem_avail_kb = mem_info.get("MemAvailable", 0)
        zswap_kb = mem_info.get("Zswap", 0)
        zswapped_kb = mem_info.get("Zswapped", 0)

        # Read max_pool_percent
        max_pool_pct = 20
        try:
            with open("/sys/module/zswap/parameters/max_pool_percent", "r") as f:
                max_pool_pct = int(f.read().strip())
        except Exception:
            pass

        # Read pool_total_size from debugfs (in bytes) or fallback to /proc/meminfo Zswap
        pool_total_bytes = zswap_kb * 1024
        try:
            if os.path.exists("/sys/kernel/debug/zswap/pool_total_size"):
                with open("/sys/kernel/debug/zswap/pool_total_size", "r") as f:
                    pool_total_bytes = int(f.read().strip())
        except Exception:
            pass

        # Max allowed pool size in bytes
        max_allowed_pool_bytes = (mem_total_kb * 1024) * (max_pool_pct / 100.0)
        if max_allowed_pool_bytes > 0:
            raw_util = (pool_total_bytes / max_allowed_pool_bytes) * 100.0
            zswap_util_pct = min(100.0, max(0.0, raw_util))
        else:
            zswap_util_pct = 0.0

        avail_ram_pct = (mem_avail_kb / mem_total_kb) * 100.0

        return {
            "mem_total_mb": mem_total_kb // 1024,
            "mem_avail_mb": mem_avail_kb // 1024,
            "avail_ram_pct": avail_ram_pct,
            "pool_total_mb": pool_total_bytes / (1024 * 1024),
            "zswap_util_pct": zswap_util_pct,
            "max_pool_pct": max_pool_pct,
            "zswap_kb": zswap_kb,
            "zswapped_kb": zswapped_kb
        }

    def process_probe_sample(self, store_count, reject_total):
        now = time.time()
        self.history.append((now, store_count, reject_total))

        # Evict samples older than window_sec
        while self.history and self.history[0][0] < now - self.window_sec:
            self.history.popleft()

        # Calculate rolling rates
        total_stores = sum(item[1] for item in self.history)
        total_rejects = sum(item[2] for item in self.history)
        
        duration = max(1.0, self.history[-1][0] - self.history[0][0]) if len(self.history) > 1 else 1.0

        store_rate = total_stores / duration
        reject_rate = total_rejects / duration

        sys_metrics = self.get_sys_memory_metrics()

        return {
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "store_rate": store_rate,
            "reject_rate": reject_rate,
            "zswap_util_pct": sys_metrics["zswap_util_pct"],
            "avail_ram_pct": sys_metrics["avail_ram_pct"],
            "max_pool_pct": sys_metrics["max_pool_pct"],
            "pool_total_mb": sys_metrics["pool_total_mb"],
            "mem_avail_mb": sys_metrics["mem_avail_mb"]
        }

def run_standalone(window_sec=5):
    # If no pipe, launch zswap_probe.py directly
    script_dir = os.path.dirname(os.path.abspath(__file__))
    probe_path = os.path.join(script_dir, "zswap_probe.py")

    cmd = ["sudo", "python3", probe_path]
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)

    agg = TelemetryAggregator(window_sec=window_sec)
    print(f"# Telemetry Aggregator started (rolling window = {window_sec}s)")
    print(f"# {'Timestamp':<20} | {'Store Rate':<12} | {'Reject Rate':<12} | {'Zswap Util %':<14} | {'Avail RAM %':<12} | {'Max Pool %':<10}", flush=True)

    try:
        for line in proc.stdout:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            parts = [p.strip() for p in line.split(",")]
            if len(parts) >= 3:
                try:
                    store_count = int(parts[1])
                    reject_total = int(parts[2])
                    metrics = agg.process_probe_sample(store_count, reject_total)
                    print(f"{metrics['timestamp']:<20} | {metrics['store_rate']:>10.1f}/s | {metrics['reject_rate']:>10.1f}/s | {metrics['zswap_util_pct']:>12.2f}% | {metrics['avail_ram_pct']:>10.1f}% | {metrics['max_pool_pct']:>9d}%", flush=True)
                except ValueError:
                    continue
    except KeyboardInterrupt:
        pass
    finally:
        proc.terminate()

def run_piped(window_sec=5):
    agg = TelemetryAggregator(window_sec=window_sec)
    print(f"# Telemetry Aggregator started (rolling window = {window_sec}s)")
    print(f"# {'Timestamp':<20} | {'Store Rate':<12} | {'Reject Rate':<12} | {'Zswap Util %':<14} | {'Avail RAM %':<12} | {'Max Pool %':<10}", flush=True)

    try:
        for line in sys.stdin:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            parts = [p.strip() for p in line.split(",")]
            if len(parts) >= 3:
                try:
                    store_count = int(parts[1])
                    reject_total = int(parts[2])
                    metrics = agg.process_probe_sample(store_count, reject_total)
                    print(f"{metrics['timestamp']:<20} | {metrics['store_rate']:>10.1f}/s | {metrics['reject_rate']:>10.1f}/s | {metrics['zswap_util_pct']:>12.2f}% | {metrics['avail_ram_pct']:>10.1f}% | {metrics['max_pool_pct']:>9d}%", flush=True)
                except ValueError:
                    continue
    except (KeyboardInterrupt, BrokenPipeError, IOError):
        pass

if __name__ == "__main__":
    try:
        import signal
        signal.signal(signal.SIGPIPE, signal.SIG_DFL)
    except Exception:
        pass
    window = 5
    if len(sys.argv) > 1:
        try:
            window = int(sys.argv[1])
        except ValueError:
            pass
    if sys.stdin.isatty():
        run_standalone(window_sec=window)
    else:
        run_piped(window_sec=window)
