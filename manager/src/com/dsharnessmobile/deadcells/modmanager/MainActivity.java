package com.dsharnessmobile.deadcells.modmanager;

import android.app.Activity;
import android.app.AlertDialog;
import android.content.Intent;
import android.graphics.Color;
import android.net.Uri;
import android.os.Bundle;
import android.view.LayoutInflater;
import android.view.Menu;
import android.view.MenuItem;
import android.view.View;
import android.view.ViewGroup;
import android.widget.AdapterView;
import android.widget.BaseAdapter;
import android.widget.Button;
import android.widget.ListView;
import android.widget.TextView;
import android.widget.Toast;

import java.io.File;
import java.io.FileOutputStream;
import java.io.InputStream;
import java.io.OutputStream;
import java.util.List;

/**
 * 主界面：列出 / 导入 / 启停 / 删除 mod，并查看加载日志。
 *
 * <p>刻意不依赖 AndroidX —— 本机没有 Gradle，这些 APK 是用
 * aapt2 + javac + d8 手工构建的，引入第三方库会让构建复杂很多。
 */
public class MainActivity extends Activity {

    private static final int REQ_PICK = 1001;

    private ListView list;
    private TextView empty, dirPath;
    private ModAdapter adapter;

    @Override
    protected void onCreate(Bundle b) {
        super.onCreate(b);
        setContentView(R.layout.activity_main);

        list     = findViewById(R.id.list);
        empty    = findViewById(R.id.empty);
        dirPath  = findViewById(R.id.dirPath);

        adapter = new ModAdapter();
        list.setAdapter(adapter);
        list.setOnItemClickListener(new AdapterView.OnItemClickListener() {
            @Override public void onItemClick(AdapterView<?> p, View v, int pos, long id) {
                showActions(adapter.getItem(pos));
            }
        });

        dirPath.setText(ModRepo.dirPath());

        ((Button) findViewById(R.id.btnImport)).setOnClickListener(new View.OnClickListener() {
            @Override public void onClick(View v) { pickFile(); }
        });
        ((Button) findViewById(R.id.btnRefresh)).setOnClickListener(new View.OnClickListener() {
            @Override public void onClick(View v) { reload(); }
        });
        ((Button) findViewById(R.id.btnLog)).setOnClickListener(new View.OnClickListener() {
            @Override public void onClick(View v) {
                startActivity(new Intent(MainActivity.this, LogActivity.class));
            }
        });
        ((Button) findViewById(R.id.btnHelp)).setOnClickListener(new View.OnClickListener() {
            @Override public void onClick(View v) { showHelp(); }
        });
        ((Button) findViewById(R.id.btnLoader)).setOnClickListener(new View.OnClickListener() {
            @Override public void onClick(View v) { showLoaderHelp(); }
        });
    }

    @Override
    protected void onResume() {
        super.onResume();
        reload();
    }

    private void reload() {
        if (!ModRepo.ensureDir()) {
            toast("无法创建 Mod 目录，请检查存储状态");
        }
        // 权限自检：没有「所有文件访问」时，Android/media 目录会"看起来可读"
        // 但 listFiles() 静默返回空 —— 这是最容易误判的一种失败，必须显式提示。
        if (!ModRepo.hasAllFilesAccess()) {
            adapter.setData(java.util.Collections.<ModRepo.Item>emptyList());
            empty.setVisibility(View.VISIBLE);
            list.setVisibility(View.GONE);
            empty.setText("缺少「所有文件访问」权限\n\n"
                    + "Android 11+ 下 Mod 目录需要该权限才能读取。\n"
                    + "点这里去授权，回来后会自动刷新。");
            empty.setOnClickListener(new View.OnClickListener() {
                @Override public void onClick(View v) { ModRepo.requestAllFilesAccess(MainActivity.this); }
            });
            dirPath.setText(ModRepo.dirPath());
            return;
        }
        empty.setOnClickListener(null);

        adapter.setData(ModRepo.list());
        boolean none = adapter.getCount() == 0;
        empty.setVisibility(none ? View.VISIBLE : View.GONE);
        list.setVisibility(none ? View.GONE : View.VISIBLE);
        empty.setText(R.string.empty_hint);
        dirPath.setText(ModRepo.dirPath());
    }

    // ---------------------------------------------------------------- 导入

    private void pickFile() {
        Intent i = new Intent(Intent.ACTION_GET_CONTENT);
        i.setType("*/*");
        i.addCategory(Intent.CATEGORY_OPENABLE);
        try {
            startActivityForResult(Intent.createChooser(i, getString(R.string.btn_import)), REQ_PICK);
        } catch (Exception e) {
            toast("没有可用的文件选择器：" + e.getMessage());
        }
    }

    @Override
    protected void onActivityResult(int req, int res, Intent data) {
        super.onActivityResult(req, res, data);
        if (req != REQ_PICK || res != RESULT_OK || data == null) return;
        Uri uri = data.getData();
        if (uri == null) return;

        if (!ModRepo.ensureDir()) { toast("无法创建 Mod 目录"); return; }

        String name = queryName(uri);
        // 统一成 .pak 结尾，加载器按 basename 精确匹配资源名
        if (name == null || name.isEmpty()) name = "imported.pak";
        if (!name.toLowerCase().endsWith(".pak")) name = name + ".pak";

        File dst = new File(ModRepo.dir(), name);
        if (dst.exists()) { toast("已存在同名 Mod：" + name); return; }

        try {
            InputStream in = getContentResolver().openInputStream(uri);
            if (in == null) { toast("无法读取所选文件"); return; }
            OutputStream out = new FileOutputStream(dst);
            byte[] buf = new byte[64 * 1024];
            long total = 0; int n;
            while ((n = in.read(buf)) > 0) { out.write(buf, 0, n); total += n; }
            out.flush(); out.close(); in.close();

            // 基本校验：PAK 魔数，避免把随便一个文件当 mod
            if (total < 76 || !"PAK".equals(readMagic(dst))) {
                dst.delete();
                toast("这不是有效的 PAK 文件（魔数不匹配）");
                return;
            }
            toast("已导入 " + name + "（" + (total / 1024) + " KB）");
            reload();

            // 关键提醒：加载器是按「文件名」精确匹配引擎请求的资源名的，
            // 所以 mod 必须叫 res5.pak / res3.pak 这类名字才会生效。
            // 用户从别处下载的 mod 往往名字随意，这里必须讲清楚，
            // 否则会出现"导入了却没反应"的困惑。
            if (!isLoaderName(name)) {
                new AlertDialog.Builder(this)
                        .setTitle("注意：这个文件名可能不会生效")
                        .setMessage("加载器是按文件名匹配的 —— 只有当文件名和游戏\n"
                                + "要加载的资源同名时才会生效，比如：\n\n"
                                + "    res5.pak\n"
                                + "    res3.pak\n\n"
                                + "当前文件名是「" + name + "」，加载器不会把它\n"
                                + "当作任何资源的替代品。\n\n"
                                + "如果这个 mod 的作者说明它应该覆盖某个包，\n"
                                + "请重命名成对应名字。")
                        .setPositiveButton(R.string.ok, null)
                        .show();
            }
        } catch (Exception e) {
            dst.delete();
            toast("导入失败：" + e.getMessage());
        }
    }

    /**
     * 文件名是否像加载器能识别的资源名。
     *
     * <p>引擎固定请求 {@code res.pak}、{@code res1.pak} … {@code res4.pak}
     * （部分版本还有 res5/res6）。只有同名文件才会被外载逻辑命中。
     */
    private static boolean isLoaderName(String name) {
        String n = name.toLowerCase(java.util.Locale.US);
        if (!n.endsWith(".pak")) return false;
        String base = n.substring(0, n.length() - 4);
        if (base.equals("res")) return true;
        if (!base.startsWith("res")) return false;
        String num = base.substring(3);
        if (num.isEmpty() || num.length() > 2) return false;
        for (int i = 0; i < num.length(); i++) {
            if (!Character.isDigit(num.charAt(i))) return false;
        }
        return true;
    }

    private static String readMagic(File f) {
        try {
            InputStream in = new java.io.FileInputStream(f);
            byte[] b3 = new byte[3];
            int n = in.read(b3); in.close();
            return n == 3 ? new String(b3, "US-ASCII") : "";
        } catch (Exception e) { return ""; }
    }

    private String queryName(Uri uri) {
        android.database.Cursor c = null;
        try {
            c = getContentResolver().query(uri, null, null, null, null);
            if (c != null && c.moveToFirst()) {
                int i = c.getColumnIndex(android.provider.OpenableColumns.DISPLAY_NAME);
                if (i >= 0) return c.getString(i);
            }
        } catch (Exception ignored) {
        } finally { if (c != null) c.close(); }
        String p = uri.getLastPathSegment();
        if (p == null) return null;
        int s = p.lastIndexOf('/');
        return s >= 0 ? p.substring(s + 1) : p;
    }

    // ------------------------------------------------------------ 条目操作

    private void showActions(final ModRepo.Item it) {
        final String[] acts = {
                it.enabled ? getString(R.string.menu_disable) : getString(R.string.menu_enable),
                getString(R.string.menu_info),
                getString(R.string.menu_delete)
        };
        new AlertDialog.Builder(this)
                .setTitle(it.baseName)
                .setItems(acts, new android.content.DialogInterface.OnClickListener() {
                    @Override public void onClick(android.content.DialogInterface d, int w) {
                        if (w == 0) toggle(it);
                        else if (w == 1) showInfo(it);
                        else confirmDelete(it);
                    }
                })
                .show();
    }

    private void toggle(ModRepo.Item it) {
        String err = ModRepo.setEnabled(it, !it.enabled);
        if (err != null) toast(err);
        else toast(it.enabled ? "已停用" : "已启用");
        reload();
    }

    private void confirmDelete(final ModRepo.Item it) {
        new AlertDialog.Builder(this)
                .setTitle(R.string.dlg_delete_title)
                .setMessage(getString(R.string.dlg_delete_msg, it.baseName))
                .setPositiveButton(R.string.ok, new android.content.DialogInterface.OnClickListener() {
                    @Override public void onClick(android.content.DialogInterface d, int w) {
                        String err = ModRepo.delete(it);
                        toast(err != null ? err : "已删除");
                        reload();
                    }
                })
                .setNegativeButton(R.string.cancel, null)
                .show();
    }

    private void showInfo(ModRepo.Item it) {
        StringBuilder sb = new StringBuilder();
        sb.append("文件：").append(it.file.getName()).append('\n');
        sb.append("大小：").append(it.sizeText()).append('\n');
        sb.append("状态：").append(it.enabled ? "已启用" : "已停用").append('\n');
        sb.append("覆盖包：").append(it.targetPak()).append('\n');
        sb.append("路径：").append(it.file.getAbsolutePath()).append("\n\n");
        sb.append(it.enabled
                ? "加载器会在游戏请求 “" + it.baseName + "” 时改用这个文件。\n"
                  + "编号越大越晚加载，同名资源覆盖先前加载的。"
                : "已停用：文件名带 .off，加载器不会匹配到它。");
        new AlertDialog.Builder(this).setTitle("Mod 详情").setMessage(sb.toString())
                .setPositiveButton(R.string.ok, null).show();
    }

    // -------------------------------------------------------------- 帮助

    private void showHelp() {
        String s = "用法\n\n"
                + "1) 先把「加载器版」游戏装上（只需一次）。\n\n"
                + "2) 把 .pak 放到这里：\n" + ModRepo.dirPath() + "\n\n"
                + "3) 进游戏即生效，不用重装、不用清数据。\n\n"
                + "文件名决定覆盖哪个包：res5.pak 会覆盖游戏内置的 res5.pak。\n"
                + "编号越大加载越晚，同名资源以晚加载的为准。\n\n"
                + "停用 = 改名成 .pak.off，文件保留但不生效。\n\n"
                + "为什么需要特殊版本的游戏？\n"
                + "原版游戏只从 APK 内部读资源。加载器版把启动时会加载的\n"
                + "libmain.so 换成了我们的实现，让引擎改从本目录读取。";
        new AlertDialog.Builder(this).setTitle(R.string.btn_help)
                .setMessage(s).setPositiveButton(R.string.ok, null).show();
    }

    private void showLoaderHelp() {
        String s = "本 App 只负责管理 Mod 文件，不改动游戏。\n\n"
                + "要让 Mod 生效，游戏必须是「加载器版」—— 即在原版基础上\n"
                + "替换了 lib/arm64-v8a/libmain.so 的版本。\n\n"
                + "获取方式：见项目仓库（GitHub: Mysterio1006/\n"
                + "deadcells-android-modloader）里的构建说明。\n\n"
                + "装好后直接覆盖安装即可，不会清除游戏数据。";
        new AlertDialog.Builder(this).setTitle(R.string.btn_install_loader)
                .setMessage(s).setPositiveButton(R.string.ok, null).show();
    }

    private void toast(String s) { Toast.makeText(this, s, Toast.LENGTH_SHORT).show(); }

    // ------------------------------------------------------------- Adapter

    private class ModAdapter extends BaseAdapter {
        private List<ModRepo.Item> data;

        void setData(List<ModRepo.Item> d) { data = d; notifyDataSetChanged(); }

        @Override public int getCount() { return data == null ? 0 : data.size(); }
        @Override public ModRepo.Item getItem(int i) { return data.get(i); }
        @Override public long getItemId(int i) { return i; }

        @Override
        public View getView(int pos, View cv, ViewGroup parent) {
            if (cv == null) {
                cv = LayoutInflater.from(MainActivity.this)
                        .inflate(R.layout.item_mod, parent, false);
            }
            ModRepo.Item it = getItem(pos);

            ((TextView) cv.findViewById(R.id.itemName)).setText(it.baseName);
            ((TextView) cv.findViewById(R.id.itemDetail))
                    .setText(it.sizeText() + "  ·  覆盖 " + it.targetPak());

            TextView st = cv.findViewById(R.id.itemState);
            if (it.enabled) {
                st.setText(R.string.state_on);
                st.setTextColor(Color.parseColor("#2E7D32"));
            } else {
                st.setText(R.string.state_off);
                st.setTextColor(Color.parseColor("#999999"));
            }
            return cv;
        }
    }

    @Override
    public boolean onCreateOptionsMenu(Menu m) {
        m.add(0, 1, 0, R.string.btn_log);
        m.add(0, 2, 1, R.string.btn_help);
        return true;
    }

    @Override
    public boolean onOptionsItemSelected(MenuItem mi) {
        if (mi.getItemId() == 1) { startActivity(new Intent(this, LogActivity.class)); return true; }
        if (mi.getItemId() == 2) { showHelp(); return true; }
        return super.onOptionsItemSelected(mi);
    }
}
