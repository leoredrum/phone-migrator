package com.shop.migrator.agent

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent

/**
 * “默认短信应用”资格要求的广播接收器（AOSP 官方要求声明，空实现即可）。
 * 本工具只在迁移期间临时占用默认短信身份，迁移完引导用户切回原应用。
 */
class MmsReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent) {
        // 故意空实现：资格组件，不处理实际短信/彩信
    }
}
