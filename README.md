# Speedtest Logger

Speedtest Logger runs recurring internet speed tests, records each result to a daily log file, and writes hourly and daily summary lines for later inspection. It ships as two console-less Windows programs: a background logger that keeps the measurement series going, and a desktop window that displays it.

The purpose it is built for is evidence. Turning "the line feels slow" into a time-series that a provider cannot wave away means the series has to be unbroken and every number in it has to be defensible. Most of the design decisions below follow from that, and the most important one is which program does the measuring.

## What This Repository Demonstrates

- Separating a long-running measurement process from its user interface, with a plain append-only log file as the single source of truth between them.
- Choosing the measurement engine on evidence rather than convenience, and documenting the comparison that forced the choice.
- Cross-process mutual exclusion through a lock file, because two overlapping measurements both saturate the line and both record a wrong number.
- Aggregation state that survives a restart, plus summary lines carrying median and 5th percentile alongside the mean, because a mean alone does not survive being argued with.
- Packaging a Python project as standalone Windows executables, including the details that only bite once there is no console: suppressed child-process windows and diagnostics through `logging` instead of `print`.
- Treating a third-party binary as a dependency with a pinned version and a verified checksum instead of a committed blob.

## Tech Stack

- Python 3.13, standard library plus `python-dotenv`
- The official Ookla `speedtest` CLI as the measurement engine
- `tkinter` from the standard library for the window
- PyInstaller for the executables
- Windows `ping`, `tasklist`, `taskkill`, `powershell` and the Startup folder for the packet-loss fallback, process handling and autostart

## The Measurement Engine

This project started on the `speedtest-cli` Python library and moved off it, because on a fast line it does not measure the line, it measures itself.

Four references on the same connection within one hour, against servers 4 to 44 km away:

| Reference | Download | Note |
|---|---|---|
| Contract | 1000 Mbit/s | — |
| Network interface | ~940 Mbit/s practical ceiling | Intel I226-V negotiated at 1 Gbit/s |
| Official Ookla CLI | **909.91 Mbit/s** | ping 17.22 ms |
| 8 parallel HTTP downloads from Cloudflare | 670–710 Mbit/s | includes TCP ramp-up, so a floor |
| `speedtest-cli` 2.1.3 | 345–584 Mbit/s | ping 24–28 ms |

`speedtest-cli` was last released in 2021. It under-reported download throughput here by roughly 40 percent, and raising its thread count from the server-supplied 8 to 32 only lifted it to 584 Mbit/s. That is the dangerous direction for an evidence log: the provider runs the official client, gets 900, and the entire series loses its credibility — not unfairly, because it was wrong.

The official client also supplies three things the library could not:

- **Real packet loss**, instead of the estimate this project derived from a separate `ping` run. The `ping` path survives as a fallback for servers that do not report loss.
- **Jitter**, which matters for any complaint about calls or games rather than throughput.
- **A result URL** hosted by Ookla, recorded in every log line. That turns each entry into something the other side can verify independently, which no local text file can achieve on its own.

### Historical note on server selection

Before the engine changed, `speedtest-cli` picked the test server through Ookla's legacy XML endpoints (`speedtest-servers-static.php` and its siblings). Those are effectively retired: they still answer, but with a rotating remnant of roughly ten servers worldwide. With a correct geolocation of `49.4404, 11.8582` the nearest server on offer was 4426 km away, and the library dutifully measured against Uzbekistan:

```text
Server insgesamt in Liste: 10
kleinste Distanzen (km): [4426.3, 4460.7, 4461.0, 4518.6]
```

That produced 223.90 Mbit/s at 128 ms against the 501.28 Mbit/s at 28 ms that the same library reached against a German server. The interim fix fetched candidates from the current JSON endpoint instead; the official client does its own selection and makes that workaround unnecessary. `SERVER_COUNTRIES` survives from it, because a German complaint should not rest on a measurement to a French server.

## Architecture Overview

```
speedtest_logger/
  config.py      settings from .env, resolved next to the program
  ookla.py       locating and invoking the official client, parsing its JSON
  core.py        one measurement cycle, returns a Measurement and writes nothing
  storage.py     log file, persisted aggregation, cross-process lock, log parsing
  runner.py      the measurement loop, driven by callbacks and a stop event
  autostart.py   Startup-folder entry, plus starting and stopping the background process
  schedule.py    the scheduled-task alternative, for running without a logged-in user
speedtest2.py    command line entry point and the background logger
gui.py           the desktop window
speedtest.spec   PyInstaller build for both executables
tools/
  fetch-ookla.py downloads and verifies the official client
```

Two processes, one file. The background logger measures and appends; the window reads the log and renders it. Nothing is shared in memory, there is no socket and no IPC, and closing the window therefore cannot interrupt the series.

## Local Development

### Prerequisites

- Python 3.13
- Internet access for the measurement
- Windows for the packet-loss fallback, autostart, process controls and packaging as implemented

### Install

```bash
python -m venv .venv
.venv/Scripts/python.exe -m pip install -r requirements-dev.txt
python tools/fetch-ookla.py
```

`requirements.txt` holds the single runtime dependency, `requirements-dev.txt` adds PyInstaller. The third line fetches the measurement engine.

The Ookla client is **not committed**. Ookla distributes it under its own licence, and vendoring a third-party executable into a public repository is the wrong default. [tools/fetch-ookla.py](tools/fetch-ookla.py) downloads a pinned version, verifies its SHA-256 against a value recorded in the script, and refuses to extract on a mismatch. Updating the pinned version means updating both the version and the checksum there.

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
SERVER_ID=
OOKLA_CLI_PATH=
MEASUREMENT_TIMEOUT_SECONDS=180
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

The Ookla client is not bundled into them, so a packaged build needs it alongside: copy it to `dist/tools/ookla-speedtest.exe`, or point `OOKLA_CLI_PATH` at an existing copy.

Both executables are built with `console=False`. That is the reason every `subprocess` call passes `CREATE_NO_WINDOW` and why diagnostics go through `logging` rather than `print`: without a console, each child process would otherwise flash its own window, and `print` would write into nothing.

## The Window

`speedtest.exe` is a viewer and a remote control, not the measuring part:

- current download, upload, ping and packet loss from the newest log line
- the selected server with its id, the timestamp, and the verdict against the thresholds
- `Jetzt messen` for a single measurement on demand
- `Logger starten` / `Logger stoppen` for the background process
- `Autostart ein` / `Autostart aus` for the Startup-folder entry
- `Log-Ordner` to open the directory
- the most recent entries of the current day, with hourly and daily summaries dimmed and errors in red

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

`LOG_DIR`

- Where log files, aggregation state, the lock file and the PID file are written.
- Falls back to `C:/StandardOrdner/Fallback` when unset.

`MIN_DOWNLOAD` and `MIN_UPLOAD`

- Throughput thresholds in Mbit/s, used to mark measurements below the expected contract baseline.
- Default to `600` and `10`.

`INTERVAL_SECONDS`

- Polling interval for recurring runs. Defaults to `600`.

`SERVER_ID`

- Pins one Ookla server by numeric id, which the log line records for every measurement.
- Unset by default. Pinning is the strongest option for a comparable series, because it holds the server constant and leaves the line as the only variable.

`SERVER_COUNTRIES`

- Comma-separated allowlist for server selection, applied when `SERVER_ID` is unset. Defaults to `DE`.
- Accepts country codes for the neighbours listed in `core.py` as well as full country names as the client reports them. An empty value accepts whatever the client picks.

`OOKLA_CLI_PATH`

- Explicit path to the official client. Unset by default, in which case `tools/ookla-speedtest.exe` next to the program and then `PATH` are tried.
- A path that is set but missing is logged as a warning, and the remaining candidates are still tried.

`PING_HOST`, `PING_COUNT`, and `PING_TIMEOUT_SECONDS`

- Controls for the packet-loss fallback, used only when the chosen server reports no loss itself. Default to `8.8.8.8`, `10`, and `10`.

`MEASUREMENT_TIMEOUT_SECONDS`

- How long a single measurement may take before it is abandoned. Defaults to `180`, minimum `30`.

An invalid or out-of-range value is logged and replaced by its default rather than aborting the run.

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

A measurement entry records the timestamp, the server with its id, the external IP and ISP as the client reports them, download, upload, ping, jitter, packet loss, the verifiable result URL, and the threshold verdict:

```text
2026-11-03 19:42:02 | Server: Pfalzkom GMBH (Ludwigshafen, Germany) [speedtest.pfalzkom.de, id 28818] | IP: 2a02:810c::1 | ISP: Vodafone Germany | Download: 887.35 Mbit/s | Upload: 52.22 Mbit/s | Ping: 19.58 ms | Jitter: 3.90 ms | Packet loss: 0.00% | Result: https://www.speedtest.net/result/c/e84be3fd | OK
2026-11-03 19:59:59 | HOURLY AVERAGE | n=3 | Download: avg 850.00, median 850.00, p05 805.00 Mbit/s | Upload: avg 52.50, median 52.50, p05 52.05 Mbit/s | Ping: avg 19.50, median 19.50 ms | Packet loss: avg 1.00, max 2.00%
```

A server that reports no packet loss and no usable `ping` fallback writes `Packet loss: n/a`, and such a measurement contributes nothing to the loss average rather than dragging it towards a sentinel value.

Summaries are written when a period closes, which keeps the first measurement of a new hour out of the previous bucket. Because the buckets live in `aggregation-state.json` rather than in memory, two cases that used to lose data now work: a restart keeps the running hour, and a bucket left open by a previous run is summarised at the next start instead of being silently dropped.

### Why the summary lines carry more than a mean

A mean on its own is the weakest number in the file. One bad measurement drags it down and one good one props it up, and whoever is on the other side of the argument knows that. Seven measurements taken during development make the point:

```text
78.80   290.10   420.04   501.28   517.31   540.45   574.65   Mbit/s
mean 417.52        median 501.28        p05 142.19
```

The single outlier pulls the mean 84 Mbit/s below the median. The median says what the connection usually does, the 5th percentile says how bad its bad moments are, and `n` says how much weight the period can carry at all. With few samples in a period the 5th percentile converges on the minimum; that is the honest answer for that sample size rather than a defect.

Log files written before the format gained these columns still parse. The `avg` keyword is optional in the parser, and `median`, `p05`, `n`, jitter and the result URL come back as absent rather than zero — which also means a file spanning the engine change stays readable, though the numbers on either side of it are not comparable.

## Operational Notes

- Public IP and ISP come from the measurement itself rather than a separate lookup service, which removes a failure mode and keeps those fields consistent with the result URL. Note that the client may report an IPv6 address where an earlier IPv4 lookup reported something different.
- A missing measurement engine, a server that cannot be reached, or a filter that matches nothing all skip the measurement and write an `ERROR` line. A visible gap is preferable to a plausible-looking but wrong number in a log intended as evidence.
- Exceptions during a measurement are logged to the current day's file and the loop continues.
- The measurement runs on a worker thread when triggered from the window; Tk widgets are only ever touched from the main thread.

## Known Limitations

- A gigabit interface caps what can be demonstrated at roughly 940 Mbit/s. A 1000 Mbit/s contract cannot be fully verified through a 1 Gbit/s link no matter which client measures it.
- A measurement reflects the line including whatever else is using it at that moment. Concurrent traffic from the same machine or network is not detected or compensated.
- Windows only in practice. The packet-loss regex handles Unix output, but autostart, the process controls and the packaging target Windows, and `tools/fetch-ookla.py` only pins the win64 build.
- No test suite. Verification during development was done through scripted assertions over the formatting, parsing, percentile, aggregation, locking and binary-discovery paths rather than committed tests.

## Repository Scope

This is a focused utility, not a framework. There is no database, no dashboard, and no deployment pipeline: the persistence layer is a text file on purpose, because a text file is still readable in five years without this program.
