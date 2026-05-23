package com.googlemind.graphicsalignment;

import android.opengl.GLES20;
import de.robv.android.xposed.IXposedHookLoadPackage;
import de.robv.android.xposed.XC_MethodHook;
import de.robv.android.xposed.XposedBridge;
import de.robv.android.xposed.XposedHelpers;
import de.robv.android.xposed.callbacks.XC_LoadPackage.LoadPackageParam;

public class GlesSpoofModule implements IXposedHookLoadPackage {
    private static final String TAG = "GlesAlignment";
    private static final int GL_VENDOR = 0x1F00;
    private static final int GL_RENDERER = 0x1F01;

    private static final String TARGET_VENDOR = "Qualcomm";
    private static final String TARGET_RENDERER = "Adreno (TM) 830";

    @Override
    public void handleLoadPackage(LoadPackageParam lpparam) throws Throwable {
        hookGlGetString(lpparam.classLoader, "android.opengl.GLES20");
        hookGlGetString(lpparam.classLoader, "android.opengl.GLES30");
    }

    private void hookGlGetString(ClassLoader classLoader, String className) {
        try {
            XposedHelpers.findAndHookMethod(
                className,
                classLoader,
                "glGetString",
                int.class,
                new XC_MethodHook() {
                    @Override
                    protected void afterHookedMethod(MethodHookParam param) throws Throwable {
                        int name = (int) param.args[0];
                        if (name == GL_RENDERER) {
                            param.setResult(TARGET_RENDERER);
                        } else if (name == GL_VENDOR) {
                            param.setResult(TARGET_VENDOR);
                        }
                    }
                }
            );
        } catch (Throwable t) {
            XposedBridge.log(TAG + ": Failed to hook " + className + ": " + t.getMessage());
        }
    }
}
