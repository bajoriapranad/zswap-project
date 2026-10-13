# Workload-Aware Adaptive Zswap Pool Resizing Using eBPF

Operating Systems Project (BCSE303P), School of Computer Science Engineering, Vellore Institute of Technology, Vellore.
Faculty: Dr. Perepi Rajarajeswari

**Team**
- Pranad Bajoria (24BAI0352)
- Rishi Mishra (24BCT0333)
- Yug Shah (24BCE0286)

## About the Project

Linux zswap keeps compressed swap pages in a RAM pool whose maximum size is set by the `max_pool_percent` parameter. This parameter can be changed at runtime, but zswap does not adapt it to the current workload.

This project is a closed-loop controller that adjusts `max_pool_percent` at runtime. An eBPF-based telemetry subsystem instruments the `zswap_store` path with a kprobe and a kretprobe. The statistics are aggregated in a five-second window and evaluated every second. The controller keeps the value within a 5% to 40% range, without modifying or rebuilding the Linux kernel.

## How It Works

The system has three levels:

1. **Kernel instrumentation:** eBPF probes (BCC) attached to `zswap_store` count store attempts and rejections in a BPF hash map.
2. **User-space telemetry:** a Python aggregator reads the BPF map, keeps a sliding five-second window, and reads `/proc/meminfo` and zswap statistics from debugfs.
3. **Control layer:** a Python daemon evaluates the telemetry once per second and writes the new value to `/sys/module/zswap/parameters/max_pool_percent`.

**Control policy**

| Parameter | Value |
|---|---|
| Rejection threshold | 1 event/s |
| Available-RAM guard | 3% |
| Step size | 5 percentage points |
| Lower bound | 5% |
| Upper bound | 40% |
| Contraction rule | Pool utilisation below 50% for at least 60 s |

- **Expand:** when the rejection rate is above 1 event/s and available RAM is above 3%, the limit is raised by one step.
- **Contract:** only after utilisation stays below 50% for at least 60 seconds, the limit is lowered by one step.

## Repository Contents

```
zswap_project/
├── zswap_probe.py           # In-kernel eBPF probe (BCC / C + Python)
├── telemetry_aggregator.py  # Sliding-window rate aggregator & sysfs reader (Python)
├── controller_daemon.py     # Closed-loop feedback controller & sysfs actuator (Python)
├── load_generator.c         # Incompressible high-entropy memory stress tool (C)
├── dynamic_load_tester.py   # Real-time interactive memory stress allocator (Python)
├── experiment_runner.py     # Automated A/B baseline vs. adaptive benchmark harness (Python)
├── demo_framework.py        # Terminal evaluation dashboard & menu (Python)
└── run_demo.sh              # One-click execution launcher (Bash)
```

| File | Description |
|---|---|
| `zswap_probe.py` | eBPF probe written with the BPF Compiler Collection (BCC). A kprobe records entry to `zswap_store`, and a kretprobe uses the return value to decide whether the store succeeded. |
| `telemetry_aggregator.py` | Reads the BPF map counters, maintains the five-second sliding window, and computes the store rate and reject rate. |
| `controller_daemon.py` | Applies the expansion and contraction policy every second and writes the new value to sysfs. |
| `load_generator.c` | C workload generator compiled with GCC `-O3`. Fills memory with high-entropy data from the xorshift64 generator so pages are hard to compress. |
| `dynamic_load_tester.py` | Interactive tool to add and free memory in real time while the controller runs. |
| `experiment_runner.py` | Runs the static 3% baseline and the adaptive configuration and collects the results. |
| `demo_framework.py` | Terminal dashboard and menu for the live demonstration. |
| `run_demo.sh` | Bash launcher that starts the demonstration. |

## Experimental Setup

- Ubuntu 24.04 LTS, Linux kernel 7.0.0-30-generic
- 13th Generation Intel Core i7, 15,158 MiB RAM, 4 GiB swap on a PCIe Gen4 NVMe SSD
- zswap with the LZO-RLE compressor and zsmalloc allocator
- Initial `max_pool_percent` of 3% for the benchmark runs
- Workload: 10,800 MiB of high-entropy memory, sustained for around 12 seconds, with a 60-second timeout per experiment

## Results

| Metric | Static 3% | Adaptive 3% to 8% |
|---|---|---|
| Maximum pool capacity | about 455 MiB | about 1,213 MiB |
| Pool utilisation | 100% at saturation | about 40% after resize |
| Reject rate | 2,488 events/s (Run 1 peak) | 0 events/s after resize in applicable trials |
| Resize response | Not applicable | 1.8 to 2.0 s |
| CPU overhead | Baseline | below 0.45% of one core |

- Runs 1 and 3 expanded the pool from 3% to 8% and the rejection rate fell to zero.
- In Run 2 the rejection rate stayed at zero, so the controller kept the 3% setting.
- A separate stress demonstration (22,176 events/s) also resulted in a 3% to 8% expansion.
- A 31.4% point-in-time latency reduction was seen in Run 1 but was not reproduced in Runs 2 and 3.

## Limitations

- One machine and one kernel version.
- Only three controlled runs, which is not enough for statistical significance, especially for latency.
- A deliberately high-entropy workload, which does not represent all real applications.
- The aggregate `zswap_store` failure signal can include failures that are not pool-capacity rejections.
- Fixed thresholds and fixed five-percentage-point steps.

## Future Work

- A smoother control algorithm such as a PID controller.
- Moving from BCC to libbpf and CO-RE.
- Per-cgroup control with cgroup-v2 zswap controls.
- A larger study across machines, kernel versions, workloads and trials.

## References

1. Linux Kernel Documentation, "Zswap". https://docs.kernel.org/admin-guide/mm/zswap.html
2. iovisor, "BCC: Tools for BPF-based Linux I/O Analysis, Networking, Monitoring, and More". https://github.com/iovisor/bcc
3. G. Marsaglia, "Xorshift RNGs," Journal of Statistical Software, vol. 8, no. 14, pp. 1-6, 2003, doi: 10.18637/jss.v008.i14.
