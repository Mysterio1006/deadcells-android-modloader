#define _GNU_SOURCE
#include <fcntl.h>
#include <unistd.h>
#include <string.h>
#include <android/log.h>
__attribute__((constructor))
static void probe_ctor(void) {
    __android_log_print(6,"DCPROBE","=== PROBE CTOR RAN pid=%d ===", getpid());
    int fd = open("/sdcard/DeadCellsMods/probe.txt", O_WRONLY|O_CREAT|O_APPEND, 0666);
    if (fd >= 0) { write(fd,"PROBE_RAN\n",10); close(fd); }
}
