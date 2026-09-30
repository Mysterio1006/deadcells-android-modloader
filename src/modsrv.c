// modsrv.c — 外部 mod 加载器 v5
//
// ================= 引擎 pak 加载链 (反汇编确认) =================
//
//   Boot_initRes -> mobile_Res_initAssets (0x16267f4)
//     loop i=1..:
//       path = "res"+i+".pak"
//       if (!assets_exists(path)) break;        <-- 闸门 A
//       fd   = File_read(path, 0)
//       addPak(fd)
//
//   File_read (0x22eb3bc):
//     fd = android_fs_assets_read(utf8path, 0)  <-- 闸门 B (C 层)
//     obj = hl_alloc_obj(FileClass); obj->fd=fd; obj->len=fd_get_length(fd)
//
//   android_fs_assets_read (0x277f8f8) 的返回值 **不是系统 fd**,
//   而是三张内部平行表的索引:
//
//     T_FILE   [i] = FILE*        @ base+0x2e8f638
//     T_OFFSET [i] = 起始偏移(64)  @ base+0x2e910638
//     T_LENGTH [i] = 长度(64)      @ base+0x2e930638
//     HINT       = 下次分配提示    @ base+0x2e950638
//
//   fd_get_length(i) -> T_LENGTH[i]          (ldr x0,[tab, w0, sxtw #3])
//   fd_seek(i,rel)   -> fseek(T_FILE[i], T_OFFSET[i]+rel)
//   fd_tell(i)       -> ftell(T_FILE[i])
//   fd_read_bytes(i) -> fread(buf,1,n,T_FILE[i])
//   fd_close(i)      -> fclose(T_FILE[i]); T_FILE[i]=0; OFFSET/LENGTH[i]=-1
//
// ================= v2/v3/v4 为什么崩 =================
// v4 hook 的是 File_read, 自己 new 了一个对象, 把 **系统 fd** 塞进 fd 字段.
// 引擎随后拿它当 **表索引** 去索引 T_FILE -> 读到垃圾指针 -> fread 崩.
// 崩溃栈: addPak -> Reader_readHeader -> haxe_io_Input_readString 完全吻合.
//
// ================= v5 策略 =================
// 只 hook 两个 C 层函数, 完全不碰 Haxe 对象:
//   assets_exists(path)      : 外部有 -> 返回 1 (让循环别提前退出)
//   android_fs_assets_read() : 外部有 -> 自己 fopen + 填三张表 -> 返回索引
// 引擎自己的 File_read 会把对象构造得完美无缺 (类型/偏移都由它写).

#define _GNU_SOURCE
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include <fcntl.h>
#include <dlfcn.h>
#include <link.h>
#include <sys/mman.h>
#include <sys/stat.h>
#include <elf.h>
#include <android/log.h>
#include <stdint.h>
#include <stdarg.h>
#include <errno.h>
#include <pthread.h>
#include <time.h>

#define TAG "DCMOD"

// ---- mod 搜索目录 (按优先级) ----
// /sdcard 根目录在 Android 11+ 作用域存储下即使有权限也读不了;
// App 自己的 external files 目录 **无需权限** 即可读写, 故列为首选.
#define MOD_DIR_COUNT 3
static const char *MOD_DIRS[MOD_DIR_COUNT] = {
    "/sdcard/Android/data/com.bilibili.deadcells.mobile/files/DeadCellsMods/",
    "/storage/emulated/0/Android/data/com.bilibili.deadcells.mobile/files/DeadCellsMods/",
    "/sdcard/DeadCellsMods/",
};

static const char *g_logfile = NULL;

// ---- 不依赖 logcat 的文件日志 (logcat 缓冲会被启动日志冲掉) ----
static int g_logfd = 0;      // 0=未初始化; >0=fd; <0=全失败
static pthread_mutex_t g_loglock = PTHREAD_MUTEX_INITIALIZER;

static void flog(const char *fmt, ...) {
    char buf[1024];
    struct timespec ts; clock_gettime(CLOCK_REALTIME, &ts);
    int n = snprintf(buf, sizeof buf, "[%ld.%03ld] ", (long)ts.tv_sec, ts.tv_nsec/1000000);
    va_list ap; va_start(ap, fmt);
    n += vsnprintf(buf+n, sizeof buf - n - 2, fmt, ap);
    va_end(ap);
    if (n > (int)sizeof buf - 2) n = sizeof buf - 2;
    buf[n++] = '\n'; buf[n] = 0;

    pthread_mutex_lock(&g_loglock);
    if (g_logfd == 0) {
        for (int i = 0; i < MOD_DIR_COUNT; i++) {
            mkdir(MOD_DIRS[i], 0777);
            char p[512];
            snprintf(p, sizeof p, "%sloader.log", MOD_DIRS[i]);
            int fd = open(p, O_WRONLY|O_CREAT|O_APPEND, 0666);
            if (fd >= 0) { g_logfd = fd; g_logfile = MOD_DIRS[i]; break; }
        }
        if (g_logfd == 0) g_logfd = -2;
    }
    if (g_logfd > 0) { ssize_t w = write(g_logfd, buf, n); (void)w; }
    pthread_mutex_unlock(&g_loglock);

    __android_log_print(ANDROID_LOG_INFO, TAG, "%s", buf);
}

// ---------------------------------------------------------------------------
// 引擎内部表的布局 (见文件头注释)
//
// ⚠ 这些偏移必须按 **页地址 + 页内偏移** 计算, 不能拼接:
//     adrp x9, 2e91000        -> x9 = 0x2e91000  (已是页地址)
//     add  x9, x9, #0x638     -> x9 = 0x2e91638  (不是 0x2e910638!)
//
// 自洽性校验: 相邻表间隔恰好 0x2000 = 1024 槽 × 8 字节,
// 且 AAssetManager* 落在 0x2e8f630 (表首 0x2e8f638 前 8 字节).
// ---------------------------------------------------------------------------
#define TBL_FILE_OFF    0x2e8f638u
#define TBL_OFFSET_OFF  0x2e91638u
#define TBL_LENGTH_OFF  0x2e93638u
#define TBL_HINT_OFF    0x2e95638u
#define TBL_STRIDE      0x2000u
#define TBL_SLOTS       1024

static FILE    **g_tfile   = NULL;
static int64_t  *g_toffset = NULL;
static int64_t  *g_tlength = NULL;
static int32_t  *g_thint   = NULL;

typedef int (*assets_read_fn)(const char *path, int forWrite);
static assets_read_fn real_assets_read = NULL;

typedef int (*assets_exists_fn)(const char *name);
static assets_exists_fn real_assets_exists = NULL;

static int g_seen = 0;
static int g_hit  = 0;
static int g_read_calls = 0;

// ---------------------------------------------------------------------------
// basename -> 各 mod 目录, 返回第一个真实存在的
// 引擎传的是 "res5.pak" 这类纯文件名 (AAssetManager 会剥掉 assets/ 前缀)
// ---------------------------------------------------------------------------
static int ext_for(const char *name, char *out, size_t cap) {
    if (!name || !*name) return 0;
    const char *b = strrchr(name, '/');
    b = b ? b + 1 : name;
    if (!*b || strstr(b, "..")) return 0;
    struct stat st;
    for (int i = 0; i < MOD_DIR_COUNT; i++) {
        snprintf(out, cap, "%s%s", MOD_DIRS[i], b);
        if (stat(out, &st) == 0 && S_ISREG(st.st_mode) && st.st_size > 100)
            return 1;
    }
    return 0;
}

// ---------------------------------------------------------------------------
// hook 1: assets_exists(const char *name) -> int
// 外部有同名文件就谎报 true, 让 initAssets 的探测循环继续
// ---------------------------------------------------------------------------
static int my_assets_exists(const char *name) {
    char ext[600];
    if (ext_for(name, ext, sizeof ext)) {
        int fd = open(ext, O_RDONLY | O_CLOEXEC);
        if (fd >= 0) {
            close(fd);
            if (g_seen < 60) flog("assets_exists(\"%s\") -> 外部可读, 返回 1", name);
            return 1;
        }
        flog("assets_exists(\"%s\") -> open 失败: %s", name, strerror(errno));
    }
    int r = real_assets_exists ? real_assets_exists(name) : 0;
    if (strstr(name ? name : "", ".pak") && g_seen < 60) {
        g_seen++;
        flog("assets_exists(\"%s\") -> %d (走内置)", name, r);
    }
    return r;
}

// ---------------------------------------------------------------------------
// hook 2: android_fs_assets_read(const char *path, int mode) -> 表索引
// 外部有就自己 fopen 并注册进引擎的三张表, 返回合法索引.
// 引擎拿到索引后 fd_seek/fd_tell/fd_read_bytes/fd_close 全部原样可用.
//
// ⚠ 关于第二个参数 (反汇编 File_read 0x22eb3bc 确认):
//     22eb3cc: cbz x1, 22eb43c     ; arg1 == NULL 时
//     22eb43c: mov w20, #1         ; w20 = 1
//     22eb3f0: mov w1, w20
//     22eb3f4: bl  assets_read
//   initAssets 传的正是 x1 = xzr (NULL) -> mode == 1 !
//   早期版本用 `if (!mode)` 当守卫, 恰好把唯一要拦的调用挡在门外,
//   直接 fall through -> "File Not Found". 这是 v6 不生效的真因.
//   故此处不按 mode 过滤: 只要外部有同名文件就接管.
// ---------------------------------------------------------------------------
static int my_assets_read(const char *path, int mode) {
    (void)mode;
    g_read_calls++;
    if (path) {
        char ext[600];
        if (ext_for(path, ext, sizeof ext)) {
            if (g_hit < 20) flog("assets_read(\"%s\", mode=%d) -> 尝试外载", path, mode);
            FILE *fp = fopen(ext, "rb");
            if (fp) {
                fseek(fp, 0, SEEK_END);
                long len = ftell(fp);
                fseek(fp, 0, SEEK_SET);

                // 从 hint 起找空槽 (照抄原版语义: 优先 hint, 满了从头找)
                int idx = g_thint ? *g_thint : 0;
                if (idx < 0 || idx >= TBL_SLOTS) idx = 0;
                while (idx < TBL_SLOTS && g_tfile[idx]) idx++;
                if (idx >= TBL_SLOTS) {
                    for (idx = 0; idx < TBL_SLOTS && g_tfile[idx]; idx++) ;
                }
                if (idx >= TBL_SLOTS) {
                    fclose(fp);
                    flog("外载失败: 表已满");
                } else {
                    g_tfile[idx]   = fp;
                    g_toffset[idx] = 0;              // 普通文件, 起始即 0
                    g_tlength[idx] = (int64_t)len;
                    if (g_thint && *g_thint <= idx) *g_thint = idx + 1;
                    flog("★★ 外载命中: %s -> %s (idx=%d, %ld B)",
                         path, ext, idx, len);
                    return idx;
                }
            } else {
                flog("外载失败 fopen(%s): %s", ext, strerror(errno));
            }
        } else if (g_read_calls <= 20) {
            flog("assets_read(\"%s\") -> 无外部文件, 走内置", path);
        }
    }
    return real_assets_read(path, mode);
}

// ---------------------------------------------------------------------------
// GOT 改写
// ---------------------------------------------------------------------------
struct modfind { const char *want; uintptr_t base; int found; };
static int modfind_cb(struct dl_phdr_info *info, size_t sz, void *data) {
    (void)sz;
    struct modfind *mf = (struct modfind *)data;
    if (info->dlpi_name && strstr(info->dlpi_name, mf->want)) {
        mf->base = (uintptr_t)info->dlpi_addr; mf->found = 1; return 1;
    }
    return 0;
}

static void **find_got(uintptr_t base, const char *sym) {
    ElfW(Ehdr) *eh = (ElfW(Ehdr)*)base;
    if (memcmp(eh->e_ident, ELFMAG, 4) != 0) return NULL;
    ElfW(Phdr) *ph = (ElfW(Phdr)*)(base + eh->e_phoff);
    ElfW(Dyn) *dyn = NULL;
    for (int i = 0; i < eh->e_phnum; i++)
        if (ph[i].p_type == PT_DYNAMIC) { dyn = (ElfW(Dyn)*)(base + ph[i].p_vaddr); break; }
    if (!dyn) return NULL;
    ElfW(Sym) *symtab = NULL; const char *strtab = NULL;
    ElfW(Rela) *jmprel = NULL; size_t pltrelsz = 0;
    for (ElfW(Dyn) *d = dyn; d->d_tag != DT_NULL; d++) {
        switch (d->d_tag) {
        case DT_SYMTAB:   symtab   = (ElfW(Sym)*)(base + d->d_un.d_ptr); break;
        case DT_STRTAB:   strtab   = (const char*)(base + d->d_un.d_ptr); break;
        case DT_JMPREL:   jmprel   = (ElfW(Rela)*)(base + d->d_un.d_ptr); break;
        case DT_PLTRELSZ: pltrelsz = d->d_un.d_val; break;
        }
    }
    if (!symtab || !strtab || !jmprel || !pltrelsz) return NULL;
    size_t n = pltrelsz / sizeof(ElfW(Rela));
    for (size_t i = 0; i < n; i++) {
        ElfW(Rela) *r = &jmprel[i];
        if (ELF64_R_TYPE(r->r_info) != R_AARCH64_JUMP_SLOT) continue;
        unsigned si = ELF64_R_SYM(r->r_info);
        if (strcmp(strtab + symtab[si].st_name, sym) == 0)
            return (void**)(base + r->r_offset);
    }
    return NULL;
}

static int hook_sym(uintptr_t base, const char *sym, void *hook, void **orig) {
    void **slot = find_got(base, sym);
    if (!slot) { flog("[hook] %s: 无 GOT 项", sym); return -1; }
    long pg = sysconf(_SC_PAGESIZE);
    uintptr_t a = (uintptr_t)slot & ~(uintptr_t)(pg - 1);
    if (mprotect((void*)a, pg, PROT_READ|PROT_WRITE) != 0) {
        flog("[hook] %s: mprotect 失败 (%s)", sym, strerror(errno)); return -2;
    }
    void *old = *slot;
    *slot = hook;
    mprotect((void*)a, pg, PROT_READ);
    if (orig) *orig = old;
    flog("[hook] %s: %p -> %p OK", sym, old, hook);
    return 0;
}

// ---------------------------------------------------------------------------
static void *hook_thread(void *arg) {
    (void)arg;
    void *h = NULL;
    for (int i = 0; i < 1200; i++) {
        h = dlopen("libnative-lib.so", RTLD_NOLOAD | RTLD_NOW);
        if (h) break;
        usleep(25000);
    }
    if (!h) { flog("[thread] 引擎未加载"); return NULL; }

    struct modfind mf = { "libnative-lib.so", 0, 0 };
    dl_iterate_phdr(modfind_cb, &mf);
    if (!mf.found) { flog("[thread] 未找到引擎"); return NULL; }
    flog("[thread] 引擎 base=%p", (void*)mf.base);

    // 解析三张内部表
    g_tfile   = (FILE**)   (mf.base + TBL_FILE_OFF);
    g_toffset = (int64_t*) (mf.base + TBL_OFFSET_OFF);
    g_tlength = (int64_t*) (mf.base + TBL_LENGTH_OFF);
    g_thint   = (int32_t*) (mf.base + TBL_HINT_OFF);
    flog("[thread] 表: FILE=%p OFFSET=%p LEN=%p HINT=%p (hint=%d)",
         (void*)g_tfile, (void*)g_toffset, (void*)g_tlength,
         (void*)g_thint, g_thint ? *g_thint : -1);

    // 运行时自检: 未打开的槽位 FILE* 必须是 NULL, OFFSET/LENGTH 必须是 -1.
    // 一旦地址算错, 这里会立刻暴露 (而不是等 fd_* 解引用时崩).
    {
        int bad = 0, nulls = 0;
        for (int i = 0; i < 16; i++) {
            if (g_tfile[i] == NULL) nulls++;
            else bad++;
        }
        flog("[thread] 自检 T_FILE[0..15]: %d 空 / %d 非空", nulls, bad);
        flog("[thread] 自检 T_LENGTH[0..7] = %lld,%lld,%lld,%lld,%lld,%lld,%lld,%lld",
             (long long)g_tlength[0], (long long)g_tlength[1],
             (long long)g_tlength[2], (long long)g_tlength[3],
             (long long)g_tlength[4], (long long)g_tlength[5],
             (long long)g_tlength[6], (long long)g_tlength[7]);
        flog("[thread] 自检 T_OFFSET[0..7] = %lld,%lld,%lld,%lld,%lld,%lld,%lld,%lld",
             (long long)g_toffset[0], (long long)g_toffset[1],
             (long long)g_toffset[2], (long long)g_toffset[3],
             (long long)g_toffset[4], (long long)g_toffset[5],
             (long long)g_toffset[6], (long long)g_toffset[7]);
    }

    int r1 = hook_sym(mf.base, "android_fs_assets_exists",
                      (void*)my_assets_exists, (void**)&real_assets_exists);
    int r2 = hook_sym(mf.base, "android_fs_assets_read",
                      (void*)my_assets_read, (void**)&real_assets_read);
    flog("[thread] assets_exists hook=%d | assets_read hook=%d (real=%p)",
         r1, r2, (void*)real_assets_read);
    flog("[thread] 完成");
    return NULL;
}

__attribute__((constructor))
static void modsrv_init(void) {
    for (int i = 0; i < MOD_DIR_COUNT; i++) mkdir(MOD_DIRS[i], 0777);
    flog("==========================================");
    flog("modsrv 外载加载器 v5 ENTER pid=%d", getpid());
    pthread_t t;
    if (pthread_create(&t, NULL, hook_thread, NULL) == 0) pthread_detach(t);
    flog("ctor EXIT");
}
