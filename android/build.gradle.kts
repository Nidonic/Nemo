plugins {
    id("com.android.application") version "8.11.1" apply false
    id("org.jetbrains.kotlin.android") version "2.1.21" apply false
    id("org.jetbrains.kotlin.plugin.compose") version "2.1.21" apply false
    id("org.jetbrains.kotlin.plugin.serialization") version "2.1.21" apply false
}

// `-PnemoBuildRoot=D:/somewhere` moves every build directory off the source drive
providers.gradleProperty("nemoBuildRoot").orNull?.let { root ->
    allprojects {
        layout.buildDirectory.set(file("$root/${project.name}"))
    }
}
