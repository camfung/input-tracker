#!/usr/bin/env python3
"""Track mouse distance, clicks, scroll, keys, active time per local day."""
from __future__ import annotations

import argparse
import csv
import json
import math
import signal
import sys
import threading
import time
from collections import Counter, deque
from datetime import date, datetime
from pathlib import Path

from pynput import keyboard, mouse
from Xlib import X, display
from Xlib.ext import randr

DATA_DIR = Path.home() / ".input-tracker"
DATA_FILE = DATA_DIR / "stats.json"
SAVE_INTERVAL = 30
WPM_WINDOW_SEC = 60.0
APP_POLL_INTERVAL = 1.0


def today_key() -> str:
    return date.today().isoformat()


def get_primary_geometry() -> tuple[int, int]:
    """Return (width_px, width_mm) of the primary output."""
    d = display.Display()
    root = d.screen().root
    try:
        primary = root.xrandr_get_output_primary().output
        if primary:
            res = randr.get_screen_resources(root)
            info = randr.get_output_info(d, primary, res.config_timestamp)
            if info.crtc:
                crtc = randr.get_crtc_info(d, info.crtc, res.config_timestamp)
                return (crtc.width, info.mm_width or 0)
    except Exception:
        pass
    s = d.screen()
    return (s.width_in_pixels, s.width_in_mms or 0)


class DayBucket:
    __slots__ = (
        "total_pixels",
        "key_counts",
        "click_counts",
        "scroll_ticks",
        "active_seconds",
        "peak_wpm",
        "hours",
        "apps",
    )

    def __init__(self) -> None:
        self.total_pixels: float = 0.0
        self.key_counts: Counter[str] = Counter()
        self.click_counts: Counter[str] = Counter()
        self.scroll_ticks: Counter[str] = Counter()
        self.active_seconds: int = 0
        self.peak_wpm: float = 0.0
        self.hours: dict[str, dict[str, float]] = {}
        self.apps: dict[str, dict[str, float]] = {}

    def hour_bucket(self, hour: int) -> dict[str, float]:
        h = str(hour)
        b = self.hours.get(h)
        if b is None:
            b = {"pixels": 0.0, "keys": 0, "clicks": 0}
            self.hours[h] = b
        return b

    def app_bucket(self, app: str) -> dict[str, float]:
        b = self.apps.get(app)
        if b is None:
            b = {"pixels": 0.0, "keys": 0, "clicks": 0}
            self.apps[app] = b
        return b

    def to_dict(self) -> dict:
        return {
            "total_pixels": self.total_pixels,
            "key_counts": dict(self.key_counts),
            "click_counts": dict(self.click_counts),
            "scroll_ticks": dict(self.scroll_ticks),
            "active_seconds": self.active_seconds,
            "peak_wpm": self.peak_wpm,
            "hours": self.hours,
            "apps": self.apps,
        }

    @classmethod
    def from_dict(cls, data: dict) -> DayBucket:
        b = cls()
        b.total_pixels = float(data.get("total_pixels", 0.0))
        b.key_counts = Counter(data.get("key_counts", {}))
        b.click_counts = Counter(data.get("click_counts", {}))
        b.scroll_ticks = Counter(data.get("scroll_ticks", {}))
        b.active_seconds = int(data.get("active_seconds", 0))
        b.peak_wpm = float(data.get("peak_wpm", 0.0))
        b.hours = data.get("hours", {}) or {}
        b.apps = data.get("apps", {}) or {}
        return b


class AppPoller:
    """Cache active window's WM_CLASS via a polling thread."""

    def __init__(self) -> None:
        self.current: str = "unknown"
        try:
            self._d = display.Display()
            self._root = self._d.screen().root
            self._net_active = self._d.intern_atom("_NET_ACTIVE_WINDOW")
            self._ok = True
        except Exception:
            self._ok = False
        if self._ok:
            threading.Thread(target=self._loop, daemon=True).start()

    def _read(self) -> str:
        try:
            prop = self._root.get_full_property(self._net_active, X.AnyPropertyType)
            if not prop or not prop.value:
                return "unknown"
            wid = prop.value[0]
            if not wid:
                return "unknown"
            win = self._d.create_resource_object("window", wid)
            cls = win.get_wm_class()
            if cls and cls[1]:
                return cls[1].lower()
            if cls and cls[0]:
                return cls[0].lower()
            name = win.get_wm_name()
            return name.lower() if name else "unknown"
        except Exception:
            return "unknown"

    def _loop(self) -> None:
        while True:
            try:
                self.current = self._read()
            except Exception:
                pass
            time.sleep(APP_POLL_INTERVAL)


class Tracker:
    def __init__(self) -> None:
        self.screen_width, self.screen_width_mm = get_primary_geometry()
        self.lock = threading.Lock()
        self.last_pos: tuple[int, int] | None = None
        self.days: dict[str, DayBucket] = {}
        self.active_seconds: set[int] = set()
        self.active_seconds_day: str = today_key()
        self.key_times: deque[float] = deque()
        self.app_poller = AppPoller()
        self._load()

    def _bucket(self) -> DayBucket:
        key = today_key()
        if key != self.active_seconds_day:
            self.active_seconds = set()
            self.active_seconds_day = key
        b = self.days.get(key)
        if b is None:
            b = DayBucket()
            self.days[key] = b
        return b

    def _load(self) -> None:
        if not DATA_FILE.exists():
            return
        data = json.loads(DATA_FILE.read_text())
        if "days" in data:
            self.days = {k: DayBucket.from_dict(v) for k, v in data["days"].items()}
            return
        ts = data.get("updated_at")
        legacy_day = (
            datetime.fromtimestamp(ts).date().isoformat() if ts else today_key()
        )
        b = DayBucket()
        b.total_pixels = float(data.get("total_pixels", 0.0))
        b.key_counts = Counter(data.get("key_counts", {}))
        self.days = {legacy_day: b}

    def save(self) -> None:
        with self.lock:
            DATA_DIR.mkdir(parents=True, exist_ok=True)
            cur = self.days.get(self.active_seconds_day)
            if cur is not None:
                cur.active_seconds = max(cur.active_seconds, len(self.active_seconds))
            payload = {
                "screen_width_px": self.screen_width,
                "screen_width_mm": self.screen_width_mm,
                "days": {k: v.to_dict() for k, v in self.days.items()},
                "updated_at": time.time(),
            }
            tmp = DATA_FILE.with_suffix(".tmp")
            tmp.write_text(json.dumps(payload, indent=2, sort_keys=True))
            tmp.replace(DATA_FILE)

    def _mark_active(self, now: float) -> None:
        self.active_seconds.add(int(now))

    def on_move(self, x: int, y: int) -> None:
        now = time.time()
        with self.lock:
            b = self._bucket()
            if self.last_pos is not None:
                dx = x - self.last_pos[0]
                dy = y - self.last_pos[1]
                dist = math.hypot(dx, dy)
                b.total_pixels += dist
                hour = datetime.fromtimestamp(now).hour
                b.hour_bucket(hour)["pixels"] = (
                    b.hour_bucket(hour)["pixels"] + dist
                )
                app = self.app_poller.current
                b.app_bucket(app)["pixels"] = b.app_bucket(app)["pixels"] + dist
            self.last_pos = (x, y)
            self._mark_active(now)

    def on_press(self, key: object) -> None:
        now = time.time()
        name = _key_name(key)
        is_char = len(name) == 1
        with self.lock:
            b = self._bucket()
            b.key_counts[name] += 1
            hour = datetime.fromtimestamp(now).hour
            hb = b.hour_bucket(hour)
            hb["keys"] = int(hb["keys"]) + 1
            ab = b.app_bucket(self.app_poller.current)
            ab["keys"] = int(ab["keys"]) + 1
            self._mark_active(now)
            if is_char:
                self.key_times.append(now)
                cutoff = now - WPM_WINDOW_SEC
                while self.key_times and self.key_times[0] < cutoff:
                    self.key_times.popleft()
                wpm = len(self.key_times) / 5.0
                if wpm > b.peak_wpm:
                    b.peak_wpm = wpm

    def on_click(self, x: int, y: int, button: object, pressed: bool) -> None:
        if not pressed:
            return
        now = time.time()
        name = getattr(button, "name", str(button))
        with self.lock:
            b = self._bucket()
            b.click_counts[name] += 1
            hour = datetime.fromtimestamp(now).hour
            hb = b.hour_bucket(hour)
            hb["clicks"] = int(hb["clicks"]) + 1
            ab = b.app_bucket(self.app_poller.current)
            ab["clicks"] = int(ab["clicks"]) + 1
            self._mark_active(now)

    def on_scroll(self, x: int, y: int, dx: int, dy: int) -> None:
        now = time.time()
        with self.lock:
            b = self._bucket()
            b.scroll_ticks["vertical"] += abs(int(dy))
            b.scroll_ticks["horizontal"] += abs(int(dx))
            self._mark_active(now)


def _key_name(key: object) -> str:
    if isinstance(key, keyboard.Key):
        return f"<{key.name}>"
    char = getattr(key, "char", None)
    if char is not None:
        return char
    return str(key)


# ---------- CLI ----------


def _load_data() -> dict | None:
    if not DATA_FILE.exists():
        return None
    return json.loads(DATA_FILE.read_text())


def _screen_width(data: dict) -> int:
    return int(data.get("screen_width_px") or data.get("screen_width") or 1)


def _mm_per_px(data: dict) -> float:
    sw = _screen_width(data)
    return float(data.get("screen_width_mm", 0) or 0) / sw if sw else 0.0


def _print_day(day: str, bucket: dict, screen_width: int, mm_per_px: float, top: int) -> None:
    px = bucket.get("total_pixels", 0.0)
    keys = bucket.get("key_counts", {})
    clicks = bucket.get("click_counts", {})
    scrolls = bucket.get("scroll_ticks", {})
    total_keys = sum(keys.values())
    total_clicks = sum(clicks.values())
    active = bucket.get("active_seconds", 0)
    wpm = bucket.get("peak_wpm", 0.0)
    meters = px * mm_per_px / 1000.0
    print(f"=== {day} ===")
    print(f"  pixels:        {px:.0f}")
    print(f"  screen widths: {px / screen_width:.2f}")
    print(f"  meters:        {meters:.2f}")
    print(f"  total keys:    {total_keys}")
    print(f"  total clicks:  {total_clicks}  {dict(clicks)}")
    print(f"  scroll ticks:  v={scrolls.get('vertical', 0)} h={scrolls.get('horizontal', 0)}")
    print(f"  active time:   {active}s ({active / 60:.1f} min)")
    print(f"  peak wpm:      {wpm:.0f}")
    if keys:
        print(f"  top {top} keys:")
        items = sorted(keys.items(), key=lambda kv: -kv[1])[:top]
        name_w = max(len(k) for k, _ in items)
        peak = items[0][1]
        for k, n in items:
            bar = "#" * max(1, int(40 * n / peak))
            print(f"    {k:<{name_w}} {n:>8d}  {bar}")


def cmd_show(args: argparse.Namespace) -> int:
    data = _load_data()
    if data is None:
        print("no stats yet")
        return 1
    sw = _screen_width(data)
    mm_per_px = _mm_per_px(data)
    days = data.get("days", {})
    if not days:
        print("no day buckets")
        return 1
    sorted_days = sorted(days.keys())
    if args.day:
        if args.day not in days:
            print(f"no data for {args.day}")
            return 1
        _print_day(args.day, days[args.day], sw, mm_per_px, args.top)
        return 0
    if args.all:
        chosen = sorted_days
    elif args.last:
        chosen = sorted_days[-args.last:]
    else:
        chosen = [sorted_days[-1]]
    for d in chosen:
        _print_day(d, days[d], sw, mm_per_px, args.top)
        print()
    if len(chosen) > 1:
        print("=== summary ===")
        print(
            f"  {'day':<12} {'pixels':>10} {'widths':>8} {'keys':>8} "
            f"{'clicks':>8} {'active_m':>10}"
        )
        for d in chosen:
            b = days[d]
            px = b.get("total_pixels", 0.0)
            nk = sum(b.get("key_counts", {}).values())
            nc = sum(b.get("click_counts", {}).values())
            am = b.get("active_seconds", 0) / 60
            print(
                f"  {d:<12} {px:>10.0f} {px / sw:>8.2f} "
                f"{nk:>8d} {nc:>8d} {am:>10.1f}"
            )
    return 0


def cmd_reset(args: argparse.Namespace) -> int:
    data = _load_data()
    if data is None:
        print("no stats file")
        return 1
    days = data.get("days", {})
    if args.all:
        data["days"] = {}
    else:
        if args.day not in days:
            print(f"no data for {args.day}")
            return 1
        del days[args.day]
    DATA_FILE.write_text(json.dumps(data, indent=2, sort_keys=True))
    print("reset complete")
    return 0


def cmd_export(args: argparse.Namespace) -> int:
    data = _load_data()
    if data is None:
        print("no stats file")
        return 1
    days = data.get("days", {})
    if args.day:
        chosen = [args.day] if args.day in days else []
    else:
        chosen = sorted(days.keys())
    if not chosen:
        print("nothing to export")
        return 1
    sw = _screen_width(data)
    mm_per_px = _mm_per_px(data)
    out_path = Path(args.out) if args.out else None
    out = out_path.open("w", newline="") if out_path else sys.stdout
    try:
        w = csv.writer(out)
        w.writerow([
            "day",
            "total_pixels",
            "screen_widths",
            "meters",
            "total_keys",
            "total_clicks",
            "click_left",
            "click_right",
            "click_middle",
            "scroll_vertical",
            "scroll_horizontal",
            "active_seconds",
            "peak_wpm",
        ])
        for d in chosen:
            b = days[d]
            px = b.get("total_pixels", 0.0)
            meters = px * mm_per_px / 1000.0
            cc = b.get("click_counts", {})
            sc = b.get("scroll_ticks", {})
            w.writerow([
                d,
                f"{px:.1f}",
                f"{px / sw:.4f}",
                f"{meters:.2f}",
                sum(b.get("key_counts", {}).values()),
                sum(cc.values()),
                cc.get("left", 0),
                cc.get("right", 0),
                cc.get("middle", 0),
                sc.get("vertical", 0),
                sc.get("horizontal", 0),
                b.get("active_seconds", 0),
                f"{b.get('peak_wpm', 0):.0f}",
            ])
    finally:
        if out_path:
            out.close()
    if out_path:
        print(f"wrote {out_path}")
    return 0


def cmd_web(args: argparse.Namespace) -> int:
    from webui import serve

    serve(port=args.port)
    return 0


def cmd_run(_args: argparse.Namespace) -> int:
    tracker = Tracker()
    stop = threading.Event()

    def save_loop() -> None:
        while not stop.wait(SAVE_INTERVAL):
            tracker.save()

    def shutdown(*_: object) -> None:
        stop.set()
        tracker.save()
        sys.exit(0)

    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGINT, shutdown)

    threading.Thread(target=save_loop, daemon=True).start()

    ml = mouse.Listener(
        on_move=tracker.on_move,
        on_click=tracker.on_click,
        on_scroll=tracker.on_scroll,
    )
    kl = keyboard.Listener(on_press=tracker.on_press)
    ml.start()
    kl.start()
    ml.join()
    kl.join()
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(prog="input-tracker")
    sub = parser.add_subparsers(dest="cmd")

    show = sub.add_parser("show", help="print stats")
    show.add_argument("--day")
    show.add_argument("--last", type=int)
    show.add_argument("--all", action="store_true")
    show.add_argument("--top", type=int, default=20)

    reset = sub.add_parser("reset", help="clear a day or everything")
    g = reset.add_mutually_exclusive_group(required=True)
    g.add_argument("--day")
    g.add_argument("--all", action="store_true")

    export = sub.add_parser("export", help="export CSV")
    export.add_argument("--day")
    export.add_argument("--out")

    web = sub.add_parser("web", help="serve dashboard (loopback only)")
    web.add_argument("--port", type=int, default=7070)

    sub.add_parser("run", help="run tracker daemon (default)")

    args = parser.parse_args()
    handlers = {
        "show": cmd_show,
        "reset": cmd_reset,
        "export": cmd_export,
        "web": cmd_web,
        "run": cmd_run,
        None: cmd_run,
    }
    sys.exit(handlers[args.cmd](args))


if __name__ == "__main__":
    main()
