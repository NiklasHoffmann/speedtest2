# Speedtest Logger

Speedtest Logger runs recurring internet speed tests, records each result to a daily log file, and writes hourly and daily summary lines for later inspection. It ships as two console-less Windows programs: a background logger that keeps the measurement series going, and a desktop window that displays it.

The purpose it is built for is evidence. Turning "the line feels slow" into a time-series the provider cannot wave away means the series has to be unbroken and every number in it has to be defensible. Most of the design decisions below follow from that.

## What This Repository Demonstrates

- Separating a long-running measurement process from its user interface, with a plain append-only log file as the single source of truth between them.
- Working around a decayed upstream dependency: `speedtest-cli` still reads Ookla's legacy XML server lists, which now return a rotating handful of servers thousands of kilometres away, so candidate servers are fetched from the current JSON endpoint and passed to the library explicitly.
- Cross-process mutual exclusion through a lock file, because two overlapping measurements both saturate the line and both record a wrong number.
- Aggregation state that survives a restart, so hourly and daily averages are not lost when the machine reboots.
- Combining multiple data sources in one measurement cycle: throughput from `speedtest-cli`, public IP and ISP lookup from `ipinfo.io`, and packet-loss estimation from the system `ping` command.
- Packaging a Python project as standalone Windows executables, including the details that only bite once there is no console: suppressed child-process windows and diagnostics through `logging` instead of `print`.

## Tech Stack

- Python 3.13
- `speedtest-cli`, `requests`, `python-dotenv`
- `tkinter` from the standard library for the window
- PyInstaller for the executables
- Windows `ping`, `tasklist`, `taskkill` and the Startup folder for process and autostart handling

## Architecture Overview

```
speedtest_logger/
  config.py      settings from .env, resolved next to the program
  core.py        one measurement cycle, returns a Measurement and writes nothing
  storage.py     log file, persisted aggregation, cross-process lock, log parsing
  runner.py      the measurement loop, driven by callbacks and a stop event
  autostart.py   Startup-folder entry, plus starting and stopping the background process
  schedule.py    the scheduled-task alternative, for running without a logged-in user
speedtest2.py    command line entry point and the background logger
gui.py           the desktop window
speedtest.spec   PyInstaller build for both executables
```

Two processes, one file. The background logger measures and appends; the window reads the log and renders it. Nothing is shared in memory, there is no socket and no IPC, and closing the window therefore cannot interrupt the series.

## Local Development

### Prerequisites

- Python 3.13
- Internet access for the speed test, the server list, and the ISP lookup
- Windows for packet-loss parsing, autostart, and the process controls as implemented

### Install dependencies

```bash
python -m venv .venv
.venv/Scripts/python.exe -m pip install -r requirements-dev.txt
```

`requirements.txt` holds the runtime dependencies, `requirements-dev.txt` adds PyInstaller. `speedtest-cli` is pinned, because the server selection depends on `get_best_server(servers=...)` accepting a pre-built candidate list.

### Configure environment

Use [.env.example](.env.example) as the starting point for a local `.env` file:

```env
LOG_DIR=C:/Users/YourName/Desktop/SpeedtestLogs
MIN_DOWNLOAD=600
MIN_UPLOAD=10
INTERVAL_SECONDS=600
PING_HOST=8.8.8.8
PING_COUNT=10
PING_TIMEOUT_SECONDS=10
SERVER_COUNTRIES=DE
SERVER_LIST_LIMIT=10
```

The `.env` file is read from next to the program first: the repository root when running from source, and the directory holding the executable in a packaged build. If there is none there, the current working directory is tried. Variables already present in the process environment are never overwritten by the file, so a packaged build can also be configured entirely from outside.

### Run from source

```bash
.venv/Scripts/python.exe speedtest2.py            # measure continuously
.venv/Scripts/python.exe speedtest2.py --once     # one measurement, then exit
.venv/Scripts/python.exe speedtest2.py --headless # no console output
.venv/Scripts/python.exe gui.py                   # the window
```

### Build the executables

```bash
.venv/Scripts/pyinstaller.exe speedtest.spec --noconfirm
```

This produces two single-file programs in `dist/`:

| File | Role |
|---|---|
| `speedtest.exe` | the window; double-click target on the desktop |
| `speedtest-logger.exe` | the background logger, no console, started at logon |

Both are built with `console=False`. That is the reason `subprocess` calls pass `CREATE_NO_WINDOW` and why diagnostics go through `logging` rather than `print`: without a console, every `ping` call would otherwise flash its own window, and `print` would write into nothing.

## The Window

`speedtest.exe` is a viewer and a remote control, not the measuring part:

- current download, upload, ping and packet loss from the newest log line
- the selected server with its distance, the timestamp, and the verdict against the thresholds
- `Jetzt messen` for a single measurement on demand
- `Logger starten` / `Logger stoppen` for the background process
- `Autostart ein` / `Autostart aus` for the Startup-folder entry
- `Log-Ordner` to open the directory
- the most recent entries of the current day, with hourly and daily averages dimmed and errors in red

A status dot reports whether the series is actually running, derived from the background process plus the age of the newest measurement rather than from a scheduler's own claim.

## Background Operation

The logger runs at logon through a shortcut in the per-user Startup folder:

```text
%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\SpeedtestLogger.lnk
```

This needs no administrator rights and no stored credentials, which is why it is the default. The window creates and removes the shortcut through `Autostart ein` / `Autostart aus`.

To keep measuring on a machine that is powered on with nobody logged in, use the scheduled task in [speedtest_logger/schedule.py](speedtest_logger/schedule.py) instead. From an elevated prompt:

```bat
schtasks /Create /TN SpeedtestLogger /TR "\"C:\path\to\speedtest-logger.exe\"" /SC ONSTART /RL LIMITED /F
```

Creating a task this way requires elevation; `schtasks /Create` answers `Zugriff verweigert` without it. That is the trade-off against the Startup folder, which has no such requirement.

## Configuration

### Environment variables

`LOG_DIR`

- Controls where log files, aggregation state, the lock file and the PID file are written.
- Falls back to `C:/StandardOrdner/Fallback` when unset.

`MIN_DOWNLOAD` and `MIN_UPLOAD`

- Throughput thresholds in Mbit/s, used to mark measurements below the expected contract baseline.
- Default to `600` and `10`.

`INTERVAL_SECONDS`

- Polling interval for recurring runs. Defaults to `600`.

`PING_HOST`, `PING_COUNT`, and `PING_TIMEOUT_SECONDS`

- Controls for packet-loss measurement. Default to `8.8.8.8`, `10`, and `10`.

`SERVER_COUNTRIES`

- Comma-separated allowlist of country codes for server selection. Defaults to `DE`.
- An empty value disables the filter and accepts any server the endpoint returns.

`SERVER_LIST_LIMIT`

- Number of candidate servers requested before filtering. Defaults to `10`.
- A narrow `SERVER_COUNTRIES` filter may need a higher limit to leave usable candidates.

An invalid or out-of-range value is logged and replaced by its default rather than aborting the run.

## Server Selection

`speedtest-cli` 2.1.3 resolves servers through Ookla's legacy XML endpoints (`speedtest-servers-static.php` and its siblings). Those endpoints are effectively retired: they still answer, but with a rotating remnant of roughly ten servers worldwide. The library's own `get_best_server()` then works correctly on unusable input and picks, for example, a server in Uzbekistan for a client in Germany. The resulting numbers describe the route to Central Asia, not the local line.

Observed directly, with a correct geolocation of `49.4404, 11.8582`:

```text
Server insgesamt in Liste: 10
kleinste Distanzen (km): [4426.3, 4460.7, 4461.0, 4518.6]
```

[speedtest_logger/core.py](speedtest_logger/core.py) therefore fetches candidates from the current JSON endpoint:

```text
https://www.speedtest.net/api/js/servers?engine=js&limit=<SERVER_LIST_LIMIT>
```

The request needs a browser `User-Agent` and a `Referer: https://www.speedtest.net/` header; without them the endpoint answers `403`. Results are filtered to `SERVER_COUNTRIES`, sorted by distance, and handed to `get_best_server(servers=...)`, which skips the legacy list entirely. The selected server's distance is recorded in each log line so an implausible pick is visible in the log itself.

The difference this makes on one connection, measured minutes apart:

| Server | Download | Ping |
|---|---|---|
| Tashkent, UZ (4461 km) | 223.90 Mbit/s | 128.03 ms |
| Mainz, DE (38 km) | 501.28 Mbit/s | 28.03 ms |

If the endpoint fails or no server matches the filter, the measurement is skipped and an `ERROR` line is logged instead. A visible gap is preferable to a plausible-looking but route-limited number in a log intended as evidence.

## Concurrency

Only one measurement may run at a time. Two overlapping runs share the available bandwidth, and both record a number that is roughly half of the truth. This is not hypothetical; it produced two entries 37 seconds apart during development, at 501.28 and 207.59 Mbit/s.

`measurement_lock()` in [speedtest_logger/storage.py](speedtest_logger/storage.py) serialises measurements across processes through an exclusively created lock file in `LOG_DIR`. The window and the background logger take turns: whoever loses the race skips the cycle. A skipped cycle is logged as a diagnostic warning and deliberately kept out of the measurement log, since nothing failed. A lock left behind by a killed process is treated as stale after 15 minutes and removed.

## Logging Behavior

`LOG_DIR` holds:

| File | Content |
|---|---|
| `log_speedtest_YYYY-MM-DD.txt` | the measurement trail, append-only, one day per file |
| `aggregation-state.json` | the open hourly and daily buckets |
| `speedtest-logger.log` | rotating program diagnostics, separate from the evidence trail |
| `measurement.lock` | present only while a measurement runs |
| `logger.pid` | PID of the running background logger |

Measurement entries include the timestamp, the selected server with its distance, public IP and ISP, download, upload, ping, packet loss, and the threshold verdict:

```text
2025-03-07 12:48:37 | Server: Frankfurt am Main (Germany, DE) [speedtest1.synlinq.de:8080, 48 km] | IP: 95.90.21.104 | ISP: Vodafone GmbH | Download: 506.77 Mbit/s | Upload: 28.85 Mbit/s | Ping: 28.94 ms | Packet loss: 0% | BELOW MINIMUM THRESHOLD
2025-03-07 12:59:59 | HOURLY AVERAGE | Download: 510.50 Mbit/s | Upload: 29.10 Mbit/s | Ping: 27.80 ms | Packet loss: 1.20%
```

Averages are written when a period closes, which keeps the first measurement of a new hour out of the previous bucket. Because the buckets live in `aggregation-state.json` rather than in memory, two cases that used to lose data now work: a restart keeps the running hour, and a bucket left open by a previous run is summarised at the next start instead of being silently dropped.

## Operational Notes

- Packet-loss parsing relies on the local `ping` command and supports common Windows and Unix-style loss output formats, including German output.
- Public IP and ISP metadata are fetched from `https://ipinfo.io/json` on every run; a failure there degrades to `Unavailable` rather than failing the measurement.
- The candidate server list is fetched on every run, so a changing location or a server going offline is picked up without a restart.
- Exceptions during a measurement are logged to the current day's file as an `ERROR` line, and the loop continues.
- The measurement runs on a worker thread when triggered from the window; Tk widgets are only ever touched from the main thread.

## Known Limitations

- `speedtest-cli` is unmaintained, last released in 2021. Its `download()` and `upload()` paths still rely on legacy per-server URLs on the same infrastructure that already broke for the server list. The durable fix would be the official Ookla CLI binary instead of this library.
- A measurement reflects the line including whatever else is using it at that moment. Concurrent traffic from the same machine or network is not detected or compensated.
- Windows only in practice. The packet-loss regex handles Unix output, but autostart, the process controls and the packaging target Windows.
- No test suite. The verification during development was done through scripted assertions over the formatting, parsing, aggregation and locking paths rather than committed tests.

## Repository Scope

This is a focused utility, not a framework. There is no database, no dashboard, and no deployment pipeline: the persistence layer is a text file on purpose, because a text file is still readable in five years without this program.
