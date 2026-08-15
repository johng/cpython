#include <stdio.h>
#include <stdint.h>
#include <time.h>
#include <sched.h>
#include <pthread.h>
#include <stdatomic.h>

static inline uint64_t now_ns(void){
    struct timespec ts; clock_gettime(CLOCK_MONOTONIC, &ts);
    return (uint64_t)ts.tv_sec*1000000000ull + ts.tv_nsec;
}

#define N 2000000ull   // sched_yield is a syscall; fewer iters

static _Atomic int g_spin_stop = 0;
static void* busy(void* a){ (void)a; volatile uint64_t x=0; while(!atomic_load(&g_spin_stop)) x++; return 0; }

int main(void){
    // --- sched_yield, UNCONTENDED (nothing else runnable) ---
    uint64_t t0=now_ns();
    for (uint64_t i=0;i<N;i++) sched_yield();
    uint64_t t1=now_ns();
    printf("sched_yield (uncontended)      %8.3f ns/call\n", (double)(t1-t0)/N);

    // --- wfe for reference on this machine ---
    t0=now_ns();
    for (uint64_t i=0;i<N;i++) __asm__ volatile("wfe");
    t1=now_ns();
    printf("wfe (this PR's aarch64 path)   %8.3f ns/call\n", (double)(t1-t0)/N);

    // --- sched_yield, CONTENDED: spawn a busy thread so there IS something to yield to ---
    atomic_store(&g_spin_stop,0);
    pthread_t th; pthread_create(&th,0,busy,0);
    t0=now_ns();
    for (uint64_t i=0;i<N;i++) sched_yield();
    t1=now_ns();
    atomic_store(&g_spin_stop,1); pthread_join(th,0);
    printf("sched_yield (1 competitor)     %8.3f ns/call\n", (double)(t1-t0)/N);

    return 0;
}
