package com.dsharnessmobile.deadcells.modmanager;

import android.os.Environment;

import java.io.File;
import java.util.ArrayList;
import java.util.Collections;
import java.util.Comparator;
import java.util.List;
import java.util.Locale;

/**
 * Mod 仓库 —— 负责 mod 目录的定位、枚举与增删。
 *
 * <h3>为什么是这个目录</h3>
 * 加载器（被塞进游戏 APK 的 libmain.so）需要能读到 mod，而本 App 需要能写进去。
 * 实测结论（vivo / Android 16）：
 *
 * <pre>
 *   位置                                          管理器写  游戏读
 *   /sdcard/Android/data/&lt;游戏&gt;/files/...          ✗       ✓    FUSE 按 uid 遮蔽，连 stat 都 ENOENT
 *   /sdcard/Android/media/&lt;管理器包名&gt;/...          ✓       ✗    属主是管理器 uid，游戏无 ext_data_rw 组
 *   /sdcard/Android/media/&lt;游戏包名&gt;/...            ✓       ✓    ← 唯一交集
 * </pre>
 *
 * 关键点：{@code Android/media} 对整个系统是公开可写的，但每个包名子目录的
 * <b>属主</b> 决定了谁能读。只有把它挂在 <b>游戏自己的包名</b> 下，游戏进程
 * 才有权限读，同时别的 App（我们）也写得进去 —— 这就是"共生"的技术基础。
 *
 * <p>另外注意：该目录下所有文件在 FUSE 层都被映射成 {@code media_rw} 属主，
 * {@code chmod} 是不生效的（调用会"成功"但权限位不变），所以不要试图靠改权限
 * 来打通访问。
 */
public final class ModRepo {

    /** 游戏包名（哔哩哔哩渠道版）。换渠道需同步改这里和加载器里的路径。 */
    public static final String GAME_PKG = "com.bilibili.deadcells.mobile";

    /** Mod 子目录名，必须与加载器 MOD_DIRS 中的最后一段一致。 */
    public static final String MOD_SUBDIR = "DeadCellsMods";

    public static final String ENABLED_EXT  = ".pak";
    public static final String DISABLED_EXT = ".pak.off";

    private ModRepo() {}

    /**
     * Mod 目录。
     *
     * <p>优先用 {@code getExternalStorageDirectory()} 拿到真实的 /sdcard 挂载点
     * （部分设备是 /storage/emulated/0 或 /storage/XXXX-XXXX），再用包名拼出
     * media 路径；写死 "/sdcard" 在个别 ROM 上会失效。
     */
    public static File dir() {
        File ext = Environment.getExternalStorageDirectory();
        File d = new File(new File(new File(ext, "Android"), "media"), GAME_PKG);
        return new File(d, MOD_SUBDIR);
    }

    /** 目录路径字符串，用于界面展示。 */
    public static String dirPath() {
        return dir().getAbsolutePath();
    }

    /** 确保目录存在；返回是否可用。 */
    public static boolean ensureDir() {
        File d = dir();
        if (d.isDirectory()) return true;
        return d.mkdirs() || d.isDirectory();
    }

    /** 一个 mod 条目。 */
    public static final class Item {
        public final File file;
        public final boolean enabled;
        /** 不含 .pak / .pak.off 后缀的显示名。 */
        public final String baseName;

        Item(File f, boolean on, String base) {
            file = f; enabled = on; baseName = base;
        }

        public long size() { return file.length(); }

        public String sizeText() {
            long b = size();
            if (b < 1024) return b + " B";
            if (b < 1024 * 1024) return String.format(Locale.US, "%.1f KB", b / 1024.0);
            return String.format(Locale.US, "%.1f MB", b / (1024.0 * 1024.0));
        }

        /** 这个 mod 会覆盖哪个内置包（res5.pak -> res5）。 */
        public String targetPak() {
            String n = baseName;
            return n.endsWith(".pak") ? n.substring(0, n.length() - 4) : n;
        }
    }

    /**
     * 列出目录内所有 mod，按启用状态优先、再按名字排序。
     *
     * <p>加载器按 basename 精确匹配引擎请求的资源名（如 "res5.pak"），
     * 因此 <b>文件名本身决定它覆盖哪个包</b>；停用就是改名成 .pak.off，
     * 加载器便不再匹配，效果等同于移除但保留文件。
     */
    public static List<Item> list() {
        List<Item> out = new ArrayList<>();
        File d = dir();
        File[] fs = d.listFiles();
        if (fs == null) return out;

        for (File f : fs) {
            if (!f.isFile()) continue;
            String n = f.getName();
            // 加载器自己的日志等非 mod 文件一并忽略
            if (n.startsWith("loader.log")) continue;

            String lower = n.toLowerCase(Locale.US);
            boolean on, off;
            if (lower.endsWith(DISABLED_EXT)) { on = false; off = true; }
            else if (lower.endsWith(ENABLED_EXT)) { on = true; off = false; }
            else continue;   // 不是 mod，跳过

            String base = on
                    ? n.substring(0, n.length() - ENABLED_EXT.length())
                    : n.substring(0, n.length() - DISABLED_EXT.length());
            out.add(new Item(f, on, base));
        }

        Collections.sort(out, new Comparator<Item>() {
            @Override public int compare(Item a, Item b) {
                if (a.enabled != b.enabled) return a.enabled ? -1 : 1;
                return a.baseName.compareToIgnoreCase(b.baseName);
            }
        });
        return out;
    }

    /** 启用 / 停用（改名）。返回 null 表示成功，否则返回错误信息。 */
    public static String setEnabled(Item it, boolean enable) {
        File src = it.file;
        String want = enable ? it.baseName + ENABLED_EXT
                             : it.baseName + DISABLED_EXT;
        File dst = new File(src.getParentFile(), want);
        if (dst.exists() && !dst.equals(src)) return "目标已存在：" + want;
        if (it.enabled == enable) return null;
        if (!src.renameTo(dst)) return "重命名失败（" + src.getName() + "）";
        return null;
    }

    /** 删除。返回 null 表示成功。 */
    public static String delete(Item it) {
        return it.file.delete() ? null : "删除失败";
    }

    /** 加载器写在这里的日志。 */
    public static File logFile() {
        return new File(dir(), "loader.log");
    }

    // ------------------------------------------------------------ 权限相关

    /**
     * 是否已获得「所有文件访问」。
     *
     * <p><b>为什么这个权限是必需的</b>：Android 11+ 下 {@code Android/media/<包名>/}
     * 属于共享存储。没有该权限时，目录的 {@code exists()} / {@code isDirectory()} /
     * {@code canRead()} <b>全都返回 true</b>，但 {@code listFiles()} 会静默返回空数组 ——
     * 不抛异常、不报错。新手很容易把它误判成"目录里确实没文件"。
     *
     * <p>API &lt; 30 没有这个权限，一律视为可用。
     */
    public static boolean hasAllFilesAccess() {
        if (android.os.Build.VERSION.SDK_INT < 30) return true;
        try {
            return android.os.Environment.isExternalStorageManager();
        } catch (Throwable t) {
            return true;   // 查询失败时不要卡住用户
        }
    }

    /** 跳到系统「所有文件访问」授权页。 */
    public static void requestAllFilesAccess(android.content.Context ctx) {
        try {
            android.content.Intent i = new android.content.Intent(
                    "android.settings.MANAGE_APP_ALL_FILES_ACCESS_PERMISSION");
            i.setData(android.net.Uri.parse("package:" + ctx.getPackageName()));
            ctx.startActivity(i);
        } catch (Throwable t1) {
            try {
                ctx.startActivity(new android.content.Intent(
                        "android.settings.MANAGE_ALL_FILES_ACCESS_PERMISSION"));
            } catch (Throwable t2) {
                // 最后的兜底：应用详情页
                try {
                    ctx.startActivity(new android.content.Intent(
                            android.provider.Settings.ACTION_APPLICATION_DETAILS_SETTINGS,
                            android.net.Uri.parse("package:" + ctx.getPackageName())));
                } catch (Throwable ignored) { }
            }
        }
    }

    public static String readLog() {
        File f = logFile();
        if (!f.isFile()) return "（还没有日志。先启动一次游戏，加载器会写入这里。）";
        try {
            java.io.FileInputStream in = new java.io.FileInputStream(f);
            byte[] buf = new byte[(int) Math.min(f.length(), 512 * 1024)];
            int n = in.read(buf);
            in.close();
            return new String(buf, 0, Math.max(n, 0), "UTF-8");
        } catch (Exception e) {
            return "读取日志失败：" + e;
        }
    }
}
