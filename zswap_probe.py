#!/usr/bin/env python3
import sys
import time
import os
import json
from bcc import BPF

# eBPF C Program
bpf_source = """
#include <uapi/linux/ptrace.h>

enum event_type {
    EV_STORE_ATTEMPT = 1,
    EV_STORE_SUCCESS = 2,
    EV_STORE_REJECT  = 3,
};

BPF_HASH(zswap_stats, u32, u64);

int trace_zswap_store_entry(struct pt_regs *ctx) {
    u32 key = EV_STORE_ATTEMPT;
    u64 *val = zswap_stats.lookup(&key);
    if (val) {
        (*val)++;
    } else {
        u64 init_val = 1;
        zswap_stats.update(&key, &init_val);
    }
    return 0;
}

int trace_zswap_store_return(struct pt_regs *ctx) {
    int ret = PT_REGS_RC(ctx);
    u32 key = (ret != 0) ? EV_STORE_SUCCESS : EV_STORE_REJECT;
    u64 *val = zswap_stats.lookup(&key);
    if (val) {
        (*val)++;
    } else {
        u64 init_val = 1;
        zswap_stats.update(&key, &init_val);
    }
    return 0;
}
"""

DEBUG_FS_PATH = "/sys/kernel/debug/zswap"
REJECT_FILES = [
    "pool_limit_hit",
    "reject_alloc_fail",
    "reject_compress_fail",
    "reject_compress_poor",
    "reject_kmemcache_fail",
    "reject_reclaim_fail"
]

def read_debugfs_rejects():
    counts = {}
    if not os.path.exists(DEBUG_FS_PATH):
        return {f.replace("reject_", ""): 0 for f in REJECT_FILES}
    for rf in REJECT_FILES:
        filepath = os.path.join(DEBUG_FS_PATH, rf)
        short_name = rf.replace("reject_", "")
        try:
            with open(filepath, "r") as f:
                counts[short_name] = int(f.read().strip())
        except Exception:
            counts[short_name] = 0
    return counts

def main():
    try:
        b = BPF(text=bpf_source)
        b.attach_kprobe(event="zswap_store", fn_name="trace_zswap_store_entry")
        b.attach_kretprobe(event="zswap_store", fn_name="trace_zswap_store_return")
    except Exception as e:
        sys.stderr.write(f"Error initializing eBPF probe: {e}\n")
        sys.exit(1)

    zswap_stats = b.get_table("zswap_stats")

    prev_bpf = {1: 0, 2: 0, 3: 0} # 1: attempts, 2: success, 3: reject
    prev_rejects = read_debugfs_rejects()

    # Print header
    print("# timestamp, store_count, reject_total, reject_breakdown", flush=True)

    while True:
        try:
            time.sleep(1.0)
            now_str = time.strftime("%Y-%m-%dT%H:%M:%S")

            # Collect current BPF counts
            curr_bpf = {1: 0, 2: 0, 3: 0}
            for k, v in zswap_stats.items():
                curr_bpf[k.value] = v.value

            delta_store = curr_bpf[2] - prev_bpf[2]
            delta_reject_bpf = curr_bpf[3] - prev_bpf[3]
            prev_bpf = curr_bpf

            # Collect debugfs reject counters
            curr_rejects = read_debugfs_rejects()
            delta_rejects = {}
            total_reject_delta = 0
            for k, v in curr_rejects.items():
                d = max(0, v - prev_rejects.get(k, 0))
                delta_rejects[k] = d
            # Total rejects
            reject_total = max(delta_reject_bpf, total_reject_delta)

            breakdown_str = json.dumps(delta_rejects, separators=(',', ':'))
            try:
                print(f"{now_str}, {delta_store}, {reject_total}, {breakdown_str}", flush=True)
            except (BrokenPipeError, IOError):
                sys.exit(0)
        except (KeyboardInterrupt, BrokenPipeError, IOError):
            sys.exit(0)
        except Exception as e:
            sys.stderr.write(f"Probe loop error: {e}\n")
            time.sleep(1.0)

if __name__ == "__main__":
    try:
        # Ignore SIGPIPE to avoid unhandled python exceptions
        signal.signal(signal.SIGPIPE, signal.SIG_DFL)
    except Exception:
        pass
    main()
