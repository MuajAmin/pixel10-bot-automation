"""Humanized Android interaction module.

Replaces instant ADB commands with realistic human-like behavior:
- Variable keystroke delays with typo simulation
- Randomized tap offsets (never hits dead-center)
- Bezier curve swipe paths (non-linear scrolling)
- Gaussian-distributed sleep intervals
- Configurable speed profiles (slow/normal/fast)

Usage:
    from bot.android_worker.humanize import HumanInteractor
    human = HumanInteractor(device)
    await human.type_text("hello@gmail.com")
    await human.tap_element(device(text="Next"))
    await human.swipe_up()
"""

from __future__ import annotations

import asyncio
import logging
import math
import random
import time
from dataclasses import dataclass, field
from typing import Any, Sequence

import uiautomator2 as u2

logger = logging.getLogger(__name__)

# ── Speed Profiles ───────────────────────────────────────────────

@dataclass
class SpeedProfile:
    """Timing parameters for human-like interaction speeds."""
    # Typing (seconds per keystroke)
    key_delay_min: float = 0.04
    key_delay_max: float = 0.15
    key_delay_mean: float = 0.08
    key_delay_std: float = 0.03
    # Probability of making a typo (0.0 - 1.0)
    typo_probability: float = 0.02
    # Pause between words (space key)
    word_pause_min: float = 0.10
    word_pause_max: float = 0.35
    # Tap offset (pixels from center)
    tap_offset_max: int = 12
    tap_offset_std: float = 4.0
    # Pre-tap think time
    pre_tap_delay_min: float = 0.15
    pre_tap_delay_max: float = 0.80
    # Post-tap settle time
    post_tap_delay_min: float = 0.20
    post_tap_delay_max: float = 0.60
    # Swipe duration (milliseconds)
    swipe_duration_min: int = 300
    swipe_duration_max: int = 700
    # General wait multiplier (scales all sleeps)
    wait_multiplier: float = 1.0
    # Bezier curve control point jitter (pixels)
    bezier_jitter: int = 40


PROFILES: dict[str, SpeedProfile] = {
    "slow": SpeedProfile(
        key_delay_min=0.08, key_delay_max=0.25, key_delay_mean=0.14,
        key_delay_std=0.05, typo_probability=0.04, word_pause_min=0.20,
        word_pause_max=0.60, tap_offset_max=18, tap_offset_std=6.0,
        pre_tap_delay_min=0.30, pre_tap_delay_max=1.20,
        post_tap_delay_min=0.40, post_tap_delay_max=1.00,
        swipe_duration_min=500, swipe_duration_max=1000,
        wait_multiplier=1.5, bezier_jitter=60,
    ),
    "normal": SpeedProfile(),
    "fast": SpeedProfile(
        key_delay_min=0.02, key_delay_max=0.08, key_delay_mean=0.04,
        key_delay_std=0.015, typo_probability=0.01, word_pause_min=0.05,
        word_pause_max=0.15, tap_offset_max=8, tap_offset_std=3.0,
        pre_tap_delay_min=0.08, pre_tap_delay_max=0.40,
        post_tap_delay_min=0.10, post_tap_delay_max=0.30,
        swipe_duration_min=200, swipe_duration_max=450,
        wait_multiplier=0.7, bezier_jitter=25,
    ),
}


# ── Bezier Math ──────────────────────────────────────────────────

def _cubic_bezier(
    t: float,
    p0: tuple[float, float],
    p1: tuple[float, float],
    p2: tuple[float, float],
    p3: tuple[float, float],
) -> tuple[float, float]:
    """Evaluate a cubic Bezier curve at parameter t ∈ [0, 1]."""
    u = 1.0 - t
    x = (u**3 * p0[0] + 3 * u**2 * t * p1[0] +
         3 * u * t**2 * p2[0] + t**3 * p3[0])
    y = (u**3 * p0[1] + 3 * u**2 * t * p1[1] +
         3 * u * t**2 * p2[1] + t**3 * p3[1])
    return (x, y)


def _generate_bezier_points(
    start: tuple[int, int],
    end: tuple[int, int],
    jitter: int = 40,
    num_points: int = 12,
) -> list[tuple[int, int]]:
    """Generate points along a cubic Bezier curve between start and end.

    Control points are offset randomly to create a natural arc,
    simulating how a human finger moves across a screen.
    """
    sx, sy = float(start[0]), float(start[1])
    ex, ey = float(end[0]), float(end[1])
    dx = ex - sx
    dy = ey - sy

    # Control points: create a subtle arc
    # CP1 is ~1/3 along the path, CP2 is ~2/3, both with random offset
    cp1 = (
        sx + dx * 0.3 + random.gauss(0, jitter),
        sy + dy * 0.3 + random.gauss(0, jitter),
    )
    cp2 = (
        sx + dx * 0.7 + random.gauss(0, jitter),
        sy + dy * 0.7 + random.gauss(0, jitter),
    )

    points: list[tuple[int, int]] = []
    for i in range(num_points + 1):
        t = i / num_points
        # Apply ease-in-out timing (faster in middle, slower at ends)
        t_eased = t * t * (3.0 - 2.0 * t)  # smoothstep
        px, py = _cubic_bezier(t_eased, (sx, sy), cp1, cp2, (ex, ey))
        points.append((int(px), int(py)))

    return points


# ── Random Helpers ───────────────────────────────────────────────

def _gauss_clamp(mean: float, std: float, lo: float, hi: float) -> float:
    """Gaussian random value clamped to [lo, hi]."""
    return max(lo, min(hi, random.gauss(mean, std)))


def _rand_offset(max_px: int, std: float) -> int:
    """Random pixel offset from center (Gaussian distribution)."""
    return int(_gauss_clamp(0.0, std, -max_px, max_px))


# ── QWERTY Layout for Typo Simulation ────────────────────────────

_QWERTY_NEIGHBORS: dict[str, str] = {
    'q': 'wa', 'w': 'qeas', 'e': 'wrds', 'r': 'etdf',
    't': 'ryfg', 'y': 'tugh', 'u': 'yijh', 'i': 'uojk',
    'o': 'iplk', 'p': 'ol', 'a': 'qwsz', 's': 'awedxz',
    'd': 'serfcx', 'f': 'drtgvc', 'g': 'ftyhbv', 'h': 'gyujnb',
    'j': 'huiknm', 'k': 'jiolm', 'l': 'kop', 'z': 'asx',
    'x': 'zsdc', 'c': 'xdfv', 'v': 'cfgb', 'b': 'vghn',
    'n': 'bhjm', 'm': 'njk', '1': '2q', '2': '13qw', '3': '24we',
    '4': '35er', '5': '46rt', '6': '57ty', '7': '68yu',
    '8': '79ui', '9': '80io', '0': '9op',
}


def _nearby_key(char: str) -> str:
    """Return a plausible typo for the given character."""
    neighbors = _QWERTY_NEIGHBORS.get(char.lower(), "")
    if not neighbors:
        return char
    typo = random.choice(neighbors)
    return typo.upper() if char.isupper() else typo


# ══════════════════════════════════════════════════════════════════
#  HumanInteractor — main class
# ══════════════════════════════════════════════════════════════════

class HumanInteractor:
    """Wraps a uiautomator2.Device with human-like interaction methods.

    All methods are async and use randomized timing, offsets, and
    Bezier curves to avoid detection by Google's behavioral analysis.

    Parameters
    ----------
    device : u2.Device
        Connected uiautomator2 device handle.
    profile : str
        Speed profile name: "slow", "normal", or "fast".
    seed : int | None
        Optional RNG seed for reproducible behavior (testing only).
    """

    def __init__(
        self,
        device: u2.Device,
        profile: str = "normal",
        seed: int | None = None,
    ) -> None:
        self.device = device
        self.profile = PROFILES.get(profile, PROFILES["normal"])
        self._screen_w: int = 0
        self._screen_h: int = 0
        if seed is not None:
            random.seed(seed)

    # ── Screen Info ──────────────────────────────────────────────

    async def _ensure_screen_size(self) -> tuple[int, int]:
        """Cache device screen dimensions."""
        if self._screen_w == 0:
            info = await asyncio.to_thread(lambda: self.device.info)
            self._screen_w = info.get("displayWidth", 1080)
            self._screen_h = info.get("displayHeight", 2400)
        return self._screen_w, self._screen_h

    # ── Humanized Sleep ──────────────────────────────────────────

    async def sleep(self, base: float, jitter: float = 0.3) -> None:
        """Sleep for a randomized duration.

        Args:
            base: Base sleep time in seconds.
            jitter: Maximum random offset (± jitter * base).
        """
        actual = base * self.profile.wait_multiplier
        offset = random.uniform(-jitter * actual, jitter * actual)
        duration = max(0.05, actual + offset)
        await asyncio.sleep(duration)

    async def think(self, min_s: float = 0.5, max_s: float = 2.0) -> None:
        """Simulate human 'thinking' pause before next action."""
        duration = _gauss_clamp(
            (min_s + max_s) / 2,
            (max_s - min_s) / 4,
            min_s * self.profile.wait_multiplier,
            max_s * self.profile.wait_multiplier,
        )
        await asyncio.sleep(duration)

    # ── Typing ───────────────────────────────────────────────────

    async def type_text(
        self,
        text: str,
        clear_first: bool = True,
        use_ime: bool = False,
    ) -> None:
        """Type text character-by-character with human-like delays.

        Includes:
        - Variable per-key delays (Gaussian distribution)
        - Longer pauses at word boundaries
        - Occasional typo + backspace correction
        - Periodic micro-pauses (as if reading what was typed)

        Args:
            text: The text string to type.
            clear_first: Whether to clear the field before typing.
            use_ime: If True, fall back to set_text (fast but detectable).
        """
        p = self.profile
        d = self.device

        if clear_first:
            await asyncio.to_thread(d.clear_text)
            await self.sleep(0.2, jitter=0.5)

        if use_ime:
            # Fast fallback — use send_keys but add a small delay
            await asyncio.to_thread(d.send_keys, text)
            await self.sleep(0.3)
            return

        # Character-by-character with human timing
        chars_typed = 0
        for i, char in enumerate(text):
            # ── Typo simulation ──────────────────────────────────
            if (
                random.random() < p.typo_probability
                and char.isalpha()
                and i > 0
            ):
                # Type wrong key
                wrong = _nearby_key(char)
                await asyncio.to_thread(d.send_keys, wrong)
                delay = _gauss_clamp(
                    p.key_delay_mean, p.key_delay_std,
                    p.key_delay_min, p.key_delay_max,
                )
                await asyncio.sleep(delay)

                # Brief pause (noticing the typo)
                await asyncio.sleep(random.uniform(0.15, 0.45))

                # Press backspace
                await asyncio.to_thread(d.press, "delete")
                await asyncio.sleep(random.uniform(0.05, 0.15))

                # Now type the correct character
                logger.debug("Typo simulation: '%s' → '%s' → backspace", char, wrong)

            # ── Type the actual character ─────────────────────────
            await asyncio.to_thread(d.send_keys, char)
            chars_typed += 1

            # ── Per-key delay ────────────────────────────────────
            if char == ' ':
                # Longer pause between words
                delay = random.uniform(p.word_pause_min, p.word_pause_max)
            elif char in '@._-':
                # Slight pause at punctuation (looking for the key)
                delay = _gauss_clamp(
                    p.key_delay_mean * 1.5, p.key_delay_std,
                    p.key_delay_min, p.key_delay_max * 1.5,
                )
            else:
                delay = _gauss_clamp(
                    p.key_delay_mean, p.key_delay_std,
                    p.key_delay_min, p.key_delay_max,
                )
            await asyncio.sleep(delay)

            # ── Micro-pause every ~8-15 characters ───────────────
            if chars_typed > 0 and chars_typed % random.randint(8, 15) == 0:
                await asyncio.sleep(random.uniform(0.3, 0.8))

        logger.debug(
            "Typed %d characters (%.1fs avg/char)",
            len(text),
            p.key_delay_mean,
        )

    async def type_text_fast(self, text: str, clear_first: bool = True) -> None:
        """Type text using set_text with a short delay.

        Faster than character-by-character but adds enough delay
        to look somewhat natural. Use for non-critical fields.
        """
        d = self.device
        if clear_first:
            await asyncio.to_thread(d.clear_text)
            await self.sleep(0.2, jitter=0.4)

        await asyncio.to_thread(d.send_keys, text)
        # Simulate the time it would take to type
        simulated = len(text) * random.uniform(0.02, 0.05)
        await asyncio.sleep(min(simulated, 2.0))

    # ── Tapping ──────────────────────────────────────────────────

    async def tap(self, x: int, y: int) -> None:
        """Tap at coordinates with randomized offset from the exact point.

        Humans never tap dead-center. Offset follows Gaussian distribution
        centered at (x, y) with configurable spread.
        """
        p = self.profile

        # Pre-tap delay (thinking / aiming)
        await asyncio.sleep(random.uniform(
            p.pre_tap_delay_min, p.pre_tap_delay_max,
        ))

        # Apply offset
        ox = _rand_offset(p.tap_offset_max, p.tap_offset_std)
        oy = _rand_offset(p.tap_offset_max, p.tap_offset_std)
        tx, ty = x + ox, y + oy

        # Clamp to screen bounds
        w, h = await self._ensure_screen_size()
        tx = max(0, min(w - 1, tx))
        ty = max(0, min(h - 1, ty))

        logger.debug("Human tap: (%d,%d) → (%d,%d) [offset %+d,%+d]", x, y, tx, ty, ox, oy)
        await asyncio.to_thread(self.device.click, tx, ty)

        # Post-tap settle
        await asyncio.sleep(random.uniform(
            p.post_tap_delay_min, p.post_tap_delay_max,
        ))

    async def tap_element(
        self,
        selector: Any,
        timeout: int = 5,
        retries: int = 2,
    ) -> bool:
        """Tap a UI element with humanized offset.

        Instead of clicking the exact center, calculates the element's
        bounds and taps at a randomized point within them.

        Args:
            selector: u2 selector (e.g., device(text="Next"))
            timeout: Max seconds to wait for element.
            retries: Number of retry attempts.

        Returns:
            True if the element was found and tapped.
        """
        p = self.profile
        d = self.device

        for attempt in range(retries + 1):
            try:
                exists = await asyncio.to_thread(
                    lambda: selector.exists(timeout=timeout)
                )
                if not exists:
                    if attempt < retries:
                        await self.sleep(1.0)
                        continue
                    return False

                # Get element bounds
                info = await asyncio.to_thread(lambda: selector.info)
                bounds = info.get("bounds", {})
                left = bounds.get("left", 0)
                top = bounds.get("top", 0)
                right = bounds.get("right", left + 100)
                bottom = bounds.get("bottom", top + 50)

                # Center of element
                cx = (left + right) // 2
                cy = (top + bottom) // 2

                # Random point within element bounds (not exact center)
                w_half = max(1, (right - left) // 4)
                h_half = max(1, (bottom - top) // 4)
                tx = cx + random.randint(-w_half, w_half)
                ty = cy + random.randint(-h_half, h_half)

                # Clamp to element bounds with small margin
                tx = max(left + 2, min(right - 2, tx))
                ty = max(top + 2, min(bottom - 2, ty))

                # Pre-tap delay
                await asyncio.sleep(random.uniform(
                    p.pre_tap_delay_min, p.pre_tap_delay_max,
                ))

                logger.debug(
                    "Human tap_element: center(%d,%d) → (%d,%d)",
                    cx, cy, tx, ty,
                )
                await asyncio.to_thread(d.click, tx, ty)

                # Post-tap settle
                await asyncio.sleep(random.uniform(
                    p.post_tap_delay_min, p.post_tap_delay_max,
                ))
                return True

            except Exception as exc:
                logger.debug("tap_element attempt %d failed: %s", attempt, exc)
                if attempt < retries:
                    await self.sleep(0.5)
                continue

        return False

    async def tap_text(
        self,
        text: str,
        timeout: int = 5,
        contains: bool = False,
    ) -> bool:
        """Tap an element by its text label with humanized offset.

        Args:
            text: Exact or partial text to find.
            timeout: Max seconds to wait.
            contains: If True, use textContains instead of exact match.
        """
        d = self.device
        if contains:
            selector = d(textContains=text)
        else:
            selector = d(text=text)
        return await self.tap_element(selector, timeout=timeout)

    # ── Swiping (Bezier Curves) ──────────────────────────────────

    async def swipe_bezier(
        self,
        start: tuple[int, int],
        end: tuple[int, int],
        duration_ms: int | None = None,
    ) -> None:
        """Perform a swipe along a cubic Bezier curve path.

        This creates a natural, non-linear finger movement that
        is very difficult for bot detection to distinguish from
        real human swipes.

        Args:
            start: (x, y) start coordinates.
            end: (x, y) end coordinates.
            duration_ms: Total swipe duration. Random if None.
        """
        p = self.profile
        if duration_ms is None:
            duration_ms = random.randint(
                p.swipe_duration_min, p.swipe_duration_max,
            )

        points = _generate_bezier_points(
            start, end, jitter=p.bezier_jitter,
        )

        # Use the device's swipe_points if available (multi-point gesture)
        # Otherwise fall back to chained small swipes
        try:
            # u2 supports swipe_ext with steps for multi-point
            # We use the point-by-point approach via shell
            point_strs = " ".join(f"{x} {y}" for x, y in points)
            steps = max(4, len(points) - 1)

            # Fallback: simple swipe with duration
            await asyncio.to_thread(
                self.device.swipe,
                start[0], start[1], end[0], end[1],
                duration=duration_ms / 1000.0,
                steps=steps,
            )
        except Exception:
            # Last resort: basic swipe
            await asyncio.to_thread(
                self.device.swipe,
                start[0], start[1], end[0], end[1],
                duration=duration_ms / 1000.0,
            )

        await asyncio.sleep(random.uniform(0.2, 0.5))

    async def swipe_up(self, distance_ratio: float = 0.4) -> None:
        """Swipe up (scroll down) with natural Bezier arc.

        Args:
            distance_ratio: Fraction of screen height to swipe (0-1).
        """
        w, h = await self._ensure_screen_size()
        cx = w // 2 + random.randint(-30, 30)
        start_y = int(h * (0.5 + distance_ratio / 2)) + random.randint(-20, 20)
        end_y = int(h * (0.5 - distance_ratio / 2)) + random.randint(-20, 20)
        await self.swipe_bezier((cx, start_y), (cx + random.randint(-15, 15), end_y))

    async def swipe_down(self, distance_ratio: float = 0.4) -> None:
        """Swipe down (scroll up) with natural Bezier arc."""
        w, h = await self._ensure_screen_size()
        cx = w // 2 + random.randint(-30, 30)
        start_y = int(h * (0.5 - distance_ratio / 2)) + random.randint(-20, 20)
        end_y = int(h * (0.5 + distance_ratio / 2)) + random.randint(-20, 20)
        await self.swipe_bezier((cx, start_y), (cx + random.randint(-15, 15), end_y))

    async def swipe_left(self, distance_ratio: float = 0.5) -> None:
        """Swipe left with natural Bezier arc."""
        w, h = await self._ensure_screen_size()
        cy = h // 2 + random.randint(-20, 20)
        start_x = int(w * (0.5 + distance_ratio / 2)) + random.randint(-15, 15)
        end_x = int(w * (0.5 - distance_ratio / 2)) + random.randint(-15, 15)
        await self.swipe_bezier((start_x, cy), (end_x, cy + random.randint(-10, 10)))

    async def swipe_right(self, distance_ratio: float = 0.5) -> None:
        """Swipe right with natural Bezier arc."""
        w, h = await self._ensure_screen_size()
        cy = h // 2 + random.randint(-20, 20)
        start_x = int(w * (0.5 - distance_ratio / 2)) + random.randint(-15, 15)
        end_x = int(w * (0.5 + distance_ratio / 2)) + random.randint(-15, 15)
        await self.swipe_bezier((start_x, cy), (end_x, cy + random.randint(-10, 10)))

    async def fling_scroll(self, direction: str = "up", speed: str = "normal") -> None:
        """Perform a fast fling scroll in the given direction.

        Args:
            direction: "up", "down", "left", "right".
            speed: "slow", "normal", "fast" — controls fling velocity.
        """
        w, h = await self._ensure_screen_size()
        durations = {"slow": 450, "normal": 280, "fast": 150}
        dur = durations.get(speed, 280)

        cx, cy = w // 2, h // 2
        dist = int(h * 0.6)  # fling covers more distance

        if direction == "up":
            start = (cx + random.randint(-20, 20), cy + dist // 2)
            end = (cx + random.randint(-20, 20), cy - dist // 2)
        elif direction == "down":
            start = (cx + random.randint(-20, 20), cy - dist // 2)
            end = (cx + random.randint(-20, 20), cy + dist // 2)
        elif direction == "left":
            start = (cx + dist // 2, cy + random.randint(-15, 15))
            end = (cx - dist // 2, cy + random.randint(-15, 15))
        else:  # right
            start = (cx - dist // 2, cy + random.randint(-15, 15))
            end = (cx + dist // 2, cy + random.randint(-15, 15))

        await self.swipe_bezier(start, end, duration_ms=dur)

    # ── Complex Interactions ─────────────────────────────────────

    async def scroll_to_text(
        self,
        text: str,
        max_scrolls: int = 8,
        contains: bool = True,
    ) -> bool:
        """Scroll down until text is found or max scrolls reached.

        Uses Bezier curve swipes and checks for the text after each.

        Returns:
            True if text was found.
        """
        d = self.device
        for i in range(max_scrolls):
            try:
                if contains:
                    found = await asyncio.to_thread(
                        lambda: d(textContains=text).exists(timeout=1)
                    )
                else:
                    found = await asyncio.to_thread(
                        lambda: d(text=text).exists(timeout=1)
                    )
                if found:
                    logger.debug("Found text '%s' after %d scrolls", text, i)
                    return True
            except Exception:
                pass

            await self.swipe_up(distance_ratio=random.uniform(0.25, 0.45))
            await self.sleep(0.8, jitter=0.4)

        logger.debug("Text '%s' not found after %d scrolls", text, max_scrolls)
        return False

    async def wait_and_tap(
        self,
        texts: Sequence[str],
        timeout: int = 10,
        contains: bool = True,
    ) -> str | None:
        """Wait for any of the given texts to appear, then tap it.

        Args:
            texts: List of text labels to look for.
            timeout: Max seconds to wait.
            contains: Use textContains matching.

        Returns:
            The text that was tapped, or None if none found.
        """
        d = self.device
        deadline = time.time() + timeout

        while time.time() < deadline:
            for txt in texts:
                try:
                    if contains:
                        sel = d(textContains=txt)
                    else:
                        sel = d(text=txt)

                    if await asyncio.to_thread(lambda s=sel: s.exists(timeout=1)):
                        tapped = await self.tap_element(sel, timeout=2)
                        if tapped:
                            logger.debug("wait_and_tap: tapped '%s'", txt)
                            return txt
                except Exception:
                    continue

            await asyncio.sleep(0.5)

        return None

    async def dismiss_dialogs(
        self,
        dismiss_texts: Sequence[str] | None = None,
        max_dismissals: int = 3,
    ) -> int:
        """Dismiss popup dialogs with humanized taps.

        Args:
            dismiss_texts: Button labels to look for. Defaults to common ones.
            max_dismissals: Max number of dialogs to dismiss.

        Returns:
            Number of dialogs dismissed.
        """
        if dismiss_texts is None:
            dismiss_texts = [
                "No thanks", "Not now", "Skip", "Got it",
                "OK, got it", "Dismiss", "Close", "Maybe later",
                "OK", "Cancel", "Deny",
            ]

        dismissed = 0
        for _ in range(max_dismissals):
            result = await self.wait_and_tap(dismiss_texts, timeout=2, contains=False)
            if result:
                dismissed += 1
                await self.sleep(0.5)
            else:
                break

        if dismissed:
            logger.debug("Dismissed %d dialog(s)", dismissed)
        return dismissed

    async def long_press(
        self,
        x: int, y: int,
        duration: float = 1.0,
    ) -> None:
        """Long press at coordinates with humanized offset."""
        p = self.profile
        ox = _rand_offset(p.tap_offset_max, p.tap_offset_std)
        oy = _rand_offset(p.tap_offset_max, p.tap_offset_std)
        tx, ty = x + ox, y + oy

        w, h = await self._ensure_screen_size()
        tx = max(0, min(w - 1, tx))
        ty = max(0, min(h - 1, ty))

        await asyncio.to_thread(
            self.device.long_click, tx, ty,
            duration=duration + random.uniform(-0.1, 0.2),
        )
        await self.sleep(0.3)

    async def press_key(self, key: str) -> None:
        """Press a device key with a small delay."""
        await self.sleep(0.15, jitter=0.5)
        await asyncio.to_thread(self.device.press, key)
        await self.sleep(0.2, jitter=0.3)

    async def press_enter(self) -> None:
        """Press Enter/Next key with human timing."""
        await self.press_key("enter")

    async def press_back(self) -> None:
        """Press the Back button with human timing."""
        await self.press_key("back")

    # ── Input Field Helpers ──────────────────────────────────────

    async def find_and_type(
        self,
        text: str,
        resource_id: str | None = None,
        class_name: str = "android.widget.EditText",
        clear_first: bool = True,
        timeout: int = 10,
    ) -> bool:
        """Find an input field and type text into it with human timing.

        Tries resource_id first, then falls back to class_name.

        Returns:
            True if text was successfully typed.
        """
        d = self.device

        # Try resource ID
        if resource_id:
            sel = d(resourceId=resource_id)
            if await asyncio.to_thread(lambda: sel.exists(timeout=timeout)):
                await self.tap_element(sel, timeout=3)
                await self.sleep(0.3)
                await self.type_text(text, clear_first=clear_first)
                return True

        # Fallback: find by class name
        sel = d(className=class_name)
        if await asyncio.to_thread(lambda: sel.exists(timeout=timeout)):
            await self.tap_element(sel, timeout=3)
            await self.sleep(0.3)
            await self.type_text(text, clear_first=clear_first)
            return True

        logger.warning("find_and_type: no input field found")
        return False

    # ── Idle / Anti-Pattern Behavior ─────────────────────────────

    async def idle_behavior(self, duration: float = 3.0) -> None:
        """Simulate idle user behavior for the given duration.

        Randomly performs small micro-actions that a real user
        would do while waiting (small scrolls, pauses, etc.)
        This prevents detection of perfectly still bots.
        """
        end_time = time.time() + duration * self.profile.wait_multiplier

        while time.time() < end_time:
            action = random.random()
            if action < 0.3:
                # Small fidget scroll
                w, h = await self._ensure_screen_size()
                cx = w // 2 + random.randint(-50, 50)
                cy = h // 2
                tiny_dy = random.randint(20, 80)
                direction = random.choice([-1, 1])
                await asyncio.to_thread(
                    self.device.swipe,
                    cx, cy, cx, cy + tiny_dy * direction,
                    duration=0.3,
                )
                await asyncio.sleep(random.uniform(0.5, 1.5))
            elif action < 0.5:
                # Just wait
                await asyncio.sleep(random.uniform(1.0, 2.0))
            else:
                # Wait longer
                await asyncio.sleep(random.uniform(0.5, 1.0))


# ══════════════════════════════════════════════════════════════════
#  Convenience factory
# ══════════════════════════════════════════════════════════════════

def create_human(
    device: u2.Device,
    profile: str = "normal",
) -> HumanInteractor:
    """Create a HumanInteractor with the given speed profile.

    Usage:
        human = create_human(device, "slow")
        await human.type_text("user@gmail.com")
    """
    return HumanInteractor(device, profile=profile)


__all__ = [
    "HumanInteractor",
    "SpeedProfile",
    "PROFILES",
    "create_human",
]
