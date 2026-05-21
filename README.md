---
tags:
  - tools
  - tracking
  - python
  - systemd
  - dashboard
---

# Input Tracker

Always-on Python daemon that records, per local calendar day:

- Mouse travel (pixels, screen widths, real-world meters via DPI)
- Click counts (left / right / middle)
- Scroll ticks (vertical / horizontal)
- Key press histogram + total
- Active seconds (any input that second)
- Peak typing speed (WPM, 60s sliding window)
- Hour-of-day activity heatmap (pixels, keys, clicks per hour)
- Per-app activity (active window's WM_CLASS)

Persisted to `~/.input-tracker/stats.json` every 30s and on shutdown.

## Files

- `tracker.py` — daemon + CLI (`show`, `reset`, `export`, `web`, `run`)
- `webui.py` — Chart.js dashboard (stdlib HTTP server, no Flask)
- `requirements.txt` — `pynput`, `python-xlib`
- `input-tracker.service` — systemd **user** unit
- `install.sh` — one-shot installer

## Install

```bash
./install.sh
```

Copies files to `~/.input-tracker/`, creates venv, asks two questions:

1. **Start input-tracker automatically at login?** (default Y)
   - Yes → `systemctl --user enable --now input-tracker.service` (runs now + every login)
   - No  → starts for this session only; you can `systemctl --user enable input-tracker.service` later
2. *(only if autostart=yes)* **Keep running when you're logged out?** (default N)
   - Yes → `loginctl enable-linger $USER`
   - No  → service stops when the last session closes

### Non-interactive

```bash
./install.sh --autostart --no-linger -y     # enable at login, stop on logout
./install.sh --no-autostart                 # install but don't autostart
./install.sh --autostart --linger -y        # enable at login + survive logout
```

## CLI

```bash
TRK=~/.input-tracker/venv/bin/python
TRACKER=~/.input-tracker/tracker.py

# run daemon (foreground; service does this for you)
$TRK $TRACKER run

# show today
$TRK $TRACKER show

# show last 7 days, with per-day breakdown + summary
$TRK $TRACKER show --last 7

# specific day
$TRK $TRACKER show --day 2026-05-15

# all recorded days
$TRK $TRACKER show --all --top 10

# clear a single day (e.g. AFK gaming session)
$TRK $TRACKER reset --day 2026-05-21

# nuke everything
$TRK $TRACKER reset --all

# CSV export (stdout or --out file)
$TRK $TRACKER export --out stats.csv

# Chart.js dashboard
$TRK $TRACKER web                    # → http://127.0.0.1:7070
$TRK $TRACKER web --port 8080
```

## Web dashboard

`tracker.py web` serves a single-page Chart.js dashboard.

### Today

- Stat cards: pixels, screen widths, meters, keys, clicks, scroll ticks, active time, peak WPM
- Bar: top 20 keys
- Stacked bar: keys + clicks by hour of day
- Doughnut: click distribution (left / right / middle)
- Bar: top apps by pixels moved while focused

### All time

- Stat cards: days tracked, total pixels / screen widths / meters / keys / clicks / active hours
- Line: daily pixels
- Line: daily keys
- Line: daily clicks
- Line: daily active minutes

Auto-refreshes every 15 seconds.

## Service control

```bash
systemctl --user status input-tracker
systemctl --user restart input-tracker
systemctl --user disable --now input-tracker
```

## JSON schema

```json
{
  "screen_width_px": 3440,
  "screen_width_mm": 800,
  "updated_at": 1779399151.27,
  "days": {
    "2026-05-21": {
      "total_pixels": 12345.6,
      "key_counts": {"a": 12, "<space>": 80},
      "click_counts": {"left": 102, "right": 14, "middle": 2},
      "scroll_ticks": {"vertical": 540, "horizontal": 0},
      "active_seconds": 4321,
      "peak_wpm": 87.0,
      "hours": {"14": {"pixels": 1234, "keys": 90, "clicks": 12}},
      "apps":  {"firefox": {"pixels": 2200, "keys": 60, "clicks": 30}}
    }
  }
}
```

Legacy flat schemas auto-migrate into the appropriate day bucket on load.

## Notes

- **X11 only.** Uses `python-xlib` for screen geometry, primary-output detection, active-window introspection. On Wayland, `pynput` cannot capture global input.
- "Screen widths" divides total pixel travel by the **primary output's width** (detected via XRandR — falls back to virtual canvas if no primary set).
- "Meters" multiplies pixel travel by `screen_width_mm / screen_width_px`. If EDID doesn't report a physical width (`screen_width_mm == 0`), meters will be 0.
- Active window is polled once per second via Xlib's `_NET_ACTIVE_WINDOW` — every input event tags into the cached `WM_CLASS`.
- Reset for a single day with `reset --day YYYY-MM-DD` (e.g. if you walked away while the cursor wiggled).
