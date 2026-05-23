"""ADB & uiautomator2 device management for ReDroid containers.

Handles connecting to the Android container, installing required apps,
taking screenshots, and health checks.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from typing import Any

import uiautomator2 as u2

from .config import (
    ADB_COMMAND_TIMEOUT_SEC,
    ADB_CONNECT_TIMEOUT_SEC,
    ADB_HOST,
    ADB_PORT,
    ADB_RECONNECT_INTERVAL_SEC,
    APP_LAUNCH_TIMEOUT,
    BOOT_WAIT_TIMEOUT,
    PKG_GOOGLE_ONE,
    PKG_PLAY_SERVICES,
    PKG_PLAY_STORE,
    SCREENSHOT_DIR,
)

logger = logging.getLogger(__name__)

# Screenshots disabled by default — enable via SCREENSHOTS_ENABLED=true in api.env
_SCREENSHOTS_ENABLED = os.getenv("SCREENSHOTS_ENABLED", "false").strip().lower() in (
    "true", "1", "yes",
)


# ── Shared Screen Text Helper ────────────────────────────────────


async def get_screen_text(device: u2.Device) -> str:
    """Get all visible text from the current screen (lowercased).

    This is the canonical implementation — used by google_login,
    offer_claim, and security_alert modules.
    """
    try:
        dump = await asyncio.to_thread(device.dump_hierarchy)
        import re
        texts = re.findall(r'text="([^"]*)"', dump)
        return " ".join(texts).lower()
    except Exception:
        return ""


# ── Device Connection ────────────────────────────────────────────


async def adb_connect(addr: str) -> bool:
    """Run a bounded adb connect before uiautomator2 connects."""
    addr = normalize_adb_addr(addr)
    process: asyncio.subprocess.Process | None = None
    try:
        process = await asyncio.create_subprocess_exec(
            "adb",
            "connect",
            addr,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(
            process.communicate(),
            timeout=ADB_COMMAND_TIMEOUT_SEC,
        )
        output = (stdout + stderr).decode("utf-8", errors="replace").strip()
        if output:
            logger.debug("adb connect %s: %s", addr, output)
        return process.returncode == 0
    except asyncio.TimeoutError:
        if process is not None:
            try:
                process.kill()
            except (ProcessLookupError, OSError):
                pass
            await process.wait()
        logger.debug("adb connect %s timed out after %ss", addr, ADB_COMMAND_TIMEOUT_SEC)
        return False
    except OSError as exc:
        logger.debug("adb connect %s failed: %s", addr, exc)
        return False


def normalize_adb_addr(addr: str) -> str:
    """Use one stable ADB serial so localhost and 127.0.0.1 do not split state."""
    if addr.startswith("localhost:"):
        return "127.0.0.1:" + addr.rsplit(":", 1)[1]
    return addr


async def run_adb(*args: str, timeout: int | None = None) -> tuple[int, str]:
    process = await asyncio.create_subprocess_exec(
        "adb",
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(
            process.communicate(),
            timeout=timeout or ADB_COMMAND_TIMEOUT_SEC,
        )
    except asyncio.TimeoutError:
        try:
            process.kill()
        except (ProcessLookupError, OSError):
            pass
        await process.wait()
        return 124, ""

    output = (stdout + stderr).decode("utf-8", errors="replace")
    return process.returncode or 0, output


async def adb_transport_state(addr: str) -> str:
    addr = normalize_adb_addr(addr)
    rc, output = await run_adb("devices", timeout=5)
    if rc != 0:
        return "unknown"

    for line in output.splitlines()[1:]:
        parts = line.split()
        if len(parts) >= 2 and parts[0] == addr:
            return parts[1]
    return "missing"


async def reset_stale_adb_transport(addr: str) -> None:
    addr = normalize_adb_addr(addr)
    targets = {addr, addr.replace("127.0.0.1:", "localhost:")}
    for target in targets:
        await run_adb("disconnect", target, timeout=5)
    await run_adb("kill-server", timeout=5)


async def adb_shell_prop(addr: str, prop: str) -> str:
    addr = normalize_adb_addr(addr)
    rc, output = await run_adb("-s", addr, "shell", "getprop", prop, timeout=5)
    if rc == 0:
        return output.strip().replace("\r", "")
    return ""


async def connect_device(
    host: str = ADB_HOST,
    port: int = ADB_PORT,
    timeout: int = ADB_CONNECT_TIMEOUT_SEC,
) -> u2.Device:
    """Connect to the ReDroid container via ADB and return a u2 device handle.

    Retries until the device is ready or *timeout* seconds elapse.
    """
    addr = normalize_adb_addr(f"{host}:{port}")
    logger.info("Connecting to Android device at %s ...", addr)

    deadline = time.time() + timeout
    last_err: Exception | None = None
    last_reset = 0.0
    last_state = ""
    attempt = 0

    while time.time() < deadline:
        attempt += 1
        try:
            if not await adb_connect(addr):
                state = await adb_transport_state(addr)
                if state != last_state:
                    logger.warning("ADB transport for %s is %s", addr, state)
                    last_state = state
                if state in ("offline", "unauthorized") or time.time() - last_reset > 30:
                    await reset_stale_adb_transport(addr)
                    last_reset = time.time()
                raise ConnectionError(f"adb transport is {state}")

            boot = await adb_shell_prop(addr, "sys.boot_completed")
            if boot != "1":
                raise ConnectionError(
                    f"Android boot not complete yet (sys.boot_completed={boot or 'empty'})"
                )

            device = await asyncio.to_thread(u2.connect, addr)
            # Verify the device is actually responsive
            # device.info is a property — must access it inside the thread
            def _get_info(d: u2.Device) -> dict:
                return d.info  # type: ignore[return-value]
            info = await asyncio.to_thread(_get_info, device)
            logger.info(
                "Connected to device: %s (screen %sx%s)",
                info.get("productName", "unknown"),
                info.get("displayWidth", "?"),
                info.get("displayHeight", "?"),
            )
            return device
        except Exception as exc:
            last_err = exc
            if attempt % 6 == 0:
                await reset_stale_adb_transport(addr)
                last_reset = time.time()
            logger.debug("Device not ready: %s; retrying in %ss", exc, ADB_RECONNECT_INTERVAL_SEC)
            await asyncio.sleep(ADB_RECONNECT_INTERVAL_SEC)

    raise ConnectionError(
        f"Could not connect to Android device at {addr} "
        f"after {timeout}s: {last_err}"
    )


async def wait_for_boot(device: u2.Device, timeout: int = BOOT_WAIT_TIMEOUT) -> None:
    """Wait until the Android system finishes booting."""
    logger.info("Waiting for Android boot to complete...")
    deadline = time.time() + timeout

    while time.time() < deadline:
        try:
            boot_completed = await asyncio.to_thread(
                device.shell, "getprop sys.boot_completed"
            )
            if boot_completed.output.strip() == "1":
                logger.info("Android boot completed.")
                return
        except Exception:
            pass
        await asyncio.sleep(3)

    raise TimeoutError(f"Android did not finish booting within {timeout}s")


# ── App Management ───────────────────────────────────────────────


async def is_app_installed(device: u2.Device, package: str) -> bool:
    """Check if a package is installed on the device."""
    try:
        result = await asyncio.to_thread(
            device.shell, f"pm list packages {package}"
        )
        return package in result.output
    except Exception:
        return False


async def ensure_apps_installed(device: u2.Device) -> dict[str, bool]:
    """Check that critical apps are installed and return status dict."""
    apps = {
        "play_store": PKG_PLAY_STORE,
        "play_services": PKG_PLAY_SERVICES,
        "google_one": PKG_GOOGLE_ONE,
    }
    status = {}
    for name, pkg in apps.items():
        installed = await is_app_installed(device, pkg)
        status[name] = installed
        if installed:
            logger.info("✅ %s (%s) installed", name, pkg)
        else:
            logger.warning("❌ %s (%s) NOT installed", name, pkg)

    return status


async def launch_app(
    device: u2.Device,
    package: str,
    timeout: int = APP_LAUNCH_TIMEOUT,
) -> None:
    """Launch an app and wait for it to become the foreground activity."""
    logger.info("Launching %s ...", package)
    await asyncio.to_thread(device.app_start, package)

    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            current = await asyncio.to_thread(device.app_current)
            if current.get("package") == package:
                logger.info("App %s is in foreground.", package)
                return
        except Exception:
            pass
        await asyncio.sleep(1)

    logger.warning("App %s did not become foreground within %ds", package, timeout)


async def stop_app(device: u2.Device, package: str) -> None:
    """Force-stop an app."""
    try:
        await asyncio.to_thread(device.app_stop, package)
    except Exception:
        pass


async def clear_app_data(device: u2.Device, package: str) -> None:
    """Clear all data for an app (cache, settings, accounts)."""
    logger.info("Clearing data for %s", package)
    await asyncio.to_thread(device.shell, f"pm clear {package}")


# ── Screenshots ──────────────────────────────────────────────────


async def take_screenshot(
    device: u2.Device,
    name: str,
    job_id: str = "",
) -> str:
    """Capture a screenshot from the Android device and save it.

    Returns the absolute path to the saved screenshot, or "" if disabled/failed.
    """
    if not _SCREENSHOTS_ENABLED:
        return ""
    os.makedirs(SCREENSHOT_DIR, exist_ok=True)
    timestamp = int(time.time())
    prefix = f"{job_id}_" if job_id else ""
    filename = f"{prefix}{name}_{timestamp}.png"
    filepath = os.path.join(SCREENSHOT_DIR, filename)
    try:
        img = await asyncio.to_thread(device.screenshot)
        img.save(filepath)
        logger.info("Screenshot saved: %s", filepath)
        return filepath
    except Exception as exc:
        logger.warning("Failed to take screenshot: %s", exc)
        return ""


# ── Device Info & Health ─────────────────────────────────────────


async def device_health_check(device: u2.Device) -> dict[str, Any]:
    """Perform a health check on the Android device.

    Returns a dict with device status, installed apps, and Play Integrity info.
    """
    health: dict[str, Any] = {
        "connected": False,
        "booted": False,
        "apps": {},
        "device_model": "",
        "android_version": "",
    }

    try:
        # device.info is a property — access it inside the worker thread
        def _get_dev_info(d: u2.Device) -> dict:
            return d.info  # type: ignore[return-value]
        info = await asyncio.to_thread(_get_dev_info, device)
        health["connected"] = True
        health["device_model"] = info.get("productName", "unknown")

        # Check boot status
        boot = await asyncio.to_thread(device.shell, "getprop sys.boot_completed")
        health["booted"] = boot.output.strip() == "1"

        # Check Android version
        version = await asyncio.to_thread(
            device.shell, "getprop ro.build.version.release"
        )
        health["android_version"] = version.output.strip()

        # Check device model (verify spoofing)
        model = await asyncio.to_thread(device.shell, "getprop ro.product.model")
        health["device_model"] = model.output.strip()

        # Check apps
        health["apps"] = await ensure_apps_installed(device)

    except Exception as exc:
        logger.warning("Health check failed: %s", exc)

    return health


async def get_device_properties(device: u2.Device) -> dict[str, str]:
    """Fetch key system properties to verify Pixel spoofing."""
    props_to_check = [
        "ro.product.model",
        "ro.product.device",
        "ro.product.brand",
        "ro.build.fingerprint",
        "ro.build.display.id",
        "ro.build.version.release",
        "ro.build.version.sdk",
        "ro.hardware",
    ]
    result = {}
    for prop in props_to_check:
        try:
            val = await asyncio.to_thread(device.shell, f"getprop {prop}")
            result[prop] = val.output.strip()
        except Exception:
            result[prop] = "ERROR"

    return result


# ── Hardening & Integrity ────────────────────────────────────────


async def verify_anti_emulator_props(device: u2.Device) -> dict[str, Any]:
    """Check that all anti-emulator properties are set correctly.

    Returns a dict with property values and a boolean 'passed' field.
    """
    checks = {
        "ro.kernel.qemu": "0",
        "ro.debuggable": "0",
        "ro.secure": "1",
        "ro.boot.verifiedbootstate": "green",
        "ro.boot.flash.locked": "1",
        "ro.boot.vbmeta.device_state": "locked",
        "ro.build.type": "user",
        "ro.build.tags": "release-keys",
    }
    results: dict[str, Any] = {"passed": True, "props": {}}

    for prop, expected in checks.items():
        try:
            val = await asyncio.to_thread(device.shell, f"getprop {prop}")
            actual = val.output.strip()
            ok = actual == expected
            results["props"][prop] = {"actual": actual, "expected": expected, "ok": ok}
            if not ok:
                results["passed"] = False
                logger.warning(
                    "Anti-emulator prop mismatch: %s=%s (expected %s)",
                    prop, actual, expected,
                )
        except Exception:
            results["props"][prop] = {"actual": "ERROR", "expected": expected, "ok": False}
            results["passed"] = False

    return results


async def apply_runtime_props(device: u2.Device) -> None:
    """Apply critical anti-emulator and Pixel identity props via resetprop.

    Called at job start to ensure all props are correct, even if the
    Magisk module's service.sh missed some or they were reset.
    Uses Magisk's ``resetprop`` for read-only properties.
    """
    # ── Pixel 5 (redfin) / Android 11 / SDK 30 ────────────────
    # Identity must match the actual ReDroid Android 11 runtime.
    # Using Pixel 5 (redfin) prevents the fatal SDK mismatch that
    # occurs when spoofing newer devices (e.g. Pixel 9 Pro / SDK 34)
    # on an Android 11 container.
    props = {
        # Anti-emulator
        "ro.kernel.qemu": "0",
        "ro.kernel.qemu.gles": "0",
        "ro.debuggable": "0",
        "ro.secure": "1",
        "ro.adb.secure": "1",
        "ro.boot.verifiedbootstate": "green",
        "ro.boot.flash.locked": "1",
        "ro.boot.vbmeta.device_state": "locked",
        "ro.build.type": "user",
        "ro.build.tags": "release-keys",
        "sys.oem_unlock_allowed": "0",
        "ro.crypto.state": "encrypted",
        "ro.crypto.type": "file",
        "ro.force.debuggable": "0",
        # Pixel 5 identity — system partition (critical: was "generic")
        "ro.product.system.device": "redfin",
        "ro.product.system.model": "Pixel 5",
        "ro.product.system.name": "redfin",
        "ro.product.system.brand": "google",
        "ro.product.system.manufacturer": "Google",
        "ro.build.flavor": "redfin-user",
        "ro.build.description": "redfin-user 11 RQ3A.210805.001.A1 7474174 release-keys",
        "ro.build.fingerprint": "google/redfin/redfin:11/RQ3A.210805.001.A1/7474174:user/release-keys",
        "ro.build.version.sdk": "30",
        "ro.build.version.release": "11",
        # Device provisioning
        "persist.sys.device_provisioned": "1",
        # Hardware identity (Snapdragon 765G / lito)
        "ro.boot.hardware.revision": "MP1.0",
        "ro.boot.slot_suffix": "_a",
        "ro.boot.hardware": "redfin",
        "ro.hardware": "redfin",
        "ro.board.platform": "lito",
        "ro.boot.hardware.sku": "redfin",
        "ro.boot.product.hardware.sku": "redfin",
    }

    # Only delete ISA-mapping props that expose emulator.
    # CRITICAL: Do NOT delete ro.dalvik.vm.native.bridge — the
    # container needs it for ARM app translation (libndk).
    props_to_delete = [
        "ro.dalvik.vm.isa.arm",
        "ro.dalvik.vm.isa.arm64",
    ]

    applied = 0
    for prop, value in props.items():
        try:
            await asyncio.to_thread(
                device.shell,
                f"/sbin/su -c 'resetprop {prop} {value}'",
            )
            applied += 1
        except Exception:
            # Fallback: try setprop for non-read-only props
            try:
                await asyncio.to_thread(
                    device.shell,
                    f"setprop {prop} {value}",
                )
                applied += 1
            except Exception:
                pass

    for prop in props_to_delete:
        try:
            await asyncio.to_thread(
                device.shell,
                f"/sbin/su -c 'resetprop --delete {prop}'",
            )
        except Exception:
            pass

    logger.info("Applied %d/%d runtime props (Pixel 5/redfin/SDK30)", applied, len(props))


async def get_gsf_id(device: u2.Device) -> str:
    """Extract the Google Service Framework ID (for uncertified device registration).

    Returns the decimal GSF ID string, or "" if extraction fails.
    """
    def _is_valid_gsf(val: str) -> bool:
        """GSF android_id may be decimal or hex — accept both."""
        if not val:
            return False
        try:
            int(val)          # plain decimal
            return True
        except ValueError:
            pass
        try:
            int(val, 16)      # hex string (no prefix)
            return True
        except ValueError:
            return False

    try:
        result = await asyncio.to_thread(
            device.shell,
            "su -c \"sqlite3 /data/data/com.google.android.gsf/databases/gservices.db "
            "'select value from main where name=\\\"android_id\\\";'\"",
        )
        gsf_id = result.output.strip()
        if _is_valid_gsf(gsf_id):
            try:
                hex_repr = hex(int(gsf_id))
            except ValueError:
                hex_repr = "0x" + gsf_id
            logger.info("GSF ID extracted: %s (hex: %s)", gsf_id, hex_repr)
            return gsf_id
    except Exception as exc:
        logger.warning("Failed to extract GSF ID: %s", exc)

    # Fallback: try content provider
    try:
        result = await asyncio.to_thread(
            device.shell,
            "content query --uri content://com.google.android.gsf.gservices "
            '--where "name=\'android_id\'"',
        )
        output = result.output.strip()
        if "value=" in output:
            gsf_id = output.split("value=")[-1].strip()
            if _is_valid_gsf(gsf_id):
                logger.info("GSF ID (via content provider): %s", gsf_id)
                return gsf_id
    except Exception:
        pass

    return ""


async def clear_gms_cache(device: u2.Device) -> None:
    """Clear Google Play Services and Play Store caches.

    This forces a fresh device registration after property changes.
    """
    logger.info("Clearing GMS and Play Store caches...")
    try:
        # Force-stop Play Services
        await asyncio.to_thread(device.shell, "am force-stop com.google.android.gms")
        await asyncio.sleep(1)

        # Clear Play Store data entirely
        await asyncio.to_thread(device.shell, "pm clear com.android.vending")

        # Clear Play Services cache only (not data — that breaks things)
        await asyncio.to_thread(
            device.shell,
            "su -c 'rm -rf /data/data/com.google.android.gms/cache/*'",
        )
        logger.info("GMS caches cleared successfully")
    except Exception as exc:
        logger.warning("Failed to clear GMS caches: %s", exc)


async def wait_for_play_services_sync(
    device: u2.Device,
    gmail: str,
    timeout: int = 60,
    interval: int = 5,
) -> bool:
    """Wait until Play Services recognizes the logged-in account.

    Polls ``dumpsys account`` and parses the output in Python (no shell
    grep) so this works reliably on minimal Android userland.

    Returns True if account is synced within the timeout.
    """
    logger.info("Waiting for Play Services to sync account: %s", gmail)
    deadline = time.time() + timeout
    attempt = 0
    import re as _re

    while time.time() < deadline:
        attempt += 1
        try:
            result = await asyncio.to_thread(
                device.shell, "dumpsys account"
            )
            output = result.output if hasattr(result, 'output') else str(result)
            # Count Google accounts by searching for the pattern in Python
            count = len(_re.findall(r'type=com\.google', output, _re.IGNORECASE))
            if count > 0:
                logger.info(
                    "Play Services synced (%d Google account(s) found, attempt %d)",
                    count, attempt,
                )
                # Give a bit more time for full sync (offers, etc.)
                await asyncio.sleep(5)
                return True
        except Exception:
            pass

        logger.debug("Play Services sync not ready (attempt %d)", attempt)
        await asyncio.sleep(interval)

    logger.warning(
        "Play Services sync timed out after %ds for %s", timeout, gmail,
    )
    return False


# ── Account Management ───────────────────────────────────────────


async def get_logged_in_accounts(device: u2.Device) -> list[str]:
    """List Google accounts currently signed in on the device.

    Runs ``dumpsys account`` and parses output in Python (no shell grep)
    for portability on minimal Android userland.
    """
    import re as _re
    try:
        result = await asyncio.to_thread(device.shell, "dumpsys account")
        output = result.output if hasattr(result, 'output') else str(result)
        # Match "Account {name=user@gmail.com, type=com.google}"
        accounts = _re.findall(
            r'Account\s*\{name=([^,]+),\s*type=com\.google\}',
            output,
            _re.IGNORECASE,
        )
        return [a.strip() for a in accounts]
    except Exception:
        return []


async def remove_google_account(device: u2.Device, email: str) -> bool:
    """Remove a specific Google account from the device.

    Tries the Settings UI with multiple label fallbacks, then falls back
    to a shell command if the UI method fails.  Verifies removal afterward.
    """
    logger.info("Removing Google account: %s", email)
    d = device

    # ── Attempt 1: Settings UI with multiple navigation labels ────

    # Helper: captures a keyword into a true zero-argument callable so that
    # asyncio.to_thread (and type-checkers) see a () -> T signature, not
    # (k=default) -> T which triggers "missing argument k".
    def _exists(kw: str, timeout: int = 3):
        def _fn() -> bool:
            return bool(d(textContains=kw).exists(timeout=timeout))
        return _fn

    def _click(kw: str):
        def _fn() -> None:
            d(textContains=kw).click()
        return _fn

    def _scroll_to(kw: str):
        def _fn() -> bool:
            return bool(d(scrollable=True).scroll.to(textContains=kw))
        return _fn

    try:
        await launch_app(device, "com.android.settings")
        await asyncio.sleep(2)

        # Try direct intent first (works across Android versions)
        # NOTE: Android 11 wraps text in BiDi markers, use textContains
        account_keywords = ["Accounts", "accounts", "Passwords"]
        nav_found = False
        try:
            await asyncio.to_thread(d.shell, "am start -a android.settings.SYNC_SETTINGS")
            await asyncio.sleep(3)
            screen = await asyncio.to_thread(d.dump_hierarchy)
            if any(w in screen.lower() for w in ["account", "google", "passwords"]):
                nav_found = True
        except Exception:
            pass

        # Fallback: navigate via Settings UI with textContains
        if not nav_found:
            for kw in account_keywords:
                try:
                    if await asyncio.to_thread(_exists(kw)):
                        await asyncio.to_thread(_click(kw))
                        nav_found = True
                        await asyncio.sleep(1)
                        break
                except Exception:
                    continue

        # Last resort: scroll to find with textContains
        if not nav_found:
            try:
                for kw in account_keywords:
                    scrolled = await asyncio.to_thread(_scroll_to(kw))
                    if scrolled:
                        if await asyncio.to_thread(_exists(kw, timeout=2)):
                            await asyncio.to_thread(_click(kw))
                            nav_found = True
                            await asyncio.sleep(1)
                            break
            except Exception:
                pass

        if nav_found:
            # Find and click the email (emails don't have BiDi markers)
            if await asyncio.to_thread(_exists(email, timeout=5)):
                await asyncio.to_thread(_click(email))
                await asyncio.sleep(1)

                # Click "Remove account"
                remove_keywords = ["Remove account", "Remove", "Delete account"]
                for rm_kw in remove_keywords:
                    if await asyncio.to_thread(_exists(rm_kw)):
                        await asyncio.to_thread(_click(rm_kw))
                        await asyncio.sleep(1)
                        # Confirm removal
                        for rm_kw2 in remove_keywords:
                            if await asyncio.to_thread(_exists(rm_kw2)):
                                await asyncio.to_thread(_click(rm_kw2))
                                break
                        break

                # Verify removal
                await asyncio.sleep(2)
                remaining = await get_logged_in_accounts(device)
                if email.lower() not in [a.lower() for a in remaining]:
                    logger.info("Account %s removed via Settings UI.", email)
                    return True

    except Exception as exc:
        logger.warning("Settings UI removal failed: %s", exc)

    # ── Attempt 2: Shell fallback ─────────────────────────────────
    try:
        logger.info("Trying shell fallback to remove %s", email)
        await asyncio.to_thread(
            device.shell,
            f"am start -a android.settings.ACCOUNT_SYNC_SETTINGS"
            f" --es account_type com.google",
        )
        await asyncio.sleep(2)
        # Some Android versions support direct account removal via content provider
        await asyncio.to_thread(
            device.shell,
            f"pm clear com.google.android.gms",
        )
        await asyncio.sleep(3)

        remaining = await get_logged_in_accounts(device)
        if email.lower() not in [a.lower() for a in remaining]:
            logger.info("Account %s removed via shell fallback.", email)
            return True
    except Exception as exc:
        logger.warning("Shell removal fallback failed: %s", exc)

    logger.warning("Could not remove account %s", email)
    return False
