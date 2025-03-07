import speedtest
import time
import requests
import subprocess
import os
import dotenv
from datetime import datetime

# .env Datei laden
dotenv.load_dotenv()

# Speicherort aus .env oder Standardwert nutzen
LOG_DIR = os.getenv("LOG_DIR", "C:/StandardOrdner/Fallback")

# Mindestgeschwindigkeit laut Vertrag
MIN_DOWNLOAD = 600  # in Mbit/s
MIN_UPLOAD = 10  # in Mbit/s

# Intervall zwischen den Speedtests in Sekunden
INTERVAL = 600  # 10 Minuten

# Speichert die Messwerte für Durchschnittsberechnung
hourly_results = []
daily_results = []

def get_log_files():
    """Gibt die Dateipfade für das aktuelle Tageslog (Text und CSV) zurück"""
    today = datetime.now().strftime("%Y-%m-%d")
    text_log = os.path.join(LOG_DIR, f"log_speedtest_{today}.txt")
    csv_log = os.path.join(LOG_DIR, f"log_speedtest_{today}.csv")
    return text_log, csv_log

def get_public_ip():
    """Ermittelt die öffentliche IP-Adresse und den ISP"""
    try:
        response = requests.get("https://ipinfo.io/json", timeout=5)
        response.raise_for_status()
        data = response.json()
        return data.get("ip", "Unbekannt"), data.get("org", "Unbekannt")
    except requests.RequestException:
        return "Keine Verbindung", "Keine Verbindung"

def get_ping_loss():
    """Misst Paketverlust durch 10 Pings an Google DNS"""
    try:
        result = subprocess.run(
            ["ping", "-n", "10", "8.8.8.8"],  
            capture_output=True, text=True, timeout=10, encoding="utf-8", errors="ignore"
        )
        output = result.stdout
        loss_line = [line for line in output.split("\n") if "Verloren" in line]
        if loss_line:
            loss_percent = loss_line[0].split("(")[-1].split("%")[0]
        else:
            loss_percent = "0"
        return int(loss_percent)
    except Exception:
        return -1  # Fehlerwert

def calculate_average(results):
    """Berechnet den Durchschnitt aus den gesammelten Werten"""
    if not results:
        return 0, 0, 0, 0  # Falls keine Werte vorhanden sind

    avg_download = sum(r[0] for r in results) / len(results)
    avg_upload = sum(r[1] for r in results) / len(results)
    avg_ping = sum(r[2] for r in results) / len(results)
    avg_loss = sum(r[3] for r in results) / len(results)

    return avg_download, avg_upload, avg_ping, avg_loss

def log_averages():
    """Speichert den stündlichen und täglichen Durchschnitt in die Datei"""
    global hourly_results, daily_results

    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    text_log, _ = get_log_files()

    if hourly_results:
        avg_hourly_download, avg_hourly_upload, avg_hourly_ping, avg_hourly_loss = calculate_average(hourly_results)
        hourly_results.clear()  # Zurücksetzen der Liste nach Berechnung

        hourly_log = (
            f"{timestamp} | 📊 STÜNDLICHER DURCHSCHNITT | "
            f"Download: {avg_hourly_download:.2f} Mbit/s | Upload: {avg_hourly_upload:.2f} Mbit/s | "
            f"Ping: {avg_hourly_ping:.2f} ms | Paketverlust: {avg_hourly_loss:.2f}%\n"
        )

        with open(text_log, "a", encoding="utf-8", errors="ignore") as file:
            file.write(hourly_log)
        print(hourly_log.strip())

    if daily_results and datetime.now().hour == 23 and datetime.now().minute >= 55:  # Um 23:55 Uhr berechnen
        avg_daily_download, avg_daily_upload, avg_daily_ping, avg_daily_loss = calculate_average(daily_results)
        daily_results.clear()

        daily_log = (
            f"{timestamp} | 📈 TÄGLICHER DURCHSCHNITT | "
            f"Download: {avg_daily_download:.2f} Mbit/s | Upload: {avg_daily_upload:.2f} Mbit/s | "
            f"Ping: {avg_daily_ping:.2f} ms | Paketverlust: {avg_daily_loss:.2f}%\n"
        )

        with open(text_log, "a", encoding="utf-8", errors="ignore") as file:
            file.write(daily_log)
        print(daily_log.strip())

def run_speed_test():
    """Führt einen Speedtest durch und speichert die Ergebnisse"""
    try:
        st = speedtest.Speedtest()
        st.get_best_server()
        st.config['force_ipv4'] = True  # IPv6 deaktivieren

        # Speedtest durchführen
        download_speed = st.download() / 1_000_000  # in Mbit/s
        upload_speed = st.upload() / 1_000_000  # in Mbit/s
        ping = st.results.ping

        # Server-Infos abrufen
        server = st.get_best_server()
        server_name = server["name"]
        server_location = f"{server['country']}, {server['cc']}"
        server_host = server["host"]

        # Paketverlust abrufen
        packet_loss = get_ping_loss()

        # Öffentliche IP & ISP abrufen
        ip_address, isp = get_public_ip()

        # Check Mindestgeschwindigkeit
        status = "✅ OK" if download_speed >= MIN_DOWNLOAD and upload_speed >= MIN_UPLOAD else "⚠ UNTER DER MINDESTGESCHWINDIGKEIT!"

        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        text_log, _ = get_log_files()

        log_entry = (
            f"{timestamp} | Server: {server_name} ({server_location}) [{server_host}] | "
            f"IP: {ip_address} | ISP: {isp} | "
            f"Download: {download_speed:.2f} Mbit/s | Upload: {upload_speed:.2f} Mbit/s | "
            f"Ping: {ping:.2f} ms | Paketverlust: {packet_loss}% | {status}\n"
        )

        # Logs speichern
        with open(text_log, "a", encoding="utf-8", errors="ignore") as file:
            file.write(log_entry)

        print(f"Speedtest durchgeführt: {log_entry.strip()}")

        # Werte für Durchschnittsberechnung speichern
        hourly_results.append((download_speed, upload_speed, ping, packet_loss))
        daily_results.append((download_speed, upload_speed, ping, packet_loss))

        # Durchschnittswerte einmal pro Stunde speichern
        if datetime.now().minute == 0:
            log_averages()

    except Exception as e:
        print(f"Fehler: {str(e)}")

if __name__ == "__main__":
    print("Starte regelmäßige Speedtests... (Zum Beenden: STRG+C)")
    
    if not os.path.exists(LOG_DIR):
        os.makedirs(LOG_DIR)

    while True:
        run_speed_test()
        time.sleep(INTERVAL)
