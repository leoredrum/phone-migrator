package com.shop.migrator.agent

import org.json.JSONObject
import java.io.ByteArrayOutputStream
import java.io.IOException
import java.io.InputStream
import java.io.OutputStream

/** 行式 JSON 协议的读写辅助。 */
object Proto {

    /** 读一行（按字节读到 \n，忽略 \r）。流结束时返回 null。 */
    fun readLine(ins: InputStream, max: Int = 64 * 1024): String? {
        val buf = ByteArrayOutputStream()
        while (true) {
            val b = ins.read()
            if (b == -1) {
                return if (buf.size() == 0) null else throw IOException("行被截断")
            }
            if (b == '\n'.code) break
            if (b != '\r'.code) {
                buf.write(b)
                if (buf.size() > max) throw IOException("请求行过长")
            }
        }
        return buf.toString("UTF-8")
    }

    fun readExact(ins: InputStream, n: Int): ByteArray {
        val out = ByteArray(n)
        var off = 0
        while (off < n) {
            val r = ins.read(out, off, n - off)
            if (r == -1) throw IOException("数据被截断")
            off += r
        }
        return out
    }

    fun writeJson(os: OutputStream, obj: JSONObject) {
        os.write((obj.toString() + "\n").toByteArray(Charsets.UTF_8))
        os.flush()
    }
}
