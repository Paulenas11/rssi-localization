from nicegui import ui
import asyncio
import socket
import json
import time

UDP_IP = "0.0.0.0"
UDP_PORT = 5005

selected_mac = None
latest_rssi = None
last_seen = {}

async def udp_listener():
    global latest_rssi, last_seen

    loop = asyncio.get_event_loop()
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind((UDP_IP, UDP_PORT))
    sock.setblocking(False)

    print(f"Listening on UDP port {UDP_PORT}...")

    while True:
        try:
            data, addr = await loop.run_in_executor(None, sock.recvfrom, 4096)
        except BlockingIOError:
            await asyncio.sleep(0.01)
            continue

        try:
            msg = json.loads(data.decode())
        except:
            continue

        if isinstance(msg, dict):
            msg = [msg]

        esp_id = msg[0].get("esp_id", msg[0].get("id", None))
        if esp_id is None:
            continue

        last_seen[esp_id] = time.time()

        for entry in msg:
            mac = entry.get("mac", "").lower()
            rssi = entry.get("rssi", None)

            if selected_mac and mac == selected_mac.lower():
                latest_rssi = rssi

        await asyncio.sleep(0.01)


# ---------------- UI ----------------

ui.label("MAC adresas:")
mac_input = ui.input(placeholder="AA:BB:CC:DD:EE:FF")

def start_reading():
    global selected_mac
    selected_mac = mac_input.value
    print("Selected MAC:", selected_mac)

ui.button("Start", on_click=start_reading)

rssi_label = ui.label("RSSI: ---")

def update_ui():
    if latest_rssi is not None:
        rssi_label.set_text(f"RSSI: {latest_rssi} dBm")
    else:
        rssi_label.set_text("RSSI: ---")

ui.timer(1, update_ui)


# ---------------- START UDP LISTENER (universal method) ----------------

started = False

def start_background_tasks():
    global started
    if not started:
        started = True
        asyncio.create_task(udp_listener())

# Paleidžiam tik vieną kartą, kai UI jau veikia
ui.timer(0.1, start_background_tasks)


ui.run(host='0.0.0.0', port=8080, reload=False)
