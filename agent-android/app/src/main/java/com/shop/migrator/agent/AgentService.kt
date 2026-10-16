package com.shop.migrator.agent

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.Service
import android.content.Intent
import android.media.MediaScannerConnection
import android.os.Build
import android.os.IBinder
import android.provider.Telephony
import org.json.JSONArray
import org.json.JSONObject
import java.io.File
import java.io.IOException
import java.io.OutputStream
import java.net.InetAddress
import java.net.ServerSocket
import java.net.Socket
import java.util.concurrent.Executors

/**
 * 前台服务：监听 127.0.0.1:7780，为 PC 端换机工作台提供数据通道。
 * PC 通过 `adb forward tcp:<port> tcp:7780` 接入。
 */
class AgentService : Service() {

    private var server: ServerSocket? = null
    private val pool = Executors.newCachedThreadPool()

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        startForegroundCompat()
        if (server == null || server!!.isClosed) {
            server = null
            pool.execute { serve() }
        }
        running = true
        return START_STICKY
    }

    override fun onDestroy() {
        running = false
        runCatching { server?.close() }
        pool.shutdownNow()
        super.onDestroy()
    }

    private fun serve() {
        try {
            val s = ServerSocket(PORT, 20, InetAddress.getByName("127.0.0.1"))
            server = s
            while (true) {
                val client = s.accept()
                pool.execute {
                    try {
                        handle(client)
                    } catch (_: IOException) {
                        // 连接中断属正常情况
                    } finally {
                        runCatching { client.close() }
                    }
                }
            }
        } catch (_: Exception) {
            // server 被关闭，退出循环
        }
    }

    private fun handle(client: Socket) {
        val ins = client.getInputStream()
        val os = client.getOutputStream()
        while (true) {
            val line = Proto.readLine(ins) ?: break
            val req = try {
                JSONObject(line)
            } catch (e: Exception) {
                Proto.writeJson(os, err("请求不是合法 JSON"))
                break
            }
            when (req.optString("cmd")) {
                "ping" -> Proto.writeJson(
                    os, JSONObject().put("ok", true)
                        .put("name", "migrator-agent").put("ver", 2))

                "contacts.export" -> replyBytes(os) {
                    ContactsIO.export(applicationContext)
                }

                "contacts.import" -> replyWithPayload(ins, os, req) {
                    val (added, skipped) =
                        ContactsIO.importVcf(applicationContext, it)
                    JSONObject().put("added", added).put("skipped", skipped)
                }

                "sms.status" -> {
                    val def = Telephony.Sms.getDefaultSmsPackage(applicationContext) ?: ""
                    Proto.writeJson(os, JSONObject().put("ok", true)
                        .put("is_default", def == packageName)
                        .put("default_pkg", def))
                }

                "sms.export" -> replyBytes(os) {
                    SmsIO.export(applicationContext)
                }

                "sms.import" -> replyWithPayload(ins, os, req) {
                    val (inserted, skipped) = SmsIO.import(applicationContext, it)
                    JSONObject().put("inserted", inserted).put("skipped", skipped)
                }

                "calls.export" -> replyBytes(os) {
                    CallsIO.export(applicationContext)
                }

                "calls.import" -> replyWithPayload(ins, os, req) {
                    val (inserted, skipped) = CallsIO.import(applicationContext, it)
                    JSONObject().put("inserted", inserted).put("skipped", skipped)
                }

                "media.scan" -> {
                    val paths = req.optJSONArray("paths") ?: JSONArray()
                    var count = 0
                    val files = ArrayList<String>()
                    for (i in 0 until paths.length()) {
                        val dir = File("/sdcard/${paths.optString(i)}")
                        if (dir.isDirectory) {
                            dir.walkTopDown()
                                .filter { it.isFile }
                                .forEach { files.add(it.absolutePath) }
                        }
                    }
                    files.chunked(300).forEach { chunk ->
                        MediaScannerConnection.scanFile(
                            applicationContext, chunk.toTypedArray(), null, null)
                        count += chunk.size
                    }
                    Proto.writeJson(os, JSONObject().put("ok", true).put("files", count))
                }

                "stop" -> {
                    Proto.writeJson(os, JSONObject().put("ok", true))
                    stopSelf()
                    return
                }

                else -> Proto.writeJson(
                    os, err("未知命令: ${req.optString("cmd")}"))
            }
        }
    }

    /** 导出类命令：回应答头 + 字节载荷。 */
    private inline fun replyBytes(os: OutputStream, body: () -> ByteArray) {
        val data = body()
        Proto.writeJson(os, JSONObject().put("ok", true).put("len", data.size))
        os.write(data)
        os.flush()
    }

    /** 导入类命令：读取载荷字节后执行，回 JSON 结果。 */
    private inline fun replyWithPayload(
        ins: java.io.InputStream, os: OutputStream,
        req: JSONObject, body: (ByteArray) -> JSONObject
    ) {
        val n = req.optInt("len", -1)
        if (n !in 1..256_000_000) {
            Proto.writeJson(os, err("bad len"))
            return
        }
        val data = Proto.readExact(ins, n)
        val result = try {
            body(data)
        } catch (e: IllegalStateException) {
            // 例如短信导入时未持默认身份
            Proto.writeJson(os, JSONObject().put("ok", false).put("error", e.message))
            return
        }
        Proto.writeJson(os, result.put("ok", true))
    }

    private fun err(msg: String): JSONObject =
        JSONObject().put("ok", false).put("error", msg)

    @Suppress("DEPRECATION")
    private fun startForegroundCompat() {
        val nm = getSystemService(NOTIFICATION_SERVICE) as NotificationManager
        val builder = if (Build.VERSION.SDK_INT >= 26) {
            val ch = NotificationChannel(CHANNEL_ID, "换机服务",
                NotificationManager.IMPORTANCE_LOW)
            nm.createNotificationChannel(ch)
            Notification.Builder(this, CHANNEL_ID)
        } else {
            Notification.Builder(this)
        }
        startForeground(1, builder
            .setContentTitle("换机助手 Agent")
            .setContentText("数据通道运行中（仅本机 127.0.0.1）")
            .setSmallIcon(android.R.drawable.stat_sys_download)
            .setOngoing(true)
            .build())
    }

    companion object {
        const val PORT = 7780
        private const val CHANNEL_ID = "migrator_agent"

        @Volatile
        var running = false
            private set
    }
}
