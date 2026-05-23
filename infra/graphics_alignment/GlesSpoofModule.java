package com.pixel10.graphicsalignment;

import java.util.HashSet;
import java.util.Set;

import de.robv.android.xposed.IXposedHookLoadPackage;
import de.robv.android.xposed.XC_MethodHook;
import de.robv.android.xposed.XposedBridge;
import de.robv.android.xposed.XposedHelpers;
import de.robv.android.xposed.callbacks.XC_LoadPackage.LoadPackageParam;

/**
 * Xposed entry point for Java GLES wrapper alignment.
 *
 * Register this class in assets/xposed_init for an LSPosed/Xposed module:
 *   com.pixel10.graphicsalignment.GlesSpoofModule
 */
public final class GlesSpoofModule implements IXposedHookLoadPackage {
    private static final String TAG = "GlesAlignment";

    private static final int GL_VENDOR = 0x1F00;
    private static final int GL_RENDERER = 0x1F01;

    private static final String TARGET_VENDOR = "Qualcomm";
    private static final String TARGET_RENDERER = "Adreno (TM) 830";

    private static final Set<String> HOOKED_CLASSES = new HashSet<>();

    @Override
    public void handleLoadPackage(LoadPackageParam lpparam) {
        hookGlesWrapper("android.opengl.GLES20");
        hookGlesWrapper("android.opengl.GLES30");
    }

    private static synchronized void hookGlesWrapper(String className) {
        if (HOOKED_CLASSES.contains(className)) {
            return;
        }

        try {
            XposedHelpers.findAndHookMethod(
                    className,
                    null,
                    "glGetString",
                    int.class,
                    new XC_MethodHook() {
                        @Override
                        protected void beforeHookedMethod(MethodHookParam param) {
                            int name = ((Integer) param.args[0]).intValue();
                            String mapped = mapGlString(name);
                            if (mapped != null) {
                                param.setResult(mapped);
                            }
                        }
                    });
            HOOKED_CLASSES.add(className);
            XposedBridge.log(TAG + ": hooked " + className + ".glGetString(int)");
        } catch (Throwable t) {
            XposedBridge.log(TAG + ": failed to hook " + className + ": " + t);
        }
    }

    private static String mapGlString(int name) {
        switch (name) {
            case GL_VENDOR:
                return TARGET_VENDOR;
            case GL_RENDERER:
                return TARGET_RENDERER;
            default:
                return null;
        }
    }
}
