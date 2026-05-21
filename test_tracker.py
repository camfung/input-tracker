#!/usr/bin/env python3
"""Deterministic synthetic tests for tracker."""
from __future__ import annotations

import json
import sys
import time
from datetime import date
from pathlib import Path

import tracker as t

TEST_DIR = Path("/tmp/input-tracker-test")
TEST_DIR.mkdir(parents=True, exist_ok=True)
t.DATA_DIR = TEST_DIR
t.DATA_FILE = TEST_DIR / "stats.json"
if t.DATA_FILE.exists():
    t.DATA_FILE.unlink()

from pynput import keyboard, mouse

EXPECTED_PIXELS = 2000
TYPED = "abc" * 5
N_LEFT_CLICKS = 4
N_RIGHT_CLICKS = 2
N_SCROLL_TICKS = 6
EVENT_SETTLE = 0.05


def test_legacy_migration() -> bool:
    legacy = {
        "total_pixels": 1234.0,
        "screen_width": 1920,
        "key_counts": {"x": 3},
        "updated_at": 1700000000,
    }
    t.DATA_FILE.write_text(json.dumps(legacy))
    tr = t.Tracker()
    assert "2023-11-14" in tr.days
    assert tr.days["2023-11-14"].total_pixels == 1234.0
    assert tr.days["2023-11-14"].key_counts["x"] == 3
    print("PASS: legacy schema migrated")
    t.DATA_FILE.unlink()
    return True


def test_live() -> bool:
    tracker = t.Tracker()
    ml = mouse.Listener(
        on_move=tracker.on_move,
        on_click=tracker.on_click,
        on_scroll=tracker.on_scroll,
    )
    kl = keyboard.Listener(on_press=tracker.on_press)
    ml.start()
    kl.start()
    time.sleep(0.2)

    m = mouse.Controller()
    k = keyboard.Controller()

    print()
    print("!!! synthesizing 15 keystrokes into focused window !!!")
    print("    focus a scratch window NOW")
    for n in range(5, 0, -1):
        print(f"    starting in {n}...", flush=True)
        time.sleep(1)

    # mouse move: 1000 right + 1000 left
    start = (200, 200)
    m.position = start
    time.sleep(EVENT_SETTLE)
    for i in range(1, 11):
        m.position = (start[0] + i * 100, start[1])
        time.sleep(EVENT_SETTLE)
    for i in range(1, 11):
        m.position = (start[0] + 1000 - i * 100, start[1])
        time.sleep(EVENT_SETTLE)

    # clicks
    for _ in range(N_LEFT_CLICKS):
        m.click(mouse.Button.left)
        time.sleep(EVENT_SETTLE)
    for _ in range(N_RIGHT_CLICKS):
        m.click(mouse.Button.right)
        time.sleep(EVENT_SETTLE)

    # scroll
    m.scroll(0, N_SCROLL_TICKS)
    time.sleep(EVENT_SETTLE)

    # keys
    for ch in TYPED:
        k.press(ch)
        k.release(ch)
        time.sleep(EVENT_SETTLE)

    time.sleep(0.5)
    ml.stop()
    kl.stop()
    tracker.save()

    today = date.today().isoformat()
    b = tracker.days[today]
    print(f"\nday:           {today}")
    print(f"pixels:        {b.total_pixels:.1f} (expected {EXPECTED_PIXELS})")
    print(f"keys:          {dict(b.key_counts)}")
    print(f"clicks:        {dict(b.click_counts)}")
    print(f"scroll:        {dict(b.scroll_ticks)}")
    print(f"active_sec:    {b.active_seconds}")
    print(f"peak_wpm:      {b.peak_wpm}")
    print(f"hours:         {b.hours}")
    print(f"apps:          {b.apps}")

    ok = True
    if abs(b.total_pixels - EXPECTED_PIXELS) > 50:
        print(f"FAIL: pixels off by {b.total_pixels - EXPECTED_PIXELS:.1f}")
        ok = False
    else:
        print("PASS: pixels within tolerance")

    for ch in "abc":
        if b.key_counts.get(ch, 0) < 5:
            print(f"FAIL: expected >=5 of {ch!r}, got {b.key_counts.get(ch, 0)}")
            ok = False
    if ok:
        print("PASS: key histogram matches")

    if b.click_counts.get("left", 0) < N_LEFT_CLICKS:
        print(f"FAIL: left clicks: got {b.click_counts.get('left')}, want {N_LEFT_CLICKS}")
        ok = False
    if b.click_counts.get("right", 0) < N_RIGHT_CLICKS:
        print(f"FAIL: right clicks: got {b.click_counts.get('right')}, want {N_RIGHT_CLICKS}")
        ok = False
    if ok:
        print("PASS: click counts match")

    if b.scroll_ticks.get("vertical", 0) < N_SCROLL_TICKS:
        print(f"FAIL: scroll: got {b.scroll_ticks.get('vertical')}, want {N_SCROLL_TICKS}")
        ok = False
    else:
        print("PASS: scroll ticks match")

    if b.active_seconds < 1:
        print("FAIL: no active seconds recorded")
        ok = False
    else:
        print(f"PASS: active_seconds = {b.active_seconds}")

    if b.hours:
        print(f"PASS: hour bucket populated ({list(b.hours.keys())})")
    else:
        print("FAIL: hours empty")
        ok = False

    if b.apps:
        print(f"PASS: app bucket populated ({list(b.apps.keys())})")
    else:
        print("WARN: apps empty (no active window detected)")

    saved = json.loads(t.DATA_FILE.read_text())
    assert "days" in saved and today in saved["days"]
    assert "screen_width_px" in saved and "screen_width_mm" in saved
    print(f"PASS: saved JSON has days[{today}] + new geometry fields")
    return ok


def main() -> int:
    ok = True
    ok &= test_legacy_migration()
    ok &= test_live()
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
