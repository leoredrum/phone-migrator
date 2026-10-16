plugins {
    id("com.android.application")
}

android {
    namespace = "com.shop.migrator.agent"
    compileSdk = 34

    defaultConfig {
        applicationId = "com.shop.migrator.agent"
        minSdk = 24
        targetSdk = 34
        versionCode = 2
        versionName = "0.2.0"
    }

    buildTypes {
        release {
            isMinifyEnabled = false
        }
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
}
