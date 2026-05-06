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
known_macs = set()


# ---------------- UDP LISTENER ----------------

async def udp_listener():
    global rssi_data, known_macs

    loop = asyncio.get_event_loop()
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

    # Leidžia perleisti portą, jei senas procesas neužsidarė
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

        for entry in msg:
            mac = entry.get("mac", "").lower()
            rssi = entry.get("rssi")

            if mac:
                known_macs.add(mac)
                rssi_data[esp_id][mac] = rssi

        await asyncio.sleep(0.01)


# ---------------- UI ----------------

ui.label("Pasirinkite MAC adresą:")

mac_dropdown = ui.select(options=[], value=None)


def refresh_dropdown():
    mac_dropdown.options = sorted(list(known_macs))


ui.timer(1, refresh_dropdown)


def on_mac_selected(e):
    global selected_mac
    selected_mac = e.value
    print("Selected MAC:", selected_mac)


mac_dropdown.on('update:model-value', on_mac_selected)


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
