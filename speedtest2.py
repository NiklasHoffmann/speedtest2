import os
import re
import subprocess
import time
from datetime import date, datetime, time as dt_time, timedelta
from pathlib import Path

import dotenv
import requests
import speedtest

dotenv.load_dotenv()


def get_env_int(name, default, minimum=0):
    raw_value = os.getenv(name)
    if raw_value is None or raw_value.strip() == "":
        return default

    try:
        value = int(raw_value)
    except ValueError:
        print(f"Invalid value for {name}: {raw_value!r}. Using default {default}.")
        return default

    if value < minimum:
        print(f"Value for {name} must be >= {minimum}. Using default {default}.")
        return default

    return value


LOG_DIR = Path(os.getenv("LOG_DIR", "C:/StandardOrdner/Fallback")).expanduser()
MIN_DOWNLOAD = get_env_int("MIN_DOWNLOAD", 600, minimum=0)
MIN_UPLOAD = get_env_int("MIN_UPLOAD", 10, minimum=0)
INTERVAL = get_env_int("INTERVAL_SECONDS", 600, minimum=1)
PING_HOST = os.getenv("PING_HOST", "8.8.8.8").strip() or "8.8.8.8"
PING_COUNT = get_env_int("PING_COUNT", 10, minimum=1)
PING_TIMEOUT = get_env_int("PING_TIMEOUT_SECONDS", 10, minimum=1)
IPINFO_URL = "https://ipinfo.io/json"
SERVER_API_URL = "https://www.speedtest.net/api/js/servers"
BROWSER_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)
SERVER_LIST_LIMIT = get_env_int("SERVER_LIST_LIMIT", 10, minimum=1)
SERVER_COUNTRIES = [
    code.strip().upper()
    for code in os.getenv("SERVER_COUNTRIES", "DE").split(",")
    if code.strip()
]

hourly_results = []
daily_results = []
current_hour_bucket = None
current_day_bucket = None


def ensure_log_dir():
    LOG_DIR.mkdir(parents=True, exist_ok=True)


def get_text_log_path(log_date=None):
    target_date = log_date or datetime.now().date()
    return LOG_DIR / f"log_speedtest_{target_date:%Y-%m-%d}.txt"


def append_log_entry(message, log_date=None):
    ensure_log_dir()
    log_path = get_text_log_path(log_date)
    with log_path.open("a", encoding="utf-8", errors="ignore") as file:
        file.write(f"{message}\n")


def format_average_entry(timestamp, label, averages):
    avg_download, avg_upload, avg_ping, avg_loss = averages
    return (
        f"{timestamp:%Y-%m-%d %H:%M:%S} | {label} | "
        f"Download: {avg_download:.2f} Mbit/s | Upload: {avg_upload:.2f} Mbit/s | "
        f"Ping: {avg_ping:.2f} ms | Packet loss: {avg_loss:.2f}%"
    )


def get_public_ip():
    try:
        response = requests.get(IPINFO_URL, timeout=5)
        response.raise_for_status()
        data = response.json()
        return data.get("ip", "Unknown"), data.get("org", "Unknown")
    except requests.RequestException:
        return "Unavailable", "Unavailable"


def fetch_candidate_servers():
    """Return nearby speedtest servers from the current Ookla endpoint.

    speedtest-cli still reads the legacy XML server lists, which today return a
    rotating handful of servers thousands of kilometres away. Measuring against
    those describes the route, not the line, so candidates are fetched here and
    handed to get_best_server() explicitly.
    """
    response = requests.get(
        SERVER_API_URL,
        params={"engine": "js", "limit": SERVER_LIST_LIMIT},
        headers={
            "User-Agent": BROWSER_USER_AGENT,
            "Referer": "https://www.speedtest.net/",
            "Accept": "application/json",
        },
        timeout=10,
    )
    response.raise_for_status()
    payload = response.json()

    if not isinstance(payload, list):
        raise RuntimeError("Unexpected server list format from speedtest.net.")

    candidates = []
    for entry in payload:
        if not isinstance(entry, dict) or not entry.get("url"):
            continue

        country_code = str(entry.get("cc", "")).upper()
        if SERVER_COUNTRIES and country_code not in SERVER_COUNTRIES:
            continue

        server = dict(entry)
        server["cc"] = country_code
        try:
            server["d"] = float(entry.get("distance", 0))
        except (TypeError, ValueError):
            server["d"] = 0.0
        candidates.append(server)

    candidates.sort(key=lambda server: server["d"])
    return candidates


def extract_packet_loss(output):
    patterns = [
        r"\((\d+)%\s*(?:loss|verlust)\)",
        r"(\d+(?:\.\d+)?)%\s*packet loss",
    ]
    for pattern in patterns:
        match = re.search(pattern, output, flags=re.IGNORECASE)
        if match:
            return int(round(float(match.group(1))))
    return -1


def get_ping_loss():
    ping_flag = "-n" if os.name == "nt" else "-c"
    try:
        result = subprocess.run(
            ["ping", ping_flag, str(PING_COUNT), PING_HOST],
            capture_output=True,
            text=True,
            timeout=PING_TIMEOUT,
            encoding="utf-8",
            errors="ignore",
        )
    except (OSError, subprocess.SubprocessError):
        return -1

    output = f"{result.stdout}\n{result.stderr}"
    return extract_packet_loss(output)


def calculate_average(results):
    if not results:
        return 0, 0, 0, 0

    avg_download = sum(result[0] for result in results) / len(results)
    avg_upload = sum(result[1] for result in results) / len(results)
    avg_ping = sum(result[2] for result in results) / len(results)
    avg_loss = sum(result[3] for result in results) / len(results)
    return avg_download, avg_upload, avg_ping, avg_loss


def flush_completed_periods(measurement_time):
    global current_hour_bucket, current_day_bucket

    hour_bucket = measurement_time.replace(minute=0, second=0, microsecond=0)
    day_bucket = measurement_time.date()

    if current_hour_bucket is not None and current_hour_bucket != hour_bucket and hourly_results:
        summary_time = current_hour_bucket + timedelta(hours=1) - timedelta(seconds=1)
        hourly_entry = format_average_entry(
            summary_time,
            "HOURLY AVERAGE",
            calculate_average(hourly_results),
        )
        append_log_entry(hourly_entry, log_date=current_hour_bucket.date())
        print(hourly_entry)
        hourly_results.clear()

    if current_day_bucket is not None and current_day_bucket != day_bucket and daily_results:
        summary_time = datetime.combine(current_day_bucket, dt_time(23, 59, 59))
        daily_entry = format_average_entry(
            summary_time,
            "DAILY AVERAGE",
            calculate_average(daily_results),
        )
        append_log_entry(daily_entry, log_date=current_day_bucket)
        print(daily_entry)
        daily_results.clear()

    current_hour_bucket = hour_bucket
    current_day_bucket = day_bucket


def get_status(download_speed, upload_speed):
    if download_speed >= MIN_DOWNLOAD and upload_speed >= MIN_UPLOAD:
        return "OK"
    return "BELOW MINIMUM THRESHOLD"


def run_speed_test():
    measurement_time = datetime.now()

    try:
        st = speedtest.Speedtest()
        candidates = fetch_candidate_servers()
        if not candidates:
            allowed = ",".join(SERVER_COUNTRIES) or "any"
            raise RuntimeError(
                f"No speedtest server available for SERVER_COUNTRIES={allowed}. "
                "Skipping this measurement rather than logging a result from an "
                "unsuitable server."
            )

        server = st.get_best_server(servers=candidates)

        download_speed = st.download() / 1_000_000
        upload_speed = st.upload() / 1_000_000
        ping = st.results.ping

        packet_loss = get_ping_loss()
        ip_address, isp = get_public_ip()
        status = get_status(download_speed, upload_speed)

        flush_completed_periods(measurement_time)

        log_entry = (
            f"{measurement_time:%Y-%m-%d %H:%M:%S} | "
            f"Server: {server['name']} ({server['country']}, {server['cc']}) "
            f"[{server['host']}, {server['d']:.0f} km] | "
            f"IP: {ip_address} | ISP: {isp} | "
            f"Download: {download_speed:.2f} Mbit/s | Upload: {upload_speed:.2f} Mbit/s | "
            f"Ping: {ping:.2f} ms | Packet loss: {packet_loss}% | {status}"
        )

        append_log_entry(log_entry, log_date=measurement_time.date())
        print(log_entry)

        hourly_results.append((download_speed, upload_speed, ping, packet_loss))
        daily_results.append((download_speed, upload_speed, ping, packet_loss))
    except Exception as exc:
        error_entry = f"{measurement_time:%Y-%m-%d %H:%M:%S} | ERROR | {exc}"
        print(error_entry)
        try:
            append_log_entry(error_entry, log_date=measurement_time.date())
        except OSError:
            print("Failed to write error entry to log file.")


if __name__ == "__main__":
    print("Starting recurring speed tests. Press Ctrl+C to stop.")
    ensure_log_dir()
    try:
        while True:
            run_speed_test()
            time.sleep(INTERVAL)
    except KeyboardInterrupt:
        print("Stopping speed tests.")

