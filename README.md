# Speedtest Logger

📡 **Automatisierte Internet-Speedtests mit Logging und Durchschnittsberechnung**

Dieses Python-Skript führt **automatische Internet-Speedtests** durch und speichert die Ergebnisse in **täglichen Log-Dateien**. Es berechnet außerdem **stündliche und tägliche Durchschnittswerte**, um **langfristige Netzwerkprobleme** zu analysieren.

---

## 📌 Features

✅ Regelmäßige Speedtests alle 10 Minuten  
✅ Speicherung der Ergebnisse in `.txt`-Dateien  
✅ Berechnung des stündlichen und täglichen Durchschnitts  
✅ Mindestgeschwindigkeit kann definiert werden  
✅ Paketverlust-Messung für bessere Analyse  
✅ Versteckte `.env` Datei für den Speicherort (sicher für GitHub)  
✅ Automatisches Weiterlaufen nach Verbindungsabbrüchen  

---

## 🚀 Installation & Setup

### 1️⃣ Python & Bibliotheken installieren

Falls du Python noch nicht hast, lade es hier herunter und installiere es:  
[https://www.python.org/downloads/](https://www.python.org/downloads/)

Danach öffne eine Konsole (PowerShell, CMD oder Terminal) und gib Folgendes ein:

pip install speedtest-cli requests python-dotenv

Falls du Linux oder macOS nutzt und `pip` nicht funktioniert:

python3 -m pip install speedtest-cli requests python-dotenv

---

### 2️⃣ `.env` Datei erstellen

Erstelle eine Datei `.env` im Hauptverzeichnis deines Projekts und füge den Speicherort für die Logs hinzu:

LOG_DIR=C:/Users/DEIN_NAME/Desktop/SpeedtestLogs

Füge `.env` zur `.gitignore` hinzu, damit sie nicht auf **GitHub** hochgeladen wird:

## Sensible Daten nicht hochladen

.env

---

### 3️⃣ Skript starten

Starte das Skript mit:

python speedtest2.py

Falls du Linux oder macOS nutzt:

python3 speedtest2.py

Das Skript wird nun **alle 10 Minuten** einen **Speedtest** durchführen und die Ergebnisse speichern.  
Zum Beenden drücke **STRG + C**.

---

## 📂 Log-Dateien

Die Speedtest-Ergebnisse werden in **täglichen Log-Dateien** gespeichert:

C:/Users/DEIN_NAME/Desktop/SpeedtestLogs/log_speedtest_YYYY-MM-DD.txt

### 📜 Beispielhafte Log-Datei (`log_speedtest_2025-03-07.txt`)

2025-03-07 12:48:37 | Server: Frankfurt am Main (Germany, DE) [speedtest1.synlinq.de:8080] | IP: 95.90.21.104 | ISP: Vodafone GmbH | Download: 506.77 Mbit/s | Upload: 28.85 Mbit/s | Ping: 28.94 ms | Paketverlust: 0% | ⚠ UNTER DER MINDESTGESCHWINDIGKEIT!
2025-03-07 13:00:00 | 📊 STÜNDLICHER DURCHSCHNITT | Download: 510.50 Mbit/s | Upload: 29.10 Mbit/s | Ping: 27.80 ms | Paketverlust: 1.2%
2025-03-07 23:55:00 | 📈 TÄGLICHER DURCHSCHNITT | Download: 500.90 Mbit/s | Upload: 28.50 Mbit/s | Ping: 28.50 ms | Paketverlust: 2.0%

---

## ⚡ Automatischer Start (Windows)

Falls du das Skript beim **Windows-Start automatisch ausführen** willst:

1. **Windows-Taste + R** → `shell:startup` eingeben.  
2. Erstelle eine **Verknüpfung** zu `pythonw.exe` und trage den Pfad zum Skript ein:

C:\Pfad\zu\pythonw.exe C:\Pfad\zu\speedtest2.py

Jetzt wird das Skript **automatisch bei jedem Systemstart ausgeführt**. 🚀

---

## 🛠️ Fehlerbehebung

Falls `speedtest-cli` nicht funktioniert oder `403 Forbidden` zurückgibt:

pip install --upgrade speedtest-cli

Falls das Skript **Fehler bei der Serververbindung hat**, versuche es mit einem anderen Speedtest-Server:

st.get_servers([])
st.get_best_server()

Falls du Probleme mit der UTF-8-Kodierung hast:

with open(text_log, "a", encoding="utf-8", errors="ignore") as file:
    file.write(log_entry)

Falls du das Skript stoppen möchtest, kannst du einfach **STRG + C** drücken.

Falls du prüfen willst, ob `speedtest-cli` funktioniert, teste es direkt in der Konsole mit:

speedtest

---

## 📜 Lizenz

Dieses Projekt steht unter der **MIT-Lizenz** – feel free to use & modify!  

🔥 Viel Erfolg mit deinem Speedtest-Logger! Wenn du Fragen hast, **melde dich einfach!** 🚀💪😎
