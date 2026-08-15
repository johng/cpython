#define _GNU_SOURCE
#include <stdio.h>
#include <stdint.h>
#include <time.h>
#include <sched.h>
#include <pthread.h>
#include <stdatomic.h>
#include <x86intrin.h>

static inline uint64_t now_ns(void){
    struct timespec ts; clock_gettime(CLOCK_MONOTONIC, &ts);
    return (uint64_t)ts.tv_sec*1000000000ull + ts.tv_nsec;
}

static _Atomic int g_stop = 0;
static void* busy(void* a){ (void)a; volatile uint64_t x=0; while(!atomic_load(&g_stop)) x++; return 0; }

int main(void){
    const uint64_t Nfast = 50000000ull;   // pause: cheap
    const uint64_t Nsy   = 1000000ull;     // sched_yield: syscall

    // warmup
    for (uint64_t i=0;i<Nfast;i++) __asm__ volatile("" ::: "memory");

    // empty
    uint64_t t0=now_ns();
    for (uint64_t i=0;i<Nfast;i++) __asm__ volatile("" ::: "memory");
    uint64_t t1=now_ns();
    double empty_ns=(double)(t1-t0)/Nfast;
    printf("empty                       %8.3f ns/iter\n", empty_ns);

    // pause (the PR's x86 path)  -- ns + cycles via rdtscp
    t0=now_ns();
    unsigned aux; uint64_t c0=__rdtscp(&aux);
    for (uint64_t i=0;i<Nfast;i++) __asm__ volatile("pause" ::: "memory");
    uint64_t c1=__rdtscp(&aux);
    t1=now_ns();
    printf("pause (PR x86 path)         %8.3f ns/iter   %6.1f tsc-cyc/iter\n",
           (double)(t1-t0)/Nfast, (double)(c1-c0)/Nfast);

    // sched_yield uncontended (old path)
    t0=now_ns();
    for (uint64_t i=0;i<Nsy;i++) sched_yield();
    t1=now_ns();
    printf("sched_yield (uncontended)   %8.3f ns/call\n", (double)(t1-t0)/Nsy);

    // sched_yield with 1 competitor thread
    atomic_store(&g_stop,0);
    pthread_t th; pthread_create(&th,0,busy,0);
    t0=now_ns();
    for (uint64_t i=0;i<Nsy;i++) sched_yield();
    t1=now_ns();
    atomic_store(&g_stop,1); pthread_join(th,0);
    printf("sched_yield (1 competitor)  %8.3f ns/call\n", (double)(t1-t0)/Nsy);

    return 0;
}
