#!/usr/bin/env python3
"""
Dynamic Memory Hike Tester with Live ASCII Graph & Real-Time Adaptive Feedback
Renders a dynamic live chart that updates instantly as memory is allocated/freed.
"""

import sys
import os
import time
import subprocess
import psutil
import select
import termios
import tty
from collections import deque

# ANSI Colors
C_RESET = "\033[0m"
C_BOLD = "\033[1m"
C_DIM = "\033[2m"
C_RED = "\033[31m"
C_GREEN = "\033[32m"
C_YELLOW = "\033[33m"
C_BLUE = "\033[34m"
C_MAGENTA = "\033[35m"
C_CYAN = "\033[36m"
C_WHITE = "\033[37m"

CHUNK_SIZE_MB = 1024  # 1 GB per chunk
HISTORY_LEN = 35

class DynamicLoadManager:
    def __init__(self):
        self.chunks = []
        self.total_allocated_mb = 0

    def allocate_chunk(self):
        try:
            size_bytes = CHUNK_SIZE_MB * 1024 * 1024
            chunk = bytearray(size_bytes)
            seed = int(time.time() * 1000) % 255
            for i in range(0, size_bytes, 4096):
                chunk[i] = (seed + (i & 0xFF)) % 256
                chunk[i + 1] = ((seed >> 2) ^ (i >> 8)) & 0xFF
            self.chunks.append(chunk)
            self.total_allocated_mb += CHUNK_SIZE_MB
            return True
        except MemoryError:
            return False

    def free_chunk(self):
        if self.chunks:
            chunk = self.chunks.pop()
            del chunk
            self.total_allocated_mb = max(0, self.total_allocated_mb - CHUNK_SIZE_MB)
            return True
        return False

    def free_all(self):
        self.chunks.clear()
        self.total_allocated_mb = 0

def get_live_metrics():
    zswap_kb = 0
    zswapped_kb = 0
    with open("/proc/meminfo") as f:
        for line in f:
            if line.startswith("Zswap:"):
                zswap_kb = int(line.split()[1])
            elif line.startswith("Zswapped:"):
                zswapped_kb = int(line.split()[1])
    try:
        with open("/sys/module/zswap/parameters/max_pool_percent") as f:
            pool_pct = int(f.read().strip())
    except Exception:
        pool_pct = 20
    return zswap_kb / 1024.0, zswapped_kb / 1024.0, pool_pct

def make_bar(percent, width=14, color=C_GREEN):
    clamped = max(0.0, min(100.0, float(percent)))
    fill = int(width * (clamped / 100.0))
    empty = width - fill
    return f"{color}{'█' * fill}{C_DIM}{'░' * empty}{C_RESET} {clamped:>5.1f}%"

def render_ascii_graph(history_alloc_mb, history_pool_pct, total_ram_mb, height=6, width=35):
    """Render a dynamic 2D ASCII waveform graph of memory load & pool percentage."""
    lines = []
    # Normalize alloc data to [0, height-1]
    alloc_vals = list(history_alloc_mb)
    pool_vals = list(history_pool_pct)

    # Pad if needed
    if len(alloc_vals) < width:
        alloc_vals = [0] * (width - len(alloc_vals)) + alloc_vals
    if len(pool_vals) < width:
        pool_vals = [pool_vals[0] if pool_vals else 20] * (width - len(pool_vals)) + pool_vals

    max_mb = max(total_ram_mb, 1)

    grid = [[" " for _ in range(width)] for _ in range(height)]

    for x in range(width):
        # Plot Alloc MB (Cyan)
        y_alloc = int((alloc_vals[x] / max_mb) * (height - 1))
        y_alloc = min(height - 1, max(0, y_alloc))
        for row in range(y_alloc + 1):
            grid[height - 1 - row][x] = f"{C_CYAN}█{C_RESET}"

        # Plot Pool % top marker (Yellow)
        y_pool = int((pool_vals[x] / 40.0) * (height - 1))
        y_pool = min(height - 1, max(0, y_pool))
        grid[height - 1 - y_pool][x] = f"{C_YELLOW}●{C_RESET}"

    # Build ASCII chart with Y-axis labels
    for row in range(height):
        label_mb = int(((height - 1 - row) / (height - 1)) * (max_mb / 1024))
        line_str = "".join(grid[row])
        lines.append(f"  {label_mb:>2}GB ┤ {line_str}")
    lines.append(f"   0GB └{'─' * width}")
    return "\n".join(lines)

def run_interactive_mode():
    manager = DynamicLoadManager()
    
    # Start starting pool at 3% for demonstration
    subprocess.run("echo 3 | sudo tee /sys/module/zswap/parameters/max_pool_percent 2>/dev/null || true", shell=True, stdout=subprocess.DEVNULL)

    # History buffers for dynamic graph
    history_alloc = deque(maxlen=HISTORY_LEN)
    history_pool = deque(maxlen=HISTORY_LEN)
    event_logs = deque(maxlen=3)
    event_logs.append(f"{C_DIM}[Init] Interactive Controller Started. Base pool set to 3%.{C_RESET}")

    old_settings = termios.tcgetattr(sys.stdin)
    tty.setcbreak(sys.stdin.fileno())

    print("\033[?25l", end="")  # Hide cursor
    os.system("clear")

    prev_pool = 3
    last_action_time = time.time()

    try:
        while True:
            # Check keyboard input (non-blocking)
            r, _, _ = select.select([sys.stdin], [], [], 0.25)
            if r:
                key = sys.stdin.read(1)
                if key in ['+', '=', 'k', 'A']:
                    if manager.allocate_chunk():
                        event_logs.append(f"{C_GREEN}[+]{C_RESET} Allocated +1 GB dirty memory (Total: {manager.total_allocated_mb} MB)")
                elif key in ['-', '_', 'j', 'B']:
                    if manager.free_chunk():
                        event_logs.append(f"{C_YELLOW}[-]{C_RESET} Freed -1 GB memory (Total: {manager.total_allocated_mb} MB)")
                elif key.lower() == 'c':
                    manager.free_all()
                    event_logs.append(f"{C_RED}[C]{C_RESET} Cleared all allocated memory chunks")
                elif key.lower() == 'a':
                    event_logs.append(f"{C_CYAN}[Auto]{C_RESET} Running automated staircase ramp (1 to 10 GB)...")
                    for _ in range(8):
                        manager.allocate_chunk()
                        time.sleep(0.3)
                elif key.lower() == 'q':
                    break

            mem = psutil.virtual_memory()
            total_ram_mb = mem.total // (1024 * 1024)
            avail_ram_mb = mem.available // (1024 * 1024)
            zswap_mb, zswapped_mb, pool_pct = get_live_metrics()

            # Dynamic adaptive simulation policy
            # If allocated memory > 7,000 MB and pool is < 25%, trigger resize
            now = time.time()
            if manager.total_allocated_mb >= 7000 and pool_pct < 20 and (now - last_action_time > 2.0):
                target_pool = min(35, pool_pct + 5)
                subprocess.run(f"echo {target_pool} | sudo tee /sys/module/zswap/parameters/max_pool_percent 2>/dev/null || true", shell=True, stdout=subprocess.DEVNULL)
                event_logs.append(f"{C_YELLOW}⚡ [ADAPTIVE ACTION]{C_RESET} Memory Hike Pressure: Resized max_pool_percent {pool_pct}% ➔ {target_pool}%")
                last_action_time = now
            elif manager.total_allocated_mb < 2000 and pool_pct > 5 and (now - last_action_time > 5.0):
                target_pool = max(3, pool_pct - 5)
                subprocess.run(f"echo {target_pool} | sudo tee /sys/module/zswap/parameters/max_pool_percent 2>/dev/null || true", shell=True, stdout=subprocess.DEVNULL)
                event_logs.append(f"{C_CYAN}📉 [SHRINK ACTION]{C_RESET} Low Pressure: Shrunk max_pool_percent {pool_pct}% ➔ {target_pool}%")
                last_action_time = now

            # Append to dynamic graph history
            history_alloc.append(manager.total_allocated_mb)
            history_pool.append(pool_pct)

            alloc_pct = (manager.total_allocated_mb / total_ram_mb) * 100 if total_ram_mb else 0
            avail_pct = (avail_ram_mb / total_ram_mb) * 100 if total_ram_mb else 0

            # Render Screen
            os.system("clear")
            print(f"{C_CYAN}{C_BOLD}┌────────────────────────────────────────────────────────────────────────┐{C_RESET}")
            print(f"{C_CYAN}{C_BOLD}│             LIVE DYNAMIC MEMORY HIKE & REAL-TIME GRAPH                 │{C_RESET}")
            print(f"{C_CYAN}{C_BOLD}└────────────────────────────────────────────────────────────────────────┘{C_RESET}")
            print(f"  {C_BOLD}Controls:{C_RESET} {C_GREEN}[+]{C_RESET} Add 1GB  │  {C_RED}[-]{C_RESET} Free 1GB  │  {C_YELLOW}[A]{C_RESET} Auto Ramp  │  {C_CYAN}[C]{C_RESET} Clear  │  {C_WHITE}[Q]{C_RESET} Exit\n")

            # Status Gauges
            print(f"  {C_BOLD}Allocated Load:{C_RESET}   {make_bar(alloc_pct, 16, C_CYAN)}  {manager.total_allocated_mb:>5} MB / {total_ram_mb} MB")
            print(f"  {C_BOLD}Available RAM:{C_RESET}    {make_bar(avail_pct, 16, C_GREEN if avail_pct > 15 else C_RED)}  {avail_ram_mb:>5} MB")
            ratio = (zswapped_mb / zswap_mb) if zswap_mb > 0 else 1.0
            print(f"  {C_BOLD}Zswap In RAM:{C_RESET}     {zswap_mb:>6.1f} MB  (Original Data: {zswapped_mb:.1f} MB | Ratio: {ratio:.2f}x)")
            print(f"  {C_BOLD}Sysfs Pool Limit:{C_RESET} {C_YELLOW}{C_BOLD}{pool_pct}%{C_RESET}  {C_DIM}(Adaptive Feedback Controller: ACTIVE){C_RESET}\n")

            # Dynamic ASCII Waveform Chart
            print(f"  {C_BOLD}Real-Time Memory Hike & Pool Cap Curve:{C_RESET} {C_CYAN}█ Load MB{C_RESET} │ {C_YELLOW}● Pool Limit %{C_RESET}")
            print(render_ascii_graph(history_alloc, history_pool, total_ram_mb, height=6, width=HISTORY_LEN))

            # Event Log Ticker
            print(f"\n  {C_BOLD}Real-Time Controller Action Stream:{C_RESET}")
            for log in event_logs:
                print(f"    ↳ {log}")

    finally:
        termios.tcsetattr(sys.stdin, termios.TCSADRAIN, old_settings)
        print("\033[?25h", end="")  # Show cursor
        manager.free_all()
        # Restore safe pool
        subprocess.run("echo 20 | sudo tee /sys/module/zswap/parameters/max_pool_percent 2>/dev/null || true", shell=True, stdout=subprocess.DEVNULL)
        print(f"\n{C_GREEN}✔ Memory released and max_pool_percent restored to 20%.{C_RESET}\n")

if __name__ == "__main__":
    run_interactive_mode()
