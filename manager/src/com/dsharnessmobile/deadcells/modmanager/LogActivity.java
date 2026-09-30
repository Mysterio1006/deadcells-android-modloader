package com.dsharnessmobile.deadcells.modmanager;

import android.app.Activity;
import android.content.Intent;
import android.os.Bundle;
import android.view.View;
import android.widget.Button;
import android.widget.TextView;
import android.widget.Toast;

import java.io.File;
import java.io.FileOutputStream;

/**
 * 加载日志查看器。
 *
 * <p>加载器不写 logcat —— 游戏启动瞬间日志量极大，logcat 的环形缓冲
 * （本机约 4 MiB）会把早期输出冲掉，"明明跑了却看不到日志" 由此而来。
 * 所以它把日志落盘到 mod 目录的 loader.log，这里直接读那个文件。
 */
public class LogActivity extends Activity {

    private TextView text;

    @Override
    protected void onCreate(Bundle b) {
        super.onCreate(b);
        setContentView(R.layout.activity_log);
        text = findViewById(R.id.logText);

        ((Button) findViewById(R.id.btnReload)).setOnClickListener(new View.OnClickListener() {
            @Override public void onClick(View v) { load(); }
        });
        ((Button) findViewById(R.id.btnShare)).setOnClickListener(new View.OnClickListener() {
            @Override public void onClick(View v) { exportLog(); }
        });
        load();
    }

    private void load() {
        String s = ModRepo.readLog();
        text.setText(s);

        // 顺手给一个"是否成功"的判断，省得用户去翻日志
        String verdict;
        if (s.contains("★★ 外载命中")) {
            verdict = "\n\n——— 检测到至少一次外载命中 ✅";
        } else if (s.contains("Permission denied")) {
            verdict = "\n\n——— 警告：出现 Permission denied，说明 mod 目录位置不对";
        } else if (s.contains("modsrv") && s.contains("ENTER")) {
            verdict = "\n\n——— 加载器已运行，但本次没有命中外部 mod";
        } else {
            verdict = "\n\n——— 还没有加载器日志：请确认装的是「加载器版」游戏，并已启动过";
        }
        text.append(verdict);
    }

    private void exportLog() {
        File src = ModRepo.logFile();
        if (!src.isFile()) { Toast.makeText(this, "没有可导出的日志", Toast.LENGTH_SHORT).show(); return; }
        try {
            File dst = new File(ModRepo.dir(), "loader-export.log");
            java.io.FileInputStream in = new java.io.FileInputStream(src);
            FileOutputStream out = new FileOutputStream(dst);
            byte[] buf = new byte[32 * 1024];
            int n;
            while ((n = in.read(buf)) > 0) out.write(buf, 0, n);
            out.close(); in.close();

            Intent i = new Intent(Intent.ACTION_SEND);
            i.setType("text/plain");
            i.putExtra(Intent.EXTRA_STREAM, android.net.Uri.fromFile(dst));
            startActivity(Intent.createChooser(i, "导出日志"));
        } catch (Exception e) {
            Toast.makeText(this, "导出失败：" + e.getMessage(), Toast.LENGTH_LONG).show();
        }
    }
}
