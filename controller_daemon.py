#!/usr/bin/env python3
import sys
import time
import os
import argparse
import subprocess
from telemetry_aggregator import TelemetryAggregator

class ZswapController:
    def __init__(self, dry_run=True, eval_interval=1.0, threshold_high=1.0,
                 min_pool=5, max_pool=40, step=5, low_util_duration=60.0):
        self.dry_run = dry_run
        self.eval_interval = eval_interval
        self.threshold_high = threshold_high
        self.min_pool = min_pool
        self.max_pool = max_pool
        self.step = step
        self.low_util_duration = low_util_duration

        self.low_util_start_time = None
        self.last_eval_time = time.time()
        self.aggregator = TelemetryAggregator(window_sec=int(eval_interval) if int(eval_interval) >= 1 else 1)

    def get_current_max_pool(self):
        try:
            with open("/sys/module/zswap/parameters/max_pool_percent", "r") as f:
                return int(f.read().strip())
        except Exception as e:
            sys.stderr.write(f"Failed to read max_pool_percent: {e}\n")
            return 20

    def set_max_pool(self, new_val, reason):
        curr_val = self.get_current_max_pool()
        if new_val == curr_val:
            return

        # Enforce hard safety bounds [5, 40]
        bounded_val = max(self.min_pool, min(self.max_pool, new_val))
        if bounded_val < 5 or bounded_val > 40:
            sys.stderr.write(f"CRITICAL SAFETY VIOLATION: {bounded_val} outside [5, 40]. Aborting write.\n")
            return
        if bounded_val == curr_val:
            return

        action_prefix = "[DRY-RUN]" if self.dry_run else "[ACTION]"
        log_msg = f"{time.strftime('%Y-%m-%dT%H:%M:%S')} {action_prefix} Adjust max_pool_percent: {curr_val}% -> {bounded_val}% (Reason: {reason})"
        print(log_msg, flush=True)

        # Write to dedicated action log file
        try:
            log_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), "controller_actions.log")
            with open(log_file, "a") as lf:
                lf.write(log_msg + "\n")
        except Exception as e:
            sys.stderr.write(f"Failed writing to action log file: {e}\n")

        if not self.dry_run:
            try:
                with open("/sys/module/zswap/parameters/max_pool_percent", "w") as f:
                    f.write(str(bounded_val))
            except PermissionError:
                subprocess.run(
                    f"echo {bounded_val} | sudo tee /sys/module/zswap/parameters/max_pool_percent",
                    shell=True,
                    check=True,
                    stdout=subprocess.DEVNULL
                )
            except Exception as e:
                sys.stderr.write(f"Failed to set max_pool_percent: {e}\n")

    def evaluate_policy(self, metrics):
        now = time.time()
        if now - self.last_eval_time < self.eval_interval:
            return

        self.last_eval_time = now
        curr_pool = self.get_current_max_pool()
        reject_rate = metrics["reject_rate"]
        avail_ram_pct = metrics["avail_ram_pct"]
        zswap_util_pct = metrics["zswap_util_pct"]

        # Track low utilization timer
        if zswap_util_pct < 50.0:
            if self.low_util_start_time is None:
                self.low_util_start_time = now
        else:
            self.low_util_start_time = None

        low_util_elapsed = (now - self.low_util_start_time) if self.low_util_start_time else 0.0

        print(f"{metrics['timestamp']} [STATUS] RejRate={reject_rate:.1f}/s, AvailRAM={avail_ram_pct:.1f}%, ZswapUtil={zswap_util_pct:.1f}%, Pool={curr_pool}%, LowUtilTimer={low_util_elapsed:.0f}s/{self.low_util_duration:.0f}s", flush=True)

        # Rule 1: High rejects AND available RAM > 3.0% (safe headroom) -> increase pool
        if reject_rate > self.threshold_high and avail_ram_pct > 3.0:
            if curr_pool < self.max_pool:
                target_pool = min(self.max_pool, curr_pool + self.step)
                self.set_max_pool(target_pool, f"reject_rate ({reject_rate:.1f}/s) > {self.threshold_high} and avail_RAM ({avail_ram_pct:.1f}%) > 3.0%")
                self.low_util_start_time = None
            else:
                print(f"{metrics['timestamp']} [INFO] Max ceiling of {self.max_pool}% reached, cannot increase further.", flush=True)

        # Rule 2: Low utilization (<50%) for low_util_duration -> decrease pool
        elif low_util_elapsed >= self.low_util_duration:
            if curr_pool > self.min_pool:
                target_pool = max(self.min_pool, curr_pool - self.step)
                self.set_max_pool(target_pool, f"zswap_utilization ({zswap_util_pct:.1f}%) < 50% for {low_util_elapsed:.0f}s")
                self.low_util_start_time = now
            else:
                print(f"{metrics['timestamp']} [INFO] Min floor of {self.min_pool}% reached, cannot decrease further.", flush=True)

def main():
    parser = argparse.ArgumentParser(description="Adaptive Zswap Pool Controller Daemon")
    parser.add_argument("--dry-run", action="store_true", default=True, help="Simulate actions without writing to sysfs (default: True)")
    parser.add_argument("--live", action="store_true", help="Enable real writes to sysfs")
    parser.add_argument("--interval", type=float, default=1.0, help="Policy evaluation interval in seconds (default: 1.0)")
    parser.add_argument("--threshold-high", type=float, default=1.0, help="High reject rate threshold in events/sec (default: 1.0)")
    parser.add_argument("--min-pool", type=int, default=5, help="Minimum max_pool_percent floor (default: 5)")
    parser.add_argument("--max-pool", type=int, default=40, help="Maximum max_pool_percent ceiling (default: 40)")
    parser.add_argument("--step", type=int, default=5, help="Step size for pool adjustment (default: 5)")
    parser.add_argument("--low-util-duration", type=float, default=60.0, help="Seconds under 50 percent util before shrinking (default: 60.0)")
    parser.add_argument("--pipe-in", action="store_true", help="Read probe metrics from stdin instead of spawning probe subprocess")

    args = parser.parse_args()
    is_dry_run = not args.live

    controller = ZswapController(
        dry_run=is_dry_run,
        eval_interval=args.interval,
        threshold_high=args.threshold_high,
        min_pool=args.min_pool,
        max_pool=args.max_pool,
        step=args.step,
        low_util_duration=args.low_util_duration
    )

    mode_str = "DRY-RUN (no sysfs writes)" if is_dry_run else "LIVE (sysfs writes active)"
    print(f"=== Zswap Controller Daemon Starting ===")
    print(f"Mode: {mode_str} | Eval Interval: {args.interval}s | Reject Threshold: {args.threshold_high}/s")
    print(f"Bounds: [{args.min_pool}%, {args.max_pool}%] with Step: {args.step}% | Low-util timer: {args.low_util_duration}s", flush=True)

    # If --pipe-in is not specified, launch zswap_probe.py internally
    if not args.pipe_in:
        script_dir = os.path.dirname(os.path.abspath(__file__))
        probe_path = os.path.join(script_dir, "zswap_probe.py")
        proc = subprocess.Popen(["sudo", "python3", probe_path], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        input_stream = proc.stdout
    else:
        proc = None
        input_stream = sys.stdin

    try:
        for line in input_stream:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            parts = [p.strip() for p in line.split(",")]
            if len(parts) >= 3:
                try:
                    store_count = int(parts[1])
                    reject_total = int(parts[2])
                    metrics = controller.aggregator.process_probe_sample(store_count, reject_total)
                    controller.evaluate_policy(metrics)
                except ValueError:
                    continue
    except KeyboardInterrupt:
        print("\nStopping controller daemon...", flush=True)
    finally:
        if proc:
            proc.terminate()

if __name__ == "__main__":
    main()
