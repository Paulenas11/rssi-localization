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

# Nauja struktūra: saugo visų ESP RSSI
rssi_data = {}   # rssi_data[esp_id][mac] = rssi


# ---------------- UDP LISTENER ----------------

async def udp_listener():
    global latest_rssi, last_seen, rssi_data

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

        esp_id = msg[0].get("esp_id", msg[0].get("id"))
        if esp_id is None:
            continue

        last_seen[esp_id] = time.time()

        if esp_id not in rssi_data:
            rssi_data[esp_id] = {}

        for entry in msg:
            mac = entry.get("mac", "").lower()
            rssi = entry.get("rssi")

            rssi_data[esp_id][mac] = rssi

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


# Lentelė visiems ESP RSSI
table = ui.table(
    columns=[
        {'name': 'esp', 'label': 'ESP ID', 'field': 'esp'},
        {'name': 'mac', 'label': 'MAC', 'field': 'mac'},
        {'name': 'rssi', 'label': 'RSSI', 'field': 'rssi'},
    ],
    rows=[],
)


def update_ui():
    # Atnaujinti pasirinkto MAC RSSI
    if latest_rssi is not None:
        rssi_label.set_text(f"RSSI: {latest_rssi} dBm")
    else:
        rssi_label.set_text("RSSI: ---")

    # Atnaujinti lentelę
    rows = []
    for esp_id, macs in rssi_data.items():
        for mac, rssi in macs.items():
            rows.append({
                'esp': esp_id,
                'mac': mac,
                'rssi': rssi,
            })
    table.rows = rows


ui.timer(1, update_ui)


# ---------------- START UDP LISTENER ----------------

started = False

def start_background_tasks():
    global started
    if not started:
        started = True
        asyncio.create_task(udp_listener())

ui.timer(0.1, start_background_tasks)


ui.run(host='0.0.0.0', port=8080, reload=False)
