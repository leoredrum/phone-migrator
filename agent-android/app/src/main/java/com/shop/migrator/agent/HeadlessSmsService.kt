package com.shop.migrator.agent

import android.app.Service
import android.content.Intent
import android.os.IBinder

/** “默认短信应用”资格要求的服务（RESPOND_VIA_MESSAGE），空实现。 */
class HeadlessSmsService : Service() {
    override fun onBind(intent: Intent?): IBinder? = null
}
