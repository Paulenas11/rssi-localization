from nicegui import ui
import asyncio
import socket
import json
import time

UDP_IP = "0.0.0.0"
UDP_PORT = 5005

selected_mac = None
last_seen = {}

# Saugo visų ESP RSSI į pasirinktą MAC
rssi_data = {}   # rssi_data[esp_id] = rssi


# ---------------- UDP LISTENER ----------------

async def udp_listener():
    global rssi_data, last_seen, selected_mac

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

        # Jei MAC nepasirinktas – nieko nedarom
        if not selected_mac:
            continue

        # Tikrinam visus įrašus
        for entry in msg:
            mac = entry.get("mac", "").lower()
            rssi = entry.get("rssi")

            # Jei šitas įrašas yra apie pasirinktą MAC
            if mac == selected_mac.lower():
                rssi_data[esp_id] = rssi

        await asyncio.sleep(0.01)


# ---------------- UI ----------------

ui.label("MAC adresas:")
mac_input = ui.input(placeholder="AA:BB:CC:DD:EE:FF")

def start_reading():
    global selected_mac, rssi_data
    selected_mac = mac_input.value.strip().lower()
    rssi_data = {}  # išvalom senus duomenis
    print("Selected MAC:", selected_mac)

ui.button("Start", on_click=start_reading)

# Lentelė tik pasirinkto MAC RSSI
table = ui.table(
    columns=[
        {'name': 'esp', 'label': 'ESP ID', 'field': 'esp'},
        {'name': 'rssi', 'label': 'RSSI', 'field': 'rssi'},
    ],
    rows=[],
)


def update_ui():
    rows = []
    for esp_id, rssi in rssi_data.items():
        rows.append({
            'esp': esp_id,
            'rssi': rssi,
        })
    table.rows = rows


ui.timer(0.5, update_ui)


# ---------------- START UDP LISTENER ----------------

started = False

def start_background_tasks():
    global started
    if not started:
        started = True
        asyncio.create_task(udp_listener())

ui.timer(0.1, start_background_tasks)


ui.run(host='0.0.0.0', port=8080, reload=False)
