package com.shop.migrator.agent

import android.content.ContentProviderOperation
import android.content.ContentValues
import android.content.Context
import android.provider.Telephony
import org.json.JSONArray
import org.json.JSONObject

/** 短信导出/导入。导入端必须是当前默认短信应用（Android 安全要求）。 */
object SmsIO {

    const val ERROR_NOT_DEFAULT = "not_default"

    fun isDefault(ctx: Context): Boolean =
        Telephony.Sms.getDefaultSmsPackage(ctx) == ctx.packageName

    fun export(ctx: Context): ByteArray {
        val arr = JSONArray()
        ctx.contentResolver.query(
            Telephony.Sms.CONTENT_URI,
            arrayOf(
                Telephony.Sms.ADDRESS, Telephony.Sms.DATE, Telephony.Sms.TYPE,
                Telephony.Sms.BODY, Telephony.Sms.READ),
            null, null, "${Telephony.Sms.DATE} ASC")?.use { c ->
            while (c.moveToNext()) {
                arr.put(JSONObject()
                    .put("a", c.getString(0) ?: "")
                    .put("d", c.getLong(1))
                    .put("t", c.getInt(2))
                    .put("b", c.getString(3) ?: "")
                    .put("r", c.getInt(4)))
            }
        }
        return arr.toString().toByteArray(Charsets.UTF_8)
    }

    /** @return Pair(写入条数, 跳过条数)。未持默认身份抛 IllegalStateException。 */
    fun import(ctx: Context, data: ByteArray): Pair<Int, Int> {
        if (!isDefault(ctx)) throw IllegalStateException(ERROR_NOT_DEFAULT)
        val arr = JSONArray(String(data, Charsets.UTF_8))
        var inserted = 0
        var skipped = 0
        // 分批 applyBatch，500 条一个事务，兼顾速度与内存
        val ops = ArrayList<ContentProviderOperation>()
        for (i in 0 until arr.length()) {
            val o = arr.getJSONObject(i)
            if (o.optString("a").isBlank()) {
                skipped++
                continue
            }
            val cv = ContentValues().apply {
                put(Telephony.Sms.ADDRESS, o.optString("a"))
                put(Telephony.Sms.DATE, o.getLong("d"))
                put(Telephony.Sms.TYPE, o.optInt("t", Telephony.Sms.MESSAGE_TYPE_SENT))
                put(Telephony.Sms.BODY, o.optString("b"))
                put(Telephony.Sms.READ, o.optInt("r", 1))
                put(Telephony.Sms.SEEN, 1)
                put(Telephony.Sms.STATUS, Telephony.Sms.STATUS_COMPLETE)
            }
            ops += ContentProviderOperation
                .newInsert(Telephony.Sms.CONTENT_URI)
                .withValues(cv)
                .build()
            if (ops.size >= 500) {
                inserted += applyBatch(ctx, ops)
                ops.clear()
            }
        }
        if (ops.isNotEmpty()) inserted += applyBatch(ctx, ops)
        return inserted to skipped
    }

    private fun applyBatch(ctx: Context, ops: List<ContentProviderOperation>): Int =
        runCatching {
            val authority = Telephony.Sms.CONTENT_URI.authority ?: "sms"
            ctx.contentResolver.applyBatch(authority, ArrayList(ops)).size
        }.getOrDefault(0)
}
