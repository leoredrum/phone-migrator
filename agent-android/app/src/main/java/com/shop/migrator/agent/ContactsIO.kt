package com.shop.migrator.agent

import android.content.ContentProviderOperation
import android.content.Context
import android.provider.ContactsContract
import android.provider.ContactsContract.CommonDataKinds.Email
import android.provider.ContactsContract.CommonDataKinds.Phone
import android.provider.ContactsContract.CommonDataKinds.StructuredName

/** 通讯录导出为 vCard 3.0 字节流 / 从 vCard 导入（按手机号去重）。 */
object ContactsIO {

    // ---------- 导出 ----------

    fun export(ctx: Context): ByteArray {
        val cr = ctx.contentResolver
        val byId = LinkedHashMap<Long, MutableContact>()

        cr.query(Phone.CONTENT_URI,
            arrayOf(Phone.CONTACT_ID, Phone.DISPLAY_NAME, Phone.NUMBER, Phone.TYPE),
            null, null, null)?.use { c ->
            while (c.moveToNext()) {
                val id = c.getLong(0)
                val name = c.getString(1) ?: ""
                val num = c.getString(2) ?: continue
                val type = when (c.getInt(3)) {
                    Phone.TYPE_HOME -> "HOME"
                    Phone.TYPE_WORK -> "WORK"
                    Phone.TYPE_MAIN -> "VOICE"
                    else -> "CELL"
                }
                byId.getOrPut(id) { MutableContact(name) }
                    .phones.add(num to type)
            }
        }
        cr.query(Email.CONTENT_URI,
            arrayOf(Email.CONTACT_ID, Email.ADDRESS),
            null, null, null)?.use { c ->
            while (c.moveToNext()) {
                val id = c.getLong(0)
                val addr = c.getString(1) ?: continue
                byId[id]?.emails?.add(addr)
            }
        }

        val sb = StringBuilder()
        for (c in byId.values) {
            val name = if (c.name.isBlank()) "未命名" else c.name
            sb.append("BEGIN:VCARD\nVERSION:3.0\n")
            sb.append("FN:").append(esc(name)).append('\n')
            sb.append("N:").append(esc(name)).append(";;;;\n")
            for ((num, type) in c.phones) {
                sb.append("TEL;TYPE=").append(type).append(':')
                    .append(esc(num)).append('\n')
            }
            for (e in c.emails) {
                sb.append("EMAIL;TYPE=INTERNET:").append(esc(e)).append('\n')
            }
            sb.append("END:VCARD\n")
        }
        return sb.toString().toByteArray(Charsets.UTF_8)
    }

    private class MutableContact(val name: String) {
        val phones = mutableListOf<Pair<String, String>>()
        val emails = mutableListOf<String>()
    }

    private fun esc(v: String): String = v
        .replace("\\", "\\\\")
        .replace(";", "\\;")
        .replace(",", "\\,")
        .replace("\n", "\\n")

    private fun unesc(v: String): String = v
        .replace("\\n", "\n")
        .replace("\\N", "\n")
        .replace("\\,", ",")
        .replace("\\;", ";")
        .replace("\\\\", "\\")

    // ---------- 导入 ----------

    fun importVcf(ctx: Context, data: ByteArray): Pair<Int, Int> {
        val cards = parse(String(data, Charsets.UTF_8))
        val existing = HashSet<String>()
        ctx.contentResolver.query(Phone.CONTENT_URI,
            arrayOf(Phone.NUMBER), null, null, null)?.use { c ->
            while (c.moveToNext()) c.getString(0)?.let { existing.add(norm(it)) }
        }

        var added = 0
        var skipped = 0
        for (card in cards) {
            val fresh = card.phones.filter { norm(it) !in existing }
            if (fresh.isEmpty()) {
                skipped++
                continue
            }
            val ops = ArrayList<ContentProviderOperation>()
            ops += ContentProviderOperation
                .newInsert(ContactsContract.RawContacts.CONTENT_URI)
                .withValue(ContactsContract.RawContacts.ACCOUNT_TYPE, null)
                .withValue(ContactsContract.RawContacts.ACCOUNT_NAME, null)
                .build()
            ops += ContentProviderOperation
                .newInsert(ContactsContract.Data.CONTENT_URI)
                .withValueBackReference(ContactsContract.Data.RAW_CONTACT_ID, 0)
                .withValue(ContactsContract.Data.MIMETYPE,
                    StructuredName.CONTENT_ITEM_TYPE)
                .withValue(StructuredName.DISPLAY_NAME,
                    if (card.name.isBlank()) "未命名" else card.name)
                .build()
            for (n in fresh) {
                ops += ContentProviderOperation
                    .newInsert(ContactsContract.Data.CONTENT_URI)
                    .withValueBackReference(ContactsContract.Data.RAW_CONTACT_ID, 0)
                    .withValue(ContactsContract.Data.MIMETYPE,
                        Phone.CONTENT_ITEM_TYPE)
                    .withValue(Phone.NUMBER, n)
                    .withValue(Phone.TYPE, Phone.TYPE_MOBILE)
                    .build()
                existing.add(norm(n))
            }
            for (e in card.emails) {
                ops += ContentProviderOperation
                    .newInsert(ContactsContract.Data.CONTENT_URI)
                    .withValueBackReference(ContactsContract.Data.RAW_CONTACT_ID, 0)
                    .withValue(ContactsContract.Data.MIMETYPE,
                        Email.CONTENT_ITEM_TYPE)
                    .withValue(Email.ADDRESS, e)
                    .withValue(Email.TYPE, Email.TYPE_OTHER)
                    .build()
            }
            val res = runCatching {
                ctx.contentResolver.applyBatch(ContactsContract.AUTHORITY, ops)
            }
            if (res.isSuccess) added++ else skipped++
        }
        return added to skipped
    }

    /** 归一化：只留数字；超过 11 位取末 11 位（兼容 +86 前缀写法）。 */
    private fun norm(n: String): String {
        val d = n.filter { it.isDigit() }
        return if (d.length > 11) d.takeLast(11) else d
    }

    // ---------- vCard 解析（覆盖自家导出格式 + 常见第三方格式） ----------

    private class Card {
        var name = ""
        val phones = mutableListOf<String>()
        val emails = mutableListOf<String>()
    }

    private fun parse(text: String): List<Card> {
        val out = mutableListOf<Card>()
        var cur: Card? = null
        for (raw in unfold(text)) {
            val line = raw.trim()
            if (line.isEmpty()) continue
            if (line.equals("BEGIN:VCARD", true)) {
                cur = Card(); continue
            }
            if (line.equals("END:VCARD", true)) {
                cur?.let { out.add(it) }; cur = null; continue
            }
            val c = cur ?: continue
            val colon = line.indexOf(':')
            if (colon < 0) continue
            val prop = line.substring(0, colon).uppercase()
            val value = unesc(line.substring(colon + 1).trim())
            when {
                prop == "FN" -> if (c.name.isBlank()) c.name = value
                prop == "N" || prop.startsWith("N;") ->
                    if (c.name.isBlank()) c.name = value.split(';').first()
                prop.startsWith("TEL") ->
                    if (value.isNotBlank()) c.phones.add(value)
                prop.startsWith("EMAIL") ->
                    if (value.isNotBlank()) c.emails.add(value)
            }
        }
        return out
    }

    /** RFC 折行合并：以空格/制表符开头的行接续上一行。 */
    private fun unfold(text: String): List<String> {
        val res = mutableListOf<String>()
        for (line in text.split("\n")) {
            val l = line.trimEnd('\r')
            if (res.isNotEmpty() && (l.startsWith(" ") || l.startsWith("\t"))) {
                res[res.size - 1] += l.substring(1)
            } else {
                res.add(l)
            }
        }
        return res
    }
}
