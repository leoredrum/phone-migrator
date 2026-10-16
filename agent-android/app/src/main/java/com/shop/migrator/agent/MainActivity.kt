package com.shop.migrator.agent

import android.app.Activity
import android.content.Intent
import android.content.pm.PackageManager
import android.os.Build
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.provider.Telephony
import android.widget.Button
import android.widget.LinearLayout
import android.widget.TextView

/**
 * 状态页 + 手动启停 + 短信迁移引导。
 * 正常流程由 PC 端 adb 自动启动服务，客户一般不需要打开这个界面；
 * 只有迁移短信时，需要在新机上点一次“设为默认短信应用”。
 */
class MainActivity : Activity() {

    private lateinit var status: TextView
    private lateinit var smsStatus: TextView
    private val ui = Handler(Looper.getMainLooper())
    private val ticker = object : Runnable {
        override fun run() {
            refreshStatus()
            ui.postDelayed(this, 1000)
        }
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        val pad = (resources.displayMetrics.density * 16).toInt()
        val root = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(pad, pad, pad, pad)
        }
        status = TextView(this).apply { textSize = 18f }
        smsStatus = TextView(this).apply { textSize = 15f; setPadding(0, pad, 0, 0) }

        val hint = TextView(this).apply {
            textSize = 13f
            text = "店内使用说明：\n" +
                "1. 电脑端「换机工作台」会通过 USB 自动安装并启动本 agent\n" +
                "2. 通讯录/通话记录/短信导出不需要任何操作\n" +
                "3. 只有把短信写进新机时，才需要点下方“设为默认短信应用”\n" +
                "4. 短信迁完记得点“切回原短信应用”\n" +
                "5. 迁移过程中请保持屏幕解锁、不要拔线"
            setPadding(0, pad / 2, 0, 0)
        }

        val start = Button(this).apply {
            text = "启动服务"
            setOnClickListener { startAgent() }
        }
        val stop = Button(this).apply {
            text = "停止服务"
            setOnClickListener {
                stopService(Intent(this@MainActivity, AgentService::class.java))
            }
        }
        val setDefault = Button(this).apply {
            text = "设为默认短信应用（迁移短信用）"
            setOnClickListener { requestDefaultSms() }
        }
        val restoreDefault = Button(this).apply {
            text = "切回原短信应用"
            setOnClickListener { restoreDefaultSms() }
        }

        root.addView(status)
        root.addView(start)
        root.addView(stop)
        root.addView(smsStatus)
        root.addView(setDefault)
        root.addView(restoreDefault)
        root.addView(hint)
        setContentView(root)
    }

    override fun onResume() {
        super.onResume()
        ticker.run()
    }

    override fun onPause() {
        super.onPause()
        ui.removeCallbacks(ticker)
    }

    private fun startAgent() {
        if (Build.VERSION.SDK_INT >= 33 &&
            checkSelfPermission(android.Manifest.permission.POST_NOTIFICATIONS) !=
            PackageManager.PERMISSION_GRANTED
        ) {
            requestPermissions(
                arrayOf(android.Manifest.permission.POST_NOTIFICATIONS), 1)
        }
        val intent = Intent(this, AgentService::class.java)
        if (Build.VERSION.SDK_INT >= 26) {
            startForegroundService(intent)
        } else {
            startService(intent)
        }
    }

    /** 记下当前默认短信应用（用于迁完切回），然后请求把我们设为默认。 */
    private fun requestDefaultSms() {
        val current = Telephony.Sms.getDefaultSmsPackage(this)
        if (current == packageName) {
            smsStatus.text = "✅ 已经是默认短信应用，可以开始写入短信"
            return
        }
        if (current != null) {
            getSharedPreferences("sms", MODE_PRIVATE).edit()
                .putString("prev_default", current).apply()
        }
        smsStatus.text = "已记录原应用（$current），请在系统弹窗中点“确认”"
        fireChangeDefault(packageName)
    }

    private fun restoreDefaultSms() {
        val prev = getSharedPreferences("sms", MODE_PRIVATE)
            .getString("prev_default", null)
        if (prev == null) {
            smsStatus.text = "没有记录到原短信应用；可在 设置→应用→默认应用 里手动改回"
            return
        }
        fireChangeDefault(prev)
        getSharedPreferences("sms", MODE_PRIVATE).edit()
            .remove("prev_default").apply()
        smsStatus.text = "已请求切回 $prev"
    }

    private fun fireChangeDefault(pkg: String) {
        val i = Intent(Telephony.Sms.Intents.ACTION_CHANGE_DEFAULT)
        i.putExtra(Telephony.Sms.Intents.EXTRA_PACKAGE_NAME, pkg)
        i.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
        runCatching { startActivity(i) }
    }

    private fun refreshStatus() {
        status.text = if (AgentService.running) {
            "● 服务运行中（127.0.0.1:${AgentService.PORT}）"
        } else {
            "○ 服务未运行"
        }
        val def = Telephony.Sms.getDefaultSmsPackage(this)
        smsStatus.text = when {
            def == packageName -> "✅ 当前是默认短信应用（短信迁移模式）"
            def != null -> "短信默认应用：$def"
            else -> "短信默认应用：未设置"
        }
    }
}
