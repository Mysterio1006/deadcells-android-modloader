// boom.c — 必定失败的 so: 引用一个不存在的符号
#define _GNU_SOURCE
#include <android/log.h>
extern void this_symbol_does_not_exist_xyz(void);
__attribute__((constructor))
static void boom_ctor(void){
    __android_log_print(6,"DCBOOM","BOOM CTOR RAN");
    this_symbol_does_not_exist_xyz();   // dlopen 时会解析失败
}
