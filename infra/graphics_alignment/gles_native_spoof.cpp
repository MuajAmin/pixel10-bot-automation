#include <jni.h>
#include <string.h>
#include <android/log.h>
#include "dobby.h"

#define LOG_TAG "GlesNativeAlignment"
#define ALOGI(...) __android_log_print(ANDROID_LOG_INFO, LOG_TAG, __VA_ARGS__)

#define GL_VENDOR 0x1F00
#define GL_RENDERER 0x1F01

// Original function pointer
const char* (*orig_glGetString)(unsigned int name) = nullptr;

// Detour implementation
const char* hk_glGetString(unsigned int name) {
    if (name == GL_RENDERER) {
        return "Adreno (TM) 830";
    }
    if (name == GL_VENDOR) {
        return "Qualcomm";
    }
    if (orig_glGetString) {
        return orig_glGetString(name);
    }
    return "";
}

// Initialization function called on library load
void install_gles_hooks() {
    ALOGI("Initializing GLES native spoof hooks...");
    
    // Resolve libGLESv2 handle
    void* gles_handle = dobby_dlopen("libGLESv2.so");
    if (!gles_handle) {
        ALOGI("Failed to load libGLESv2.so");
        return;
    }

    // Resolve glGetString symbol
    void* target_fn = dobby_dlsym(gles_handle, "glGetString");
    if (!target_fn) {
        ALOGI("Failed to resolve glGetString symbol in libGLESv2.so");
        return;
    }

    // Hook the function
    int status = DobbyHook(target_fn, (dobby_dummy_func_t)hk_glGetString, (dobby_dummy_func_t*)&orig_glGetString);
    if (status == 0) {
        ALOGI("Successfully hooked glGetString");
    } else {
        ALOGI("Failed to hook glGetString, error code: %d", status);
    }
}

// JNI initialization stub if loaded via System.loadLibrary
JNIEXPORT jint JNI_OnLoad(JavaVM* vm, void* reserved) {
    JNIEnv* env;
    if (vm->GetEnv((void**)&env, JNI_VERSION_1_6) != JNI_OK) {
        return JNI_ERR;
    }
    install_gles_hooks();
    return JNI_VERSION_1_6;
}
