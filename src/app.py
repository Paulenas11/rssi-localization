from nicegui import ui
import asyncio
import socket
import json
import time

UDP_IP = "0.0.0.0"
UDP_PORT = 5005

# Saugo visų ESP duomenis
rssi_data = {}   # rssi_data[esp_id] = {'mac': mac, 'rssi': rssi, 'time': last_seen}


# ---------------- UDP LISTENER ----------------

async def udp_listener():
    global rssi_data

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

        # Debug: rodyti žalius duomenis
        print("RAW:", msg)

        if isinstance(msg, dict):
            msg = [msg]

        esp_id = msg[0].get("esp_id", msg[0].get("id"))
        if esp_id is None:
            continue

        # Imame tik pirmą įrašą (tavo ESP siunčia vieną MAC per paketą)
        entry = msg[0]
        mac = entry.get("mac", "").lower()
        rssi = entry.get("rssi")

        rssi_data[esp_id] = {
            'mac': mac,
            'rssi': rssi,
            'time': time.time(),
        }

        await asyncio.sleep(0.01)


# ---------------- UI ----------------

ui.label("DEBUG režimas: rodomi VISI RSSI iš ESP")

table = ui.table(
    columns=[
        {'name': 'esp', 'label': 'ESP ID', 'field': 'esp'},
        {'name': 'mac', 'label': 'MAC', 'field': 'mac'},
        {'name': 'rssi', 'label': 'RSSI', 'field': 'rssi'},
        {'name': 'age', 'label': 'Sek. nuo paskutinio paketo', 'field': 'age'},
    ],
    rows=[],
)


def update_ui():
    rows = []
    now = time.time()

    for esp_id, info in rssi_data.items():
        rows.append({
            'esp': esp_id,
            'mac': info['mac'],
            'rssi': info['rssi'],
            'age': round(now - info['time'], 1),
        })

    table.rows = rows


ui.timer(0.3, update_ui)


# ---------------- START UDP LISTENER ----------------

started = False

def start_background_tasks():
    global started
    if not started:
        started = True
        asyncio.create_task(udp_listener())

ui.timer(0.1, start_background_tasks)


ui.run(host='0.0.0.0', port=8080, reload=False)
