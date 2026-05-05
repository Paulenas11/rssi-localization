import socket
import json
import time

UDP_IP = "0.0.0.0"
UDP_PORT = 5000

# MAC adresas, kurį norime filtruoti (pvz. telefono MAC)
TARGET_MAC = "b8:27:eb:49:1e:1d"   # pakeisk į savo

print("=== ESP32 RSSI TEST STARTED ===")
print(f"Listening on UDP port {UDP_PORT}...\n")

sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
sock.bind((UDP_IP, UDP_PORT))

last_seen = {}   # last_seen[esp_id] = timestamp

while True:
    data, addr = sock.recvfrom(4096)

    try:
        msg = json.loads(data.decode())
    except:
        print("Invalid JSON received")
        continue

    # Jei ESP32 siunčia vieną objektą, paverčiam į masyvą
    if isinstance(msg, dict):
        msg = [msg]

    # ESP32-ID nustatomas iš pirmo elemento
    esp_id = msg[0].get("esp_id", msg[0].get("id", None))
    if esp_id is None:
        print("Invalid packet (missing esp_id):", msg)
        continue

    last_seen[esp_id] = time.time()

    print(f"\n===== ESP32-ID_{esp_id} connected =====")

    # 1) IŠVEDAM VISUS RASTUS MAC + RSSI
    print("All scanned MAC addresses:")
    for entry in msg:
        mac = entry.get("mac", "").lower()
        rssi = entry.get("rssi", None)
        print(f"  MAC: {mac}   RSSI: {rssi}")

    # 2) FILTRUOJAM TIK TARGET MAC
    print("\nFiltered results (TARGET MAC):")
    found_target = False
    for entry in msg:
        mac = entry.get("mac", "").lower()
        rssi = entry.get("rssi", None)

        if mac == TARGET_MAC:
            print(f"  >>> MATCH: {mac}   RSSI: {rssi}")
            found_target = True

    if not found_target:
        print("  (no matches)")

    # 3) RODOM AKTYVIUS ESP32
    now = time.time()
    alive = [f"ID_{k}" for k, t in last_seen.items() if now - t < 3]

    print("\nActive ESP32:", ", ".join(alive) if alive else "none")
    print("========================================\n")
