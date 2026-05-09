from nicegui import ui
import socket
import json
import time
import threading
import math

from utils import rssi_to_distance, trilaterate


UDP_IP = "0.0.0.0"
UDP_PORT = 5005

RSSI_0 = -45
PATH_LOSS_N = 2.3
ESP_TIMEOUT = 8
TARGET_TIMEOUT = 5

selected_mac = None
system_active = False

esp_geo_positions = {
    "ESP_1": (54.905028, 23.966833),
    "ESP_2": (54.905028, 23.966953),
    "ESP_3": (54.905100, 23.966833),
}

esp_positions = {
    "ESP_1": (0.0, 0.0),
    "ESP_2": (8.0, 0.0),
    "ESP_3": (0.0, 8.0),
}

rssi_data = {}
esp_last_seen = {}
current_position = {"x": None, "y": None}

data_lock = threading.Lock()
map_inited = False
logs = []


def xy_to_latlon(x, y, lat0, lon0):
    dlat = y / 111111.0
    dlon = x / (111111.0 * math.cos(math.radians(lat0)))
    return lat0 + dlat, lon0 + dlon


def normalize_esp_id(esp_id):
    esp_id = str(esp_id).strip()

    if esp_id in ("1", "ESP1", "esp1", "ID_1", "ESP_1"):
        return "ESP_1"
    if esp_id in ("2", "ESP2", "esp2", "ID_2", "ESP_2"):
        return "ESP_2"
    if esp_id in ("3", "ESP3", "esp3", "ID_3", "ESP_3"):
        return "ESP_3"

    return esp_id


def udp_listener_thread():
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind((UDP_IP, UDP_PORT))

    print("=== UDP LISTENER STARTED ===")
    print(f"Listening on UDP port {UDP_PORT}")

    while True:
        data, addr = sock.recvfrom(4096)

        try:
            msg = json.loads(data.decode())
        except Exception:
            print("Invalid JSON received")
            continue

        if isinstance(msg, dict):
            msg = [msg]

        if not msg:
            continue

        raw_esp_id = msg[0].get("esp_id", msg[0].get("id", None))
        if raw_esp_id is None:
            print("Invalid packet, missing esp_id:", msg)
            continue

        esp_id = normalize_esp_id(raw_esp_id)

        with data_lock:
            esp_last_seen[esp_id] = time.time()

            if esp_id not in rssi_data:
                rssi_data[esp_id] = {}

            for entry in msg:
                mac = entry.get("mac", "").lower()
                rssi = entry.get("rssi", None)

                if mac and rssi is not None:
                    rssi_data[esp_id][mac] = {
                        "rssi": int(rssi),
                        "time": time.time(),
                    }


def add_log(text: str):
    timestamp = time.strftime("%H:%M:%S")
    logs.insert(0, f"[{timestamp}] {text}")

    if len(logs) > 14:
        logs.pop()

    log_container.clear()
    with log_container:
        for item in logs:
            ui.label(item).classes("text-sm")


def start_system():
    global selected_mac, system_active

    active_esp = 0
    now = time.time()

    with data_lock:
        for esp_id in esp_positions.keys():
            last_seen = esp_last_seen.get(esp_id)
            if last_seen and now - last_seen < ESP_TIMEOUT:
                active_esp += 1

    if active_esp < 3:
        add_log("Nepakanka ESP mazgų trilateracijai (reikia bent 3)")
        return

    mac = mac_input.value.strip().lower()

    if not mac:
        add_log("MAC adresas neįvestas")
        return

    selected_mac = mac
    system_active = True

    current_position["x"] = None
    current_position["y"] = None

    selected_mac_label.set_text(f"MAC: {selected_mac.upper()}")
    status_label.set_text("STATUS: ACTIVE")
    status_label.classes(replace="text-sm font-bold text-green-400")

    add_log(f"Pasirinktas MAC: {selected_mac.upper()}")
    add_log("Sistema paleista")


def stop_system():
    global system_active

    system_active = False
    current_position["x"] = None
    current_position["y"] = None

    status_label.set_text("STATUS: STOPPED")
    status_label.classes(replace="text-sm font-bold text-red-400")

    x_label.set_text("X: -")
    y_label.set_text("Y: -")
    lat_label.set_text("Lat: -")
    lon_label.set_text("Lon: -")

    for esp_id in esp_positions.keys():
        rssi_labels[esp_id].set_text(f"{esp_id}: nėra duomenų")

    ui.run_javascript("""
        if (window.obj_marker && window.leaflet_map) {
            window.leaflet_map.removeLayer(window.obj_marker);
            window.obj_marker = null;
        }
    """)

    add_log("Sistema sustabdyta")


def update_esp_statuses():
    now = time.time()

    with data_lock:
        last_seen_copy = dict(esp_last_seen)

    for esp_id in esp_positions.keys():
        last_seen = last_seen_copy.get(esp_id)

        if last_seen and now - last_seen < ESP_TIMEOUT:
            esp_status_labels[esp_id].set_text(f"{esp_id}: ACTIVE")
            esp_status_labels[esp_id].classes(replace="text-green-400")
        else:
            esp_status_labels[esp_id].set_text(f"{esp_id}: OFFLINE")
            esp_status_labels[esp_id].classes(replace="text-red-400")


def get_selected_rssi():
    if not selected_mac:
        return {}

    result = {}

    with data_lock:
        for esp_id in esp_positions.keys():
            if selected_mac in rssi_data.get(esp_id, {}):
                result[esp_id] = rssi_data[esp_id][selected_mac]["rssi"]

    return result


def calculate_position(rssi_values):
    if len(rssi_values) < 3:
        return None

    try:
        r1 = rssi_to_distance(RSSI_0, rssi_values["ESP_1"], PATH_LOSS_N)
        r2 = rssi_to_distance(RSSI_0, rssi_values["ESP_2"], PATH_LOSS_N)
        r3 = rssi_to_distance(RSSI_0, rssi_values["ESP_3"], PATH_LOSS_N)

        x, y = trilaterate(
            esp_positions["ESP_1"],
            esp_positions["ESP_2"],
            esp_positions["ESP_3"],
            r1, r2, r3,
        )

        x = max(-5, min(15, x))
        y = max(-5, min(15, y))

        return x, y

    except Exception as e:
        print("Trilateration error:", e)
        return None


def update_object_marker(lat, lon):
    ui.run_javascript(f"""
        if (!window.leaflet_map || typeof L === 'undefined') {{
            return;
        }}

        if (!window.obj_marker) {{
            window.obj_marker = L.circleMarker([{lat}, {lon}], {{
                radius: 12,
                color: 'white',
                fillColor: '#3b82f6',
                fillOpacity: 1,
                weight: 3
            }}).addTo(window.leaflet_map).bindPopup('Objektas');
        }} else {{
            window.obj_marker.setLatLng([{lat}, {lon}]);
        }}
    """)


def update_dashboard():
    if not system_active or not selected_mac:
        return

    selected_rssi = get_selected_rssi()

    for esp_id in esp_positions.keys():
        value = selected_rssi.get(esp_id)

        if value is None:
            rssi_labels[esp_id].set_text(f"{esp_id}: nėra duomenų")
        else:
            rssi_labels[esp_id].set_text(f"{esp_id}: {value} dBm")

    now = time.time()

    with data_lock:
        for esp_id in esp_positions.keys():
            last_seen = esp_last_seen.get(esp_id)
            if not last_seen or now - last_seen > ESP_TIMEOUT:
                add_log(f"{esp_id} neatsako. Trilateracija sustabdyta.")
                stop_system()
                return

    last_seen_times = []
    with data_lock:
        for esp_id in esp_positions.keys():
            entry = rssi_data.get(esp_id, {}).get(selected_mac)
            if entry:
                last_seen_times.append(entry["time"])

    if last_seen_times and now - max(last_seen_times) > TARGET_TIMEOUT:
        add_log("Lokalizuojamas įrenginys nebeaptinkamas")
        stop_system()
        return

    pos = calculate_position(selected_rssi)

    if pos:
        current_position["x"] = pos[0]
        current_position["y"] = pos[1]

        lat0, lon0 = esp_geo_positions["ESP_1"]
        lat, lon = xy_to_latlon(pos[0], pos[1], lat0, lon0)

        x_label.set_text(f"X: {pos[0]:.2f} m")
        y_label.set_text(f"Y: {pos[1]:.2f} m")
        lat_label.set_text(f"Lat: {lat:.6f}")
        lon_label.set_text(f"Lon: {lon:.6f}")

        update_object_marker(lat, lon)


def init_map():
    global map_inited

    if map_inited:
        return

    map_inited = True

    lat0, lon0 = esp_geo_positions["ESP_1"]

    ui.run_javascript(f"""
        if (typeof L === 'undefined') {{
            console.error('Leaflet not loaded');
        }} else {{
            window.leaflet_map = L.map('map', {{
                zoomControl: true,
                preferCanvas: true
            }}).setView([{lat0}, {lon0}], 21);

            L.tileLayer('https://{{s}}.tile.openstreetmap.org/{{z}}/{{x}}/{{y}}.png', {{
                maxNativeZoom: 19,
                maxZoom: 22,
                minZoom: 18,
                updateWhenIdle: false,
                updateWhenZooming: true,
                keepBuffer: 4
            }}).addTo(window.leaflet_map);

            window.esp_markers = {{}};
            window.obj_marker = null;

            const bounds = L.latLngBounds([
                [54.905028, 23.966833],
                [54.905028, 23.966953],
                [54.905100, 23.966833]
            ]);

            window.leaflet_map.fitBounds(bounds.pad(2.0));

            setTimeout(() => window.leaflet_map.invalidateSize(true), 300);
            setTimeout(() => window.leaflet_map.invalidateSize(true), 1000);
            setTimeout(() => window.leaflet_map.invalidateSize(true), 2000);
        }}
    """)


def refresh_map_size():
    ui.run_javascript("""
        if (window.leaflet_map) {
            window.leaflet_map.invalidateSize(true);
        }
    """)


def update_esp_markers():
    for esp_id, (lat, lon) in esp_geo_positions.items():
        ui.run_javascript(f"""
            if (window.leaflet_map && typeof L !== 'undefined') {{
                if (!window.esp_markers['{esp_id}']) {{
                    window.esp_markers['{esp_id}'] = L.circleMarker([{lat}, {lon}], {{
                        radius: 8,
                        color: 'white',
                        fillColor: '#22c55e',
                        fillOpacity: 1,
                        weight: 2
                    }}).addTo(window.leaflet_map).bindPopup('{esp_id}');
                }} else {{
                    window.esp_markers['{esp_id}'].setLatLng([{lat}, {lon}]);
                }}
            }}
        """)


ui.colors(primary="#3b82f6")

ui.add_head_html("""
<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css" />
<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>

<style>
body {
    background-color: #0f172a;
    color: #e2e8f0;
    font-family: Inter, sans-serif;
}
.card-clean {
    background-color: #1e293b;
    border: 1px solid #334155;
    padding: 12px;
    border-radius: 8px;
}
#map {
    width: 100%;
    height: 560px;
    background-color: #0f172a;
}
</style>
""")

with ui.row().classes("w-full h-screen p-4 gap-4"):

    with ui.column().classes("w-64 gap-4"):

        selected_mac_label = ui.label("MAC: nepasirinktas").classes("text-sm font-bold text-blue-400")
        status_label = ui.label("STATUS: STOPPED").classes("text-sm font-bold text-red-400")

        with ui.element("div").classes("card-clean w-full"):
            ui.label("Mazgų būsena").classes("font-bold text-lg mb-1")

            esp_status_labels = {}
            for esp_id in esp_positions.keys():
                esp_status_labels[esp_id] = ui.label(f"{esp_id}: OFFLINE").classes("text-red-400")

        with ui.element("div").classes("card-clean w-full"):
            ui.label("RSSI duomenys").classes("font-bold text-lg mb-1")

            rssi_labels = {}
            for esp_id in esp_positions.keys():
                rssi_labels[esp_id] = ui.label(f"{esp_id}: nėra duomenų")

            ui.separator().classes("my-2")

            ui.label("Apskaičiuota pozicija").classes("font-bold text-lg")
            x_label = ui.label("X: -")
            y_label = ui.label("Y: -")
            lat_label = ui.label("Lat: -")
            lon_label = ui.label("Lon: -")

    with ui.column().classes("flex-1"):

        ui.label("GPS žemėlapis").classes("text-xl font-bold mb-2 text-blue-300")

        map_container = ui.element("div").props("id=map").classes(
            "w-full h-[560px] rounded-md border border-slate-600"
        )

        with ui.row().classes("w-full justify-center gap-6 mt-4"):
            start_button = ui.button("START", on_click=start_system).classes("bg-green-600 px-10 text-white")
            stop_button = ui.button("STOP", on_click=stop_system).classes("bg-red-600 px-10 text-white")

            def update_buttons():
                if system_active:
                    start_button.disable()
                    stop_button.enable()
                else:
                    start_button.enable()
                    stop_button.disable()

            ui.timer(0.2, update_buttons)

    with ui.column().classes("w-80 gap-4"):

        with ui.element("div").classes("card-clean w-full"):
            ui.label("Pasirinktas objektas").classes("font-bold text-lg mb-1")
            mac_input = ui.input("MAC adresas", placeholder="AA:BB:CC:DD:EE:FF").classes("w-full")

        with ui.element("div").classes("card-clean w-full h-[500px]"):
            ui.label("Įvykiai").classes("font-bold text-lg mb-1")
            log_container = ui.column().classes("gap-1")


listener_started = False


def start_background_tasks():
    global listener_started

    if not listener_started:
        listener_started = True

        thread = threading.Thread(target=udp_listener_thread, daemon=True)
        thread.start()

        add_log(f"UDP listener paleistas: {UDP_PORT}")


ui.timer(0.1, start_background_tasks, once=True)
ui.timer(1.0, init_map, once=True)
ui.timer(1.5, update_esp_markers)
ui.timer(3.0, refresh_map_size)
ui.timer(0.5, update_esp_statuses)
ui.timer(0.5, update_dashboard)

ui.run(host="0.0.0.0", port=8080, reload=False)