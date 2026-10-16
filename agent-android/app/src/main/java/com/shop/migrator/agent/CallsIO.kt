package com.shop.migrator.agent

import android.content.ContentProviderOperation
import android.content.ContentValues
import android.content.Context
import android.provider.CallLog
import org.json.JSONArray
import org.json.JSONObject

/** 通话记录导出/导入。 */
object CallsIO {

    fun export(ctx: Context): ByteArray {
        val arr = JSONArray()
        ctx.contentResolver.query(
            CallLog.Calls.CONTENT_URI,
            arrayOf(
                CallLog.Calls.NUMBER, CallLog.Calls.DATE, CallLog.Calls.DURATION,
                CallLog.Calls.TYPE, CallLog.Calls.CACHED_NAME),
            null, null, "${CallLog.Calls.DATE} ASC")?.use { c ->
            while (c.moveToNext()) {
                arr.put(JSONObject()
                    .put("n", c.getString(0) ?: "")
                    .put("d", c.getLong(1))
                    .put("dur", c.getLong(2))
                    .put("t", c.getInt(3))
                    .put("name", c.getString(4) ?: ""))
            }
        }
        return arr.toString().toByteArray(Charsets.UTF_8)
    }

    fun import(ctx: Context, data: ByteArray): Pair<Int, Int> {
        val arr = JSONArray(String(data, Charsets.UTF_8))
        var inserted = 0
        var skipped = 0
        val ops = ArrayList<ContentProviderOperation>()
        for (i in 0 until arr.length()) {
            val o = arr.getJSONObject(i)
            if (o.optString("n").isBlank()) {
                skipped++
                continue
            }
            val cv = ContentValues().apply {
                put(CallLog.Calls.NUMBER, o.optString("n"))
                put(CallLog.Calls.DATE, o.getLong("d"))
                put(CallLog.Calls.DURATION, o.optLong("dur"))
                put(CallLog.Calls.TYPE, o.optInt("t", CallLog.Calls.OUTGOING_TYPE))
                if (o.optString("name").isNotBlank()) {
                    put(CallLog.Calls.CACHED_NAME, o.optString("name"))
                }
                put(CallLog.Calls.NEW, 0)
            }
            ops += ContentProviderOperation
                .newInsert(CallLog.Calls.CONTENT_URI)
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
            ctx.contentResolver.applyBatch(CallLog.AUTHORITY, ArrayList(ops)).size
        }.getOrDefault(0)
}
