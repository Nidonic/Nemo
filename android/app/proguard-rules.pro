# kotlinx.serialization
-keepattributes *Annotation*, InnerClasses
-dontnote kotlinx.serialization.**
-keepclassmembers class dev.nemo.android.** {
    *** Companion;
}
-keepclasseswithmembers class dev.nemo.android.** {
    kotlinx.serialization.KSerializer serializer(...);
}
# OkHttp
-dontwarn okhttp3.internal.platform.**
-dontwarn org.conscrypt.**
-dontwarn org.bouncycastle.**
-dontwarn org.openjsse.**
