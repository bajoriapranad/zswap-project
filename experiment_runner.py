#!/usr/bin/env python3
import sys
import time
import os
import subprocess
import csv
import psutil
import argparse
import signal

def get_zswap_metrics():
    zswap_kb = 0
    with open("/proc/meminfo", "r") as f:
        for line in f:
            if line.startswith("Zswap:"):
                zswap_kb = int(line.split()[1])
                break
    pool_bytes = zswap_kb * 1024
    try:
        if os.path.exists("/sys/kernel/debug/zswap/pool_total_size"):
            with open("/sys/kernel/debug/zswap/pool_total_size", "r") as f:
                pool_bytes = int(f.read().strip())
    except Exception:
        pass
    try:
        with open("/sys/module/zswap/parameters/max_pool_percent", "r") as f:
            max_pool_pct = int(f.read().strip())
    except Exception:
        max_pool_pct = 20
    return pool_bytes / (1024 * 1024), max_pool_pct

def measure_page_fault_and_latency():
    t0 = time.perf_counter()
    b = bytearray(32 * 1024 * 1024)
    for i in range(0, len(b), 4096):
        b[i] = 1
    t1 = time.perf_counter()
    latency_ms = (t1 - t0) * 1000.0
    del b
    return latency_ms

def run_phase(phase_name, target_mb, hold_sec, passes, is_adaptive, start_pool_pct, output_csv, max_phase_timeout=60, daemon_log_path="controller_daemon_live.log"):
    print(f"\n==========================================")
    print(f"Starting: {phase_name} (Adaptive: {is_adaptive}, Start Pool: {start_pool_pct}%)")
    print(f"Target Load: {target_mb} MB | Hold: {hold_sec}s | Passes: {passes} | Max Timeout: {max_phase_timeout}s")
    print(f"==========================================")

    script_dir = os.path.dirname(os.path.abspath(__file__))
    load_bin = os.path.join(script_dir, "load_generator")
    daemon_py = os.path.join(script_dir, "controller_daemon.py")
    daemon_log = os.path.join(script_dir, daemon_log_path)

    # Set initial starting pool
    subprocess.run(f"echo {start_pool_pct} | sudo tee /sys/module/zswap/parameters/max_pool_percent", shell=True, stdout=subprocess.DEVNULL)
    time.sleep(1)

    daemon_proc = None
    daemon_file = None
    if is_adaptive:
        print(f"[+] Starting adaptive controller daemon in LIVE mode (logging to {daemon_log})...")
        daemon_file = open(daemon_log, "a")
        cmd = [
            "sudo", "python3", daemon_py,
            "--live",
            "--interval", "1.0",
            "--threshold-high", "1.0",
            "--step", "5",
            "--min-pool", str(start_pool_pct),
            "--max-pool", "35"
        ]
        daemon_proc = subprocess.Popen(cmd, stdout=daemon_file, stderr=daemon_file)
        time.sleep(2)

    print(f"[+] Launching memory workload ({target_mb} MB)...")
    load_proc = subprocess.Popen([load_bin, str(target_mb), str(hold_sec), str(passes)], stdout=subprocess.PIPE, text=True)

    start_time = time.time()

    with open(output_csv, "a", newline="") as f:
        writer = csv.writer(f)
        while True:
            poll_ret = load_proc.poll()
            now = time.time()
            elapsed = now - start_time

            # Hard timeout protection
            if elapsed > max_phase_timeout:
                print(f"[!] Warning: {phase_name} exceeded hard timeout ({max_phase_timeout}s). Terminating workload.")
                load_proc.terminate()
                time.sleep(1)
                if load_proc.poll() is None:
                    load_proc.kill()
                break

            mem = psutil.virtual_memory()
            swap = psutil.swap_memory()
            pool_mb, max_pool_pct = get_zswap_metrics()
            latency_ms = measure_page_fault_and_latency()

            # Record metrics to CSV
            writer.writerow([
                time.strftime("%Y-%m-%dT%H:%M:%S"),
                phase_name,
                f"{elapsed:.2f}",
                mem.used // (1024 * 1024),
                mem.available // (1024 * 1024),
                swap.used // (1024 * 1024),
                f"{pool_mb:.2f}",
                max_pool_pct,
                f"{latency_ms:.2f}"
            ])
            f.flush()
            print(f"[{phase_name} {elapsed:>5.1f}s] RAM Used: {mem.used//(1024*1024):>5}MB | Avail: {mem.available//(1024*1024):>5}MB | Swap: {swap.used//(1024*1024):>4}MB | Zswap: {pool_mb:>6.1f}MB | Pool: {max_pool_pct}% | Latency: {latency_ms:>5.1f}ms", flush=True)

            if poll_ret is not None:
                break
            time.sleep(1.0)

    print(f"[+] {phase_name} completed.")

    if daemon_proc:
        print("[+] Stopping controller daemon...")
        daemon_proc.terminate()
        try:
            daemon_proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            daemon_proc.kill()
        if daemon_file:
            daemon_file.close()

    # Reset pool to start_pool_pct or 20%
    subprocess.run("echo 20 | sudo tee /sys/module/zswap/parameters/max_pool_percent", shell=True, stdout=subprocess.DEVNULL)
    time.sleep(4)

def main():
    parser = argparse.ArgumentParser(description="Zswap Adaptive vs Baseline Multi-Run Benchmark Harness")
    parser.add_argument("--size-mb", type=int, default=10800, help="Memory footprint in MB (default: 10800 (~71 percent RAM))")
    parser.add_argument("--hold-sec", type=int, default=12, help="Hold duration in seconds (default: 12)")
    parser.add_argument("--passes", type=int, default=1, help="Memory touch passes (default: 1)")
    parser.add_argument("--start-pool", type=int, default=5, help="Starting max_pool_percent to enforce constrained baseline (default: 5)")
    parser.add_argument("--runs", type=int, default=3, help="Number of benchmark iterations (default: 3)")
    parser.add_argument("--out", type=str, default="experiment_results.csv", help="Output CSV path")
    parser.add_argument("--timeout", type=int, default=60, help="Max runtime per phase in seconds (default: 60)")

    args = parser.parse_args()

    script_dir = os.path.dirname(os.path.abspath(__file__))
    out_path = os.path.join(script_dir, args.out)

    # Initialize CSV header
    with open(out_path, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow([
            "timestamp", "phase", "elapsed_sec", "mem_used_mb",
            "mem_avail_mb", "swap_used_mb", "zswap_pool_mb",
            "max_pool_pct", "access_latency_ms"
        ])

    print(f"=== Zswap Adaptive vs Baseline Benchmark ({args.runs} Runs) ===")
    print(f"Target Allocation: {args.size_mb} MB | Hold: {args.hold_sec}s | Start Pool: {args.start_pool}%")
    print(f"Hard Phase Timeout: {args.timeout}s | Telemetry output: {out_path}\n")

    try:
        for r in range(1, args.runs + 1):
            print(f"\n>>>>>>>>>>>>>>> RUN {r} OF {args.runs} <<<<<<<<<<<<<<<")

            # 1. Baseline Phase (Static Constrained Pool)
            run_phase(f"Baseline Run {r} (Static {args.start_pool}%)", args.size_mb, args.hold_sec, args.passes,
                      is_adaptive=False, start_pool_pct=args.start_pool, output_csv=out_path, max_phase_timeout=args.timeout)

            print("\nCooldown pause before adaptive run (6s)...")
            time.sleep(6)

            # 2. Adaptive Phase (Dynamic Pool Resizing)
            run_phase(f"Adaptive Run {r} (Dynamic Pool)", args.size_mb, args.hold_sec, args.passes,
                      is_adaptive=True, start_pool_pct=args.start_pool, output_csv=out_path, max_phase_timeout=args.timeout)

            if r < args.runs:
                print("\nInter-run cooldown pause (8s)...")
                time.sleep(8)

    finally:
        # Guaranteed safe state restoration
        print("\n[+] Final cleanup: Restoring max_pool_percent to 20%...")
        subprocess.run("echo 20 | sudo tee /sys/module/zswap/parameters/max_pool_percent", shell=True, stdout=subprocess.DEVNULL)

    print(f"\n==========================================")
    print(f"All {args.runs} benchmark runs completed successfully!")
    print(f"Dataset saved to: {out_path}")
    print(f"==========================================")

if __name__ == "__main__":
    main()
