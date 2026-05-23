# GLES Graphics Alignment

This directory contains the active GLES context alignment layers:

- `GlesSpoofModule.java`: Xposed entry point for Java wrapper calls through
  `android.opengl.GLES20.glGetString(int)` and `GLES30.glGetString(int)`.
- `gles_native_spoof.cpp`: Dobby-based native detour for `glGetString` in the
  GLES runtime layer.
- `patch_driver.py`: static ELF string patcher for
  `/vendor/lib64/egl/libGLES_swiftshader.so`.

Target mappings:

- `GL_VENDOR` (`0x1F00`) -> `Qualcomm`
- `GL_RENDERER` (`0x1F01`) -> `Adreno (TM) 830`

Example static patch command inside the container:

```sh
python3 /data/local/tmp/patch_driver.py \
  --file /vendor/lib64/egl/libGLES_swiftshader.so \
  --search "Google SwiftShader" \
  --replace "Adreno (TM) 830"
```
