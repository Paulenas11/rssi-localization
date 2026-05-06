from nicegui import ui
import asyncio
import socket
import json
import time

UDP_IP = "0.0.0.0"
UDP_PORT = 5005

selected_mac = None

# Saugo visų ESP duomenis: rssi_data[esp_id][mac] = rssi
rssi_data = {}


# ---------------- UDP LISTENER ----------------

async def udp_listener():
    global rssi_data

    loop = asyncio.get_event_loop()
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
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

        if esp_id not in rssi_data:
            rssi_data[esp_id] = {}

        # Kiekvienas ESP siunčia daug MAC įrašų
        for entry in msg:
            mac = entry.get("mac", "").lower()
            rssi = entry.get("rssi")

            if mac:
                rssi_data[esp_id][mac] = rssi

        await asyncio.sleep(0.01)


# ---------------- UI ----------------

ui.label("Įveskite MAC adresą (rankiniu būdu):")
mac_input = ui.input(placeholder="AA:BB:CC:DD:EE:FF")

def set_mac():
    global selected_mac
    selected_mac = mac_input.value.strip().lower()
    print("Selected MAC:", selected_mac)

ui.button("Start", on_click=set_mac)


# Lentelė pasirinkto MAC RSSI
table = ui.table(
    columns=[
        {'name': 'esp', 'label': 'ESP ID', 'field': 'esp'},
        {'name': 'rssi', 'label': 'RSSI', 'field': 'rssi'},
    ],
    rows=[],
)


def update_table():
    if not selected_mac:
        table.rows = []
        return

    rows = []
    for esp_id, macs in rssi_data.items():
        if selected_mac in macs:
            rows.append({
                'esp': esp_id,
                'rssi': macs[selected_mac],
            })

    table.rows = rows


ui.timer(0.3, update_table)


# ---------------- START UDP LISTENER ----------------

started = False

def start_background_tasks():
    global started
    if not started:
        started = True
        asyncio.create_task(udp_listener())

ui.timer(0.1, start_background_tasks)


ui.run(host='0.0.0.0', port=8080, reload=False)
