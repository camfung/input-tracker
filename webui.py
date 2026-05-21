"""Chart.js dashboard for input-tracker. Serves over stdlib HTTP."""
from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from tracker import DATA_FILE, today_key

INDEX_HTML = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>Input Tracker</title>
  <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
  <style>
    body { font-family: -apple-system, system-ui, sans-serif; margin: 24px;
           background: #0f1116; color: #eaeaf2; }
    h1, h2, h3 { font-weight: 600; margin: 0 0 12px 0; }
    h1 { font-size: 28px; }
    h2 { font-size: 22px; margin-top: 24px; }
    h3 { font-size: 14px; color: #a0a4b8; text-transform: uppercase; letter-spacing: 0.05em; }
    .grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(380px, 1fr)); gap: 16px; }
    .card { background: #181a23; border: 1px solid #262838;
            border-radius: 10px; padding: 16px; }
    .stats { display: grid; grid-template-columns: repeat(auto-fit, minmax(140px, 1fr)); gap: 12px; }
    .stat { font-size: 26px; font-weight: 700; color: #f1f3fb; }
    .label { font-size: 11px; color: #8a8fa3; text-transform: uppercase; letter-spacing: 0.05em; margin-bottom: 4px; }
    section { margin-bottom: 32px; }
    canvas { width: 100% !important; height: 280px !important; }
    .refreshed { color: #6b7088; font-size: 11px; margin-top: 4px; }
  </style>
</head>
<body>
  <h1>Input Tracker</h1>
  <div class="refreshed">Auto-refresh every 15s &middot; <span id="refreshed"></span></div>

  <section>
    <h2>Today &mdash; <span id="today-date"></span></h2>
    <div class="card stats" id="today-stats"></div>
    <div class="grid" style="margin-top:16px">
      <div class="card"><h3>Top keys</h3><canvas id="today-keys"></canvas></div>
      <div class="card"><h3>Hour-of-day heatmap</h3><canvas id="today-hours"></canvas></div>
      <div class="card"><h3>Click distribution</h3><canvas id="today-clicks"></canvas></div>
      <div class="card"><h3>Top apps (pixels moved)</h3><canvas id="today-apps"></canvas></div>
    </div>
  </section>

  <section>
    <h2>All time</h2>
    <div class="card stats" id="alltime-stats"></div>
    <div class="grid" style="margin-top:16px">
      <div class="card"><h3>Daily pixels</h3><canvas id="alltime-pixels"></canvas></div>
      <div class="card"><h3>Daily keys</h3><canvas id="alltime-keys"></canvas></div>
      <div class="card"><h3>Daily clicks</h3><canvas id="alltime-clicks"></canvas></div>
      <div class="card"><h3>Daily active minutes</h3><canvas id="alltime-active"></canvas></div>
    </div>
  </section>

  <script>
    Chart.defaults.color = '#c0c4d6';
    Chart.defaults.borderColor = '#262838';
    const charts = {};

    async function fetchJson(u) { const r = await fetch(u); return r.json(); }

    function statCard(label, value) {
      return '<div><div class="label">' + label + '</div><div class="stat">' + value + '</div></div>';
    }
    function fmt(n, digits) {
      if (digits === undefined) digits = 0;
      return Number(n || 0).toLocaleString(undefined, {maximumFractionDigits: digits});
    }
    function fmtSeconds(s) {
      const m = s / 60;
      if (m < 60) return fmt(m, 1) + ' min';
      return fmt(m / 60, 2) + ' hr';
    }
    function topN(obj, n) {
      return Object.entries(obj || {}).sort((a, b) => b[1] - a[1]).slice(0, n);
    }
    function sumVals(obj) { return Object.values(obj || {}).reduce((a, b) => a + b, 0); }

    function destroyIf(id) { if (charts[id]) { charts[id].destroy(); delete charts[id]; } }

    function bar(id, labels, data, label, color) {
      destroyIf(id);
      charts[id] = new Chart(document.getElementById(id), {
        type: 'bar',
        data: { labels, datasets: [{ label, data, backgroundColor: color || '#5b8def' }] },
        options: { responsive: true, maintainAspectRatio: false, plugins: { legend: { display: false } } }
      });
    }
    function line(id, labels, data, label) {
      destroyIf(id);
      charts[id] = new Chart(document.getElementById(id), {
        type: 'line',
        data: { labels, datasets: [{ label, data, borderColor: '#5b8def',
          backgroundColor: 'rgba(91,141,239,0.18)', tension: 0.25, fill: true,
          pointRadius: 2 }] },
        options: { responsive: true, maintainAspectRatio: false, plugins: { legend: { display: false } } }
      });
    }
    function doughnut(id, labels, data) {
      destroyIf(id);
      charts[id] = new Chart(document.getElementById(id), {
        type: 'doughnut',
        data: { labels, datasets: [{ data,
          backgroundColor: ['#5b8def', '#f59e0b', '#10b981', '#ef4444', '#a855f7'] }] },
        options: { responsive: true, maintainAspectRatio: false }
      });
    }

    async function load() {
      const data = await fetchJson('/api/stats');
      const sw = data.screen_width_px || data.screen_width || 1;
      const mmPerPx = sw ? (data.screen_width_mm || 0) / sw : 0;
      const days = data.days || {};
      const sortedDays = Object.keys(days).sort();

      // ---- today ----
      const today = sortedDays[sortedDays.length - 1] || '';
      document.getElementById('today-date').textContent = today || '(no data)';
      const tb = days[today] || {};
      const tpx = tb.total_pixels || 0;
      const tkeys = sumVals(tb.key_counts);
      const tclicks = sumVals(tb.click_counts);
      const ts = tb.scroll_ticks || {};
      const tscroll = (ts.vertical || 0) + (ts.horizontal || 0);
      const tactive = tb.active_seconds || 0;
      const twpm = tb.peak_wpm || 0;
      const tmeters = tpx * mmPerPx / 1000;

      document.getElementById('today-stats').innerHTML = [
        statCard('Pixels moved', fmt(tpx)),
        statCard('Screen widths', fmt(tpx / sw, 2)),
        statCard('Meters', fmt(tmeters, 1)),
        statCard('Keys', fmt(tkeys)),
        statCard('Clicks', fmt(tclicks)),
        statCard('Scroll ticks', fmt(tscroll)),
        statCard('Active time', fmtSeconds(tactive)),
        statCard('Peak WPM', fmt(twpm)),
      ].join('');

      const topKeys = topN(tb.key_counts, 20);
      bar('today-keys', topKeys.map(x => x[0]), topKeys.map(x => x[1]), 'presses');

      const hours = [...Array(24).keys()];
      const tHours = tb.hours || {};
      const hourKeys = hours.map(h => (tHours[h] && tHours[h].keys) || 0);
      const hourClicks = hours.map(h => (tHours[h] && tHours[h].clicks) || 0);
      destroyIf('today-hours');
      charts['today-hours'] = new Chart(document.getElementById('today-hours'), {
        type: 'bar',
        data: { labels: hours.map(h => String(h).padStart(2, '0') + ':00'), datasets: [
          { label: 'keys', data: hourKeys, backgroundColor: '#5b8def' },
          { label: 'clicks', data: hourClicks, backgroundColor: '#f59e0b' },
        ]},
        options: { responsive: true, maintainAspectRatio: false,
          scales: { x: { stacked: true }, y: { stacked: true } } }
      });

      doughnut('today-clicks', Object.keys(tb.click_counts || {}), Object.values(tb.click_counts || {}));

      const apps = topN(
        Object.fromEntries(Object.entries(tb.apps || {}).map(([k, v]) => [k, v.pixels || 0])),
        10
      );
      bar('today-apps', apps.map(x => x[0]), apps.map(x => x[1]), 'pixels', '#10b981');

      // ---- all time ----
      const allPx = sortedDays.map(d => days[d].total_pixels || 0);
      const allKeys = sortedDays.map(d => sumVals(days[d].key_counts));
      const allClicks = sortedDays.map(d => sumVals(days[d].click_counts));
      const allActive = sortedDays.map(d => (days[d].active_seconds || 0) / 60);

      const sumPx = allPx.reduce((a, b) => a + b, 0);
      const sumKeys = allKeys.reduce((a, b) => a + b, 0);
      const sumClicks = allClicks.reduce((a, b) => a + b, 0);
      const sumActive = allActive.reduce((a, b) => a + b, 0);
      const sumMeters = sumPx * mmPerPx / 1000;

      document.getElementById('alltime-stats').innerHTML = [
        statCard('Days tracked', fmt(sortedDays.length)),
        statCard('Total pixels', fmt(sumPx)),
        statCard('Screen widths', fmt(sumPx / sw, 1)),
        statCard('Total meters', fmt(sumMeters, 1)),
        statCard('Total keys', fmt(sumKeys)),
        statCard('Total clicks', fmt(sumClicks)),
        statCard('Active hours', fmt(sumActive / 60, 1)),
      ].join('');

      line('alltime-pixels', sortedDays, allPx, 'pixels');
      line('alltime-keys', sortedDays, allKeys, 'keys');
      line('alltime-clicks', sortedDays, allClicks, 'clicks');
      line('alltime-active', sortedDays, allActive, 'minutes');

      document.getElementById('refreshed').textContent = 'last loaded ' + new Date().toLocaleTimeString();
    }

    load();
    setInterval(load, 15000);
  </script>
</body>
</html>
"""


class Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        if self.path in ("/", "/index.html"):
            self._send(200, "text/html; charset=utf-8", INDEX_HTML.encode())
            return
        if self.path == "/api/stats":
            body = DATA_FILE.read_bytes() if DATA_FILE.exists() else b"{}"
            self._send(200, "application/json", body)
            return
        if self.path == "/api/today":
            if not DATA_FILE.exists():
                self._send(200, "application/json", b"{}")
                return
            data = json.loads(DATA_FILE.read_text())
            today_data = data.get("days", {}).get(today_key(), {})
            self._send(200, "application/json", json.dumps(today_data).encode())
            return
        self._send(404, "text/plain", b"not found")

    def _send(self, status: int, ct: str, body: bytes) -> None:
        self.send_response(status)
        self.send_header("Content-Type", ct)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: object) -> None:  # noqa: A002
        return


def serve(port: int = 7070) -> None:
    host = "127.0.0.1"
    srv = ThreadingHTTPServer((host, port), Handler)
    print(f"input-tracker web ui: http://{host}:{port}")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        srv.shutdown()
