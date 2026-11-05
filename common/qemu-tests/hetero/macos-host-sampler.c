/* SPDX-License-Identifier: GPL-2.0-or-later */
/* Observe a same-user process and system CPU counters without stopping it. */
#include <errno.h>
#include <inttypes.h>
#include <libproc.h>
#include <mach/mach.h>
#include <signal.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/proc_info.h>
#include <time.h>
#include <unistd.h>

static volatile sig_atomic_t stopping;
static void stop(int sig) { stopping = 1; }

static void quoted(const char *s)
{
    putchar('"');
    for (; *s; s++) {
        if (*s == '"' || *s == '\\') {
            putchar('\\');
        }
        if ((unsigned char)*s >= 32) {
            putchar(*s);
        }
    }
    putchar('"');
}

static int snapshot(pid_t pid)
{
    uint64_t ids[1024];
    int size = proc_pidinfo(pid, PROC_PIDLISTTHREADS, 0, ids, sizeof(ids));
    if (size <= 0) {
        fprintf(stderr, "proc_pidinfo list: %s\n", strerror(errno));
        return 1;
    }
    printf("{\"monotonic_ns\":%" PRIu64 ",\"threads\":[", clock_gettime_nsec_np(CLOCK_MONOTONIC));
    int emitted = 0;
    for (int i = 0; i < size / sizeof(ids[0]); i++) {
        struct proc_threadinfo info;
        int result = proc_pidinfo(pid, PROC_PIDTHREADID64INFO, ids[i], &info, sizeof(info));
        if (result != sizeof(info)) {
            result = proc_pidinfo(pid, PROC_PIDTHREADINFO, ids[i], &info, sizeof(info));
        }
        if (result != sizeof(info)) {
            fprintf(stderr, "thread %" PRIu64 ": result=%d errno=%d\n", ids[i], result, errno);
            continue;
        }
        printf("%s{\"tid\":%" PRIu64 ",\"name\":", emitted++ ? "," : "", ids[i]);
        quoted(info.pth_name);
        printf(",\"user_ns\":%" PRIu64 ",\"system_ns\":%" PRIu64
               ",\"state\":%d,\"priority\":%d,\"cpu_usage_scaled\":%d}",
               info.pth_user_time, info.pth_system_time, info.pth_run_state,
               info.pth_curpri, info.pth_cpu_usage);
    }
    natural_t count;
    processor_info_array_t counters;
    mach_msg_type_number_t entries;
    kern_return_t result = host_processor_info(mach_host_self(), PROCESSOR_CPU_LOAD_INFO,
                                               &count, &counters, &entries);
    printf("],\"cpus\":[");
    if (result == KERN_SUCCESS) {
        for (int i = 0; i < count; i++) {
            printf("%s{\"cpu\":%d,\"ticks\":[%u,%u,%u,%u]}", i ? "," : "", i,
                   counters[i * CPU_STATE_MAX + CPU_STATE_USER],
                   counters[i * CPU_STATE_MAX + CPU_STATE_SYSTEM],
                   counters[i * CPU_STATE_MAX + CPU_STATE_IDLE],
                   counters[i * CPU_STATE_MAX + CPU_STATE_NICE]);
        }
        vm_deallocate(mach_task_self(), (vm_address_t)counters, entries * sizeof(integer_t));
    }
    printf("],\"cpu_query_status\":%d}\n", result);
    fflush(stdout);
    return emitted == 0 || result != KERN_SUCCESS;
}

int main(int argc, char **argv)
{
    if (argc != 3) {
        fprintf(stderr, "usage: %s pid interval-ms\n", argv[0]);
        return 2;
    }
    pid_t pid = atoi(argv[1]);
    int interval = atoi(argv[2]);
    if (pid <= 0 || interval <= 0) {
        return 2;
    }
    signal(SIGTERM, stop);
    signal(SIGINT, stop);
    do {
        if (snapshot(pid)) {
            return 1;
        }
        if (!stopping) {
            usleep(interval * 1000);
        }
    } while (!stopping);
    return snapshot(pid);
}
