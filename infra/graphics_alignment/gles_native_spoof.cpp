#include <android/log.h>
#include <dlfcn.h>
#include <jni.h>
#include <stdint.h>

#include "dobby.h"

#define LOG_TAG "GlesNativeAlignment"
#define ALOGI(...) __android_log_print(ANDROID_LOG_INFO, LOG_TAG, __VA_ARGS__)
#define ALOGE(...) __android_log_print(ANDROID_LOG_ERROR, LOG_TAG, __VA_ARGS__)

static constexpr uint32_t GL_VENDOR_VALUE = 0x1F00;
static constexpr uint32_t GL_RENDERER_VALUE = 0x1F01;

static const char* kTargetVendor = "Qualcomm";
static const char* kTargetRenderer = "Adreno (TM) 830";

using GlGetStringFn = const unsigned char* (*)(uint32_t name);

static GlGetStringFn g_original_gl_get_string = nullptr;
static bool g_hook_installed = false;

static const unsigned char* aligned_gl_get_string(uint32_t name) {
    switch (name) {
        case GL_VENDOR_VALUE:
            return reinterpret_cast<const unsigned char*>(kTargetVendor);
        case GL_RENDERER_VALUE:
            return reinterpret_cast<const unsigned char*>(kTargetRenderer);
        default:
            if (g_original_gl_get_string != nullptr) {
                return g_original_gl_get_string(name);
            }
            return reinterpret_cast<const unsigned char*>("");
    }
}

static void* open_gles_library() {
    const char* candidates[] = {
        "libGLESv2.so",
        "libGLESv3.so",
        "/system/lib64/libGLESv2.so",
        "/vendor/lib64/egl/libGLES_swiftshader.so",
    };

    for (const char* path : candidates) {
        void* handle = dlopen(path, RTLD_NOW | RTLD_LOCAL);
        if (handle != nullptr) {
            ALOGI("opened GLES library: %s", path);
            return handle;
        }
    }
    return nullptr;
}

extern "C" bool install_gles_alignment_hooks() {
    if (g_hook_installed) {
        return true;
    }

    void* handle = open_gles_library();
    if (handle == nullptr) {
        ALOGE("failed to open a GLES library");
        return false;
    }

    void* target = dlsym(handle, "glGetString");
    if (target == nullptr) {
        ALOGE("failed to resolve glGetString");
        return false;
    }

    int status = DobbyHook(
            target,
            reinterpret_cast<dobby_dummy_func_t>(aligned_gl_get_string),
            reinterpret_cast<dobby_dummy_func_t*>(&g_original_gl_get_string));

    if (status != 0) {
        ALOGE("DobbyHook(glGetString) failed: %d", status);
        return false;
    }

    g_hook_installed = true;
    ALOGI("glGetString native alignment hook installed");
    return true;
}

__attribute__((constructor))
static void on_library_loaded() {
    install_gles_alignment_hooks();
}

extern "C" JNIEXPORT jint JNICALL JNI_OnLoad(JavaVM* vm, void*) {
    JNIEnv* env = nullptr;
    if (vm->GetEnv(reinterpret_cast<void**>(&env), JNI_VERSION_1_6) != JNI_OK) {
        return JNI_ERR;
    }
    install_gles_alignment_hooks();
    return JNI_VERSION_1_6;
}
