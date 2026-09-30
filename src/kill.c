// kill.c — ctor 里立刻自杀, 用来证明 "游戏是否真的加载了我们的 libmain.so"
#define _GNU_SOURCE
#include <android/log.h>
#include <signal.h>
#include <unistd.h>
#include <fcntl.h>
#include <string.h>
__attribute__((constructor))
static void k(void){
    __android_log_print(6,"DCKILL","CTOR RAN -> abort");
    int fd=open("/sdcard/DeadCellsMods/kill.txt",O_WRONLY|O_CREAT|O_APPEND,0666);
    if(fd>=0){write(fd,"KILL CTOR RAN\n",14);close(fd);}
    // 用 SIGSEGV 直接崩, 保证能看到
    *(volatile int*)0 = 1;
}
