#define _GNU_SOURCE
#include <stdio.h>
#include <stdlib.h>
#include <stdint.h>
#include <string.h>
#include <unistd.h>
#include <time.h>
#include <sys/time.h>
#include <sys/sysinfo.h>

static double get_time_sec(void) {
    struct timeval tv;
    gettimeofday(&tv, NULL);
    return tv.tv_sec + tv.tv_usec * 1e-6;
}

// Fast high-entropy 64-bit PRNG (Xorshift64*)
static inline uint64_t xorshift64star(uint64_t *state) {
    uint64_t x = *state;
    x ^= x >> 12;
    x ^= x << 25;
    x ^= x >> 27;
    *state = x;
    return x * 0x2545F4914F6CDD1DULL;
}

int main(int argc, char *argv[]) {
    struct sysinfo si;
    sysinfo(&si);
    unsigned long total_ram_mb = (si.totalram * si.mem_unit) / (1024 * 1024);
    
    // Default to ~72% of total RAM
    unsigned long target_mb = (unsigned long)(total_ram_mb * 0.72);
    int hold_sec = 10;
    int passes = 1;

    if (argc >= 2) {
        target_mb = strtoul(argv[1], NULL, 10);
    }
    if (argc >= 3) {
        hold_sec = atoi(argv[2]);
    }
    if (argc >= 4) {
        passes = atoi(argv[3]);
    }

    printf("=== High-Entropy Incompressible Memory Load Generator ===\n");
    printf("Total System RAM: %lu MB\n", total_ram_mb);
    printf("Target Allocation: %lu MB (%.1f%% of total RAM)\n", 
           target_mb, ((double)target_mb / total_ram_mb) * 100.0);
    printf("Hold Duration: %d seconds | Passes: %d\n", hold_sec, passes);
    printf("Data Pattern: Pseudo-Random High-Entropy (Incompressible Payload)\n");

    size_t bytes = (size_t)target_mb * 1024 * 1024;
    double t_start = get_time_sec();

    printf("[1/3] Allocating %lu MB virtual memory...\n", target_mb);
    uint64_t *buf = (uint64_t *)malloc(bytes);
    if (!buf) {
        perror("malloc failed");
        return 1;
    }

    size_t num_uint64 = bytes / sizeof(uint64_t);
    size_t u64_per_page = 4096 / sizeof(uint64_t); // 512 uint64s per 4KB page

    printf("[2/3] Generating random data & dirtying all pages (page size = 4KB)...\n");
    for (int p = 0; p < passes; p++) {
        double p_start = get_time_sec();
        uint64_t rng_state = 0x853c49e6748fea9bULL ^ ((uint64_t)p * 0x9e3779b97f4a7c15ULL);

        for (size_t i = 0; i < num_uint64; i += u64_per_page) {
            // Fill each 4KB page with high-entropy pseudo-random words
            for (size_t j = 0; j < u64_per_page; j++) {
                buf[i + j] = xorshift64star(&rng_state);
            }
            if (((i * sizeof(uint64_t)) % (512 * 1024 * 1024)) == 0 && i > 0) {
                printf("  -> Written %zu MB / %lu MB (Pass %d/%d)\n", 
                       (i * sizeof(uint64_t)) / (1024 * 1024), target_mb, p + 1, passes);
                fflush(stdout);
            }
        }
        double p_elapsed = get_time_sec() - p_start;
        printf("  Pass %d completed in %.2f seconds (throughput: %.1f MB/s)\n",
               p + 1, p_elapsed, (double)target_mb / p_elapsed);
    }

    double alloc_touch_time = get_time_sec() - t_start;
    printf("[3/3] Total allocation & random fill time: %.2f seconds. Holding memory for %d seconds...\n",
           alloc_touch_time, hold_sec);
    fflush(stdout);

    sleep(hold_sec);

    printf("Freeing memory...\n");
    free(buf);
    printf("Workload finished successfully.\n");
    return 0;
}
