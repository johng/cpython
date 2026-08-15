#include <stdio.h>
#include <stdint.h>
#include <time.h>

static inline uint64_t now_ns(void){
    struct timespec ts; clock_gettime(CLOCK_MONOTONIC, &ts);
    return (uint64_t)ts.tv_sec*1000000000ull + ts.tv_nsec;
}
#define N 20000000ull
#define BENCH(label, stmt) do { \
    uint64_t t0=now_ns(); \
    for (uint64_t i=0;i<N;i++){ stmt; } \
    uint64_t t1=now_ns(); \
    printf("%-16s %7.3f ns/iter\n", label, (double)(t1-t0)/N); \
} while(0)

int main(void){
    for (uint64_t i=0;i<N;i++){ __asm__ volatile("" ::: "memory"); } // warmup
    BENCH("empty",   __asm__ volatile("" ::: "memory"));
    BENCH("nop",     __asm__ volatile("nop" ::: "memory"));
    BENCH("yield",   __asm__ volatile("yield" ::: "memory"));   // ARM YIELD hint
    BENCH("isb",     __asm__ volatile("isb sy" ::: "memory"));
    BENCH("wfe",     __asm__ volatile("wfe"));                  // the PR's aarch64 path
    return 0;
}
