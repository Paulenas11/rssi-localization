from nicegui import ui
import socket
import json
import time
import threading
import math
from pathlib import Path

from utils import rssi_to_distance, trilaterate


UDP_IP = "0.0.0.0"
UDP_PORT = 5005

RSSI_0 = -60
PATH_LOSS_N = 2
ESP_TIMEOUT = 20
TARGET_TIMEOUT = 20
SETTINGS_FILE = Path(__file__).with_name("settings.json")

selected_mac = None
system_active = False

esp_geo_positions = {
    "ESP_1": (54.90491, 23.966833),
    "ESP_2": (54.90491, 23.966958),
    "ESP_3": (54.904982, 23.966833),
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
logs = []


def load_settings():
    """
    Loads sensor node positions from the settings file.
    Keeps default values if the settings file is missing or invalid.
    """
    if not SETTINGS_FILE.exists():
        return

    try:
        data = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
    except Exception as e:
        print("Settings load error:", e)
        return

    for esp_id, value in data.get("esp_positions", {}).items():
        if esp_id not in esp_positions:
            continue

        try:
            esp_positions[esp_id] = (float(value["x"]), float(value["y"]))
        except (KeyError, TypeError, ValueError):
            continue

    for esp_id, value in data.get("esp_geo_positions", {}).items():
        if esp_id not in esp_geo_positions:
            continue

        try:
            esp_geo_positions[esp_id] = (float(value["lat"]), float(value["lon"]))
        except (KeyError, TypeError, ValueError):
            continue


def save_settings_to_file():
    """
    Saves sensor node local and geographic coordinates to the settings file.
    """
    data = {
        "esp_positions": {
            esp_id: {"x": position[0], "y": position[1]}
            for esp_id, position in esp_positions.items()
        },
        "esp_geo_positions": {
            esp_id: {"lat": position[0], "lon": position[1]}
            for esp_id, position in esp_geo_positions.items()
        },
    }

    SETTINGS_FILE.write_text(
        json.dumps(data, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


load_settings()


def xy_to_latlon(x, y, lat0, lon0):
    """
    x, y - local coordinates in meters
    lat0, lon0 - geographic origin point
    Returns latitude and longitude coordinates
    """
    dlat = y / 111111.0
    dlon = x / (111111.0 * math.cos(math.radians(lat0)))
    return lat0 + dlat, lon0 + dlon


def latlon_to_xy(lat, lon, lat0, lon0):
    """
    lat, lon - geographic coordinates
    lat0, lon0 - geographic origin point
    Returns local x and y coordinates in meters
    """
    y = (lat - lat0) * 111111.0
    x = (lon - lon0) * 111111.0 * math.cos(math.radians(lat0))
    return x, y


def update_xy_from_geo_positions():
    """
    Updates local sensor node coordinates from their geographic coordinates.
    ESP_1 is used as the local coordinate origin.
    """
    lat0, lon0 = esp_geo_positions["ESP_1"]

    for esp_id, (lat, lon) in esp_geo_positions.items():
        esp_positions[esp_id] = latlon_to_xy(lat, lon, lat0, lon0)


def normalize_esp_id(esp_id):
    """
    Converts different ESP ID formats to the internal ESP_X names.
    """
    esp_id = str(esp_id).strip().upper()

    if esp_id.startswith("ESP_"):
        return esp_id

    if esp_id.startswith("ESP"):
        number = esp_id.replace("ESP", "")
        if number.isdigit():
            return f"ESP_{number}"

    if esp_id.startswith("ID_"):
        number = esp_id.replace("ID_", "")
        if number.isdigit():
            return f"ESP_{number}"

    if esp_id.isdigit():
        return f"ESP_{esp_id}"

    return esp_id


def udp_listener_thread():
    """
    Listens for UDP packets from sensor nodes.
    Decodes JSON scan results and stores RSSI data by ESP node and MAC address.
    """
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
                ssid = entry.get("ssid", "")
                rssi = entry.get("rssi", None)

                if mac and rssi is not None:
                    rssi_data[esp_id][mac] = {
                        "rssi": int(rssi),
                        "ssid": ssid,
                        "time": time.time(),
                    }


def add_log(text: str):
    """
    Adds a timestamped message to the event log shown in the user interface.
    """
    timestamp = time.strftime("%H:%M:%S")
    with data_lock:
        logs.insert(0, f"[{timestamp}] {text}")

        if len(logs) > 14:
            logs.pop()


def auto_select_mac():
    """
    Finds the best visible MAC address based on the number of detecting ESP nodes,
    average RSSI strength and latest received packet time.
    """
    now = time.time()
    candidates = {}

    with data_lock:
        for esp_id in esp_positions.keys():
            for mac, entry in rssi_data.get(esp_id, {}).items():
                if now - entry["time"] > TARGET_TIMEOUT:
                    continue

                if mac not in candidates:
                    candidates[mac] = {
                        "esp_count": 0,
                        "rssi_sum": 0,
                        "latest_time": 0,
                    }

                candidates[mac]["esp_count"] += 1
                candidates[mac]["rssi_sum"] += entry["rssi"]
                candidates[mac]["latest_time"] = max(candidates[mac]["latest_time"], entry["time"])

    if not candidates:
        return None

    seen_by_all = {
        mac: stats
        for mac, stats in candidates.items()
        if stats["esp_count"] >= 3
    }
    source = seen_by_all or candidates

    best_mac, _ = max(
        source.items(),
        key=lambda item: (
            item[1]["esp_count"],
            item[1]["rssi_sum"] / item[1]["esp_count"],
            item[1]["latest_time"],
        ),
    )

    return best_mac


def get_mac_options():
    """
    Builds the selectable device list for the settings dialog.
    Returns MAC addresses with SSID, node count and average RSSI information.
    """
    now = time.time()
    candidates = {}

    with data_lock:
        for esp_id in esp_positions.keys():
            for mac, entry in rssi_data.get(esp_id, {}).items():
                if now - entry["time"] > TARGET_TIMEOUT:
                    continue

                if mac not in candidates:
                    candidates[mac] = {
                        "esp_count": 0,
                        "rssi_sum": 0,
                        "ssids": set(),
                    }

                candidates[mac]["esp_count"] += 1
                candidates[mac]["rssi_sum"] += entry["rssi"]

                if entry.get("ssid"):
                    candidates[mac]["ssids"].add(entry["ssid"])

    options = {}
    for mac, stats in sorted(
        candidates.items(),
        key=lambda item: (-item[1]["esp_count"], item[0]),
    ):
        avg_rssi = stats["rssi_sum"] / stats["esp_count"]
        ssid_text = ", ".join(sorted(stats["ssids"])) or "be SSID"
        options[mac] = (
            f"{ssid_text} | {mac.upper()} | "
            f"{stats['esp_count']}/{len(esp_positions)} ESP | {avg_rssi:.0f} dBm"
        )

    return options


def select_mac_value(mac):
    """
    Stores the selected MAC address for localization.
    """
    global selected_mac

    selected_mac = mac.lower() if mac else None


def start_system(mac=None):
    """
    Starts the localization process if enough ESP nodes are active
    and the selected device is visible from at least three nodes.
    """
    global selected_mac, system_active

    active_esp = 0
    now = time.time()

    with data_lock:
        for esp_id in esp_positions.keys():
            last_seen = esp_last_seen.get(esp_id)
            if last_seen and now - last_seen < ESP_TIMEOUT:
                active_esp += 1

    if active_esp < 3:
        add_log("Not enough active nodes for trilateration")
        return

    mac = (mac or selected_mac)

    if not mac:
        add_log("No device selected in settings")
        return

    mac = mac.lower()

    if mac not in get_mac_options():
        add_log("Selected device is not currently detected")
        return

    selected_mac = mac

    if len(get_valid_rssi_for_mac(selected_mac)) < 3:
        add_log("Selected MAC not visible from at least 3 nodes")
        return

    system_active = True

    current_position["x"] = None
    current_position["y"] = None

    add_log(f"MAC: {selected_mac.upper()}")
    add_log("System running")


def stop_system(add_message=True):
    """
    Stops the localization process and clears the last calculated position.
    """
    global system_active

    system_active = False
    current_position["x"] = None
    current_position["y"] = None

    if add_message:
        add_log("System stopped")


def get_rssi_for_mac(mac):
    """
    mac - selected device MAC address
    Returns the latest RSSI values for this MAC from each ESP node
    """
    if not mac:
        return {}

    mac = mac.lower()
    result = {}

    with data_lock:
        for esp_id in esp_positions.keys():
            if mac in rssi_data.get(esp_id, {}):
                result[esp_id] = rssi_data[esp_id][mac]["rssi"]

    return result


def get_valid_rssi_for_mac(mac):
    """
    mac - selected device MAC address
    Returns fresh RSSI values from active ESP nodes for this MAC
    """
    if not mac:
        return {}

    mac = mac.lower()
    now = time.time()
    result = {}

    with data_lock:
        for esp_id in esp_positions.keys():
            last_seen = esp_last_seen.get(esp_id)
            entry = rssi_data.get(esp_id, {}).get(mac)

            if (
                last_seen
                and now - last_seen <= ESP_TIMEOUT
                and entry
                and now - entry["time"] <= TARGET_TIMEOUT
            ):
                result[esp_id] = entry["rssi"]

    return result


def get_selected_rssi():
    """
    Returns the latest RSSI values for the currently selected MAC address.
    """
    return get_rssi_for_mac(selected_mac)


def calculate_position(rssi_values):
    """
    rssi_values - RSSI values from available ESP nodes
    Selects 3 strongest nodes and calculates local x, y position.
    """
    available_nodes = [
        (esp_id, rssi)
        for esp_id, rssi in rssi_values.items()
        if esp_id in esp_positions
    ]

    visible_nodes = get_valid_rssi_for_mac(selected_mac)
    if len(visible_nodes) < 3:
        add_log(f"Selected MAC visible from {len(visible_nodes)}/3 nodes: {', '.join(visible_nodes.keys())}")

    selected_nodes = sorted(
        available_nodes,
        key=lambda item: item[1],
        reverse=True,
    )[:3]

    try:
        points = [esp_positions[esp_id] for esp_id, _ in selected_nodes]
        distances = [
            rssi_to_distance(RSSI_0, rssi, PATH_LOSS_N)
            for _, rssi in selected_nodes
        ]

        x, y = trilaterate(
            points[0],
            points[1],
            points[2],
            distances[0],
            distances[1],
            distances[2],
        )

        x = max(-5, min(15, x))
        y = max(-5, min(15, y))

        return x, y

    except Exception as e:
        print("Trilateration error:", e)
        return None


def update_object_marker(lat, lon):
    """
    Updates or creates the localized object marker on the Leaflet map.
    """
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
            }}).addTo(window.leaflet_map).bindPopup('Object');
        }} else {{
            window.obj_marker.setLatLng([{lat}, {lon}]);
        }}
    """)


def update_position_state():
    """
    Validates active nodes and selected device data.
    Calculates and returns the current object position in local and geographic coordinates.
    """
    if not system_active or not selected_mac:
        return None

    valid_rssi = get_valid_rssi_for_mac(selected_mac)

    if len(valid_rssi) < 3:
        add_log("Selected device is not visible from at least 3 active nodes.")
        stop_system(add_message=False)
        return None

    pos = calculate_position(valid_rssi)

    if pos:
        current_position["x"] = pos[0]
        current_position["y"] = pos[1]

        lat0, lon0 = esp_geo_positions["ESP_1"]
        lat, lon = xy_to_latlon(pos[0], pos[1], lat0, lon0)

        return pos[0], pos[1], lat, lon

    return None


def init_map():
    """
    Initializes the Leaflet map and sets its view around the sensor node positions.
    """
    lat0, lon0 = esp_geo_positions["ESP_1"]
    bounds = json.dumps([
        [lat, lon]
        for lat, lon in esp_geo_positions.values()
    ])

    ui.run_javascript(f"""
        if (typeof L === 'undefined') {{
            console.error('Leaflet not loaded');
        }} else if (window.leaflet_map) {{
            window.leaflet_map.invalidateSize(true);
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

            const bounds = L.latLngBounds({bounds});

            window.leaflet_map.fitBounds(bounds.pad(2.0));

            setTimeout(() => window.leaflet_map.invalidateSize(true), 300);
            setTimeout(() => window.leaflet_map.invalidateSize(true), 1000);
            setTimeout(() => window.leaflet_map.invalidateSize(true), 2000);
        }}
    """)


def refresh_map_size():
    """
    Forces Leaflet to recalculate the map size after UI layout changes.
    """
    ui.run_javascript("""
        if (window.leaflet_map) {
            window.leaflet_map.invalidateSize(true);
        }
    """)


def fit_map_to_esp_positions():
    """
    Fits the map view to include all configured ESP node positions.
    """
    bounds = json.dumps([
        [lat, lon]
        for lat, lon in esp_geo_positions.values()
    ])

    ui.run_javascript(f"""
        if (window.leaflet_map && typeof L !== 'undefined') {{
            const bounds = L.latLngBounds({bounds});
            window.leaflet_map.fitBounds(bounds.pad(2.0));
            setTimeout(() => window.leaflet_map.invalidateSize(true), 100);
        }}
    """)


def update_esp_markers():
    """
    Updates or creates ESP node markers on the Leaflet map.
    """
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
                    }})
                    .addTo(window.leaflet_map)
                    .bindPopup('{esp_id}')
                    .bindTooltip('{esp_id}', {{
                        permanent: true,
                        direction: 'top',
                        offset: [0, -9],
                        className: 'esp-tooltip'
                    }});
                }} else {{
                    window.esp_markers['{esp_id}'].setLatLng([{lat}, {lon}]);
                }}
            }}
        """)


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
.q-field__native,
.q-field__input,
.q-field__label,
.q-item__label {
    color: #e2e8f0 !important;
}
.q-menu {
    background-color: #1e293b !important;
    color: #e2e8f0 !important;
}
#map {
    width: 100%;
    height: 560px;
    background-color: #0f172a;
}
.leaflet-tooltip.esp-tooltip {
    background: transparent;
    border: 0;
    box-shadow: none;
    color: #e2e8f0;
    font-size: 10px;
    font-weight: 700;
    line-height: 1;
    padding: 0;
    text-shadow: 0 1px 2px #020617, 0 0 2px #020617;
}
.leaflet-tooltip.esp-tooltip::before {
    display: none;
}
</style>
""", shared=True)


listener_started = False


def start_background_tasks():
    """
    Starts background tasks required by the application.
    The UDP listener is started only once.
    """
    global listener_started

    if not listener_started:
        listener_started = True

        thread = threading.Thread(target=udp_listener_thread, daemon=True)
        thread.start()

        add_log(f"UDP listener started: {UDP_PORT}")


start_background_tasks()


@ui.page("/")
def index():
    with ui.row().classes("w-full h-screen p-4 gap-4"):

        with ui.column().classes("w-64 gap-4"):

            selected_mac_label = ui.label("Device MAC: not selected").classes("text-sm font-bold text-blue-400")
            status_label = ui.label("STATUS: STOPPED").classes("text-sm font-bold text-red-400")

            settings_button = ui.button("SETTINGS").classes("bg-slate-600 w-full text-white")

            with ui.element("div").classes("card-clean w-full"):
                ui.label("Node status").classes("font-bold text-lg mb-1")

                esp_status_labels = {}
                for esp_id in esp_positions.keys():
                    esp_status_labels[esp_id] = ui.label(f"{esp_id}: OFFLINE").classes("text-red-400")

            with ui.element("div").classes("card-clean w-full"):
                ui.label("RSSI data").classes("font-bold text-lg mb-1")

                rssi_labels = {}
                for esp_id in esp_positions.keys():
                    rssi_labels[esp_id] = ui.label(f"{esp_id}: no data")

                ui.separator().classes("my-2")

                ui.label("Calculated position").classes("font-bold text-lg")
                x_label = ui.label("X: -")
                y_label = ui.label("Y: -")
                lat_label = ui.label("Lat: -")
                lon_label = ui.label("Lon: -")

        with ui.column().classes("flex-1"):

            ui.label("Positioning System Map").classes("text-xl font-bold mb-2 text-blue-300")

            ui.element("div").props("id=map").classes(
                "w-full h-[560px] rounded-md border border-slate-600"
            )

            with ui.row().classes("w-full justify-center gap-6 mt-4"):
                start_button = ui.button("START").classes("bg-green-600 px-10 text-white")
                stop_button = ui.button("STOP").classes("bg-red-600 px-10 text-white")

        with ui.column().classes("w-80 gap-4"):

            with ui.element("div").classes("card-clean w-full h-[500px]"):
                ui.label("Events").classes("font-bold text-lg mb-1")
                log_container = ui.column().classes("gap-1")

    with ui.dialog() as settings_dialog:
        with ui.card().classes("card-clean w-[560px] max-w-[90vw] text-slate-100"):
            ui.label("Settings").classes("font-bold text-xl text-blue-300")

            settings_mac_select = ui.select(
                options={},
                label="Select a device",
            ).props("dark dense outlined").classes("w-full")

            ui.separator().classes("my-2")
            ui.label("Sensor node positions").classes("font-bold text-lg")

            position_inputs = {}
            for esp_id, (x, y) in esp_positions.items():
                lat, lon = esp_geo_positions[esp_id]
                with ui.row().classes("w-full gap-2 items-center"):
                    ui.label(esp_id).classes("w-16 font-bold")
                    lat_input = ui.number(label="Lat", value=lat, step=0.000001, precision=6).props("dark dense outlined").classes("flex-1")
                    lon_input = ui.number(label="Lon", value=lon, step=0.000001, precision=6).props("dark dense outlined").classes("flex-1")
                with ui.row().classes("w-full gap-2 items-center"):
                    ui.label("").classes("w-16")
                    x_input = ui.number(label="X", value=x, step=0.1, precision=2).props("dark dense outlined suffix=m").classes("flex-1")
                    y_input = ui.number(label="Y", value=y, step=0.1, precision=2).props("dark dense outlined suffix=m").classes("flex-1")
                    position_inputs[esp_id] = (lat_input, lon_input, x_input, y_input)

            for lat_input, lon_input, _, _ in position_inputs.values():
                lat_input.on_value_change(lambda _: update_settings_xy_from_latlon())
                lon_input.on_value_change(lambda _: update_settings_xy_from_latlon())

            with ui.row().classes("w-full justify-end gap-2 mt-3"):
                cancel_settings_button = ui.button("CANCEL").classes("bg-slate-600 text-white")
                save_settings_button = ui.button("SAVE").classes("bg-blue-600 text-white")

    def update_selected_label():
        if system_active and selected_mac:
            selected_mac_label.set_text(f"MAC: {selected_mac.upper()}")
        else:
            selected_mac_label.set_text("Device MAC: not selected")

    def remove_object_marker():
        ui.run_javascript("""
            if (window.obj_marker && window.leaflet_map) {
                window.leaflet_map.removeLayer(window.obj_marker);
                window.obj_marker = null;
            }
        """)

    def handle_start():
        start_system(selected_mac)
        update_selected_label()
        update_page_state()

    def handle_stop():
        stop_system()
        remove_object_marker()
        update_page_state()

    def close_settings():
        settings_mac_select.value = None
        settings_dialog.close()

    def update_settings_xy_from_latlon():
        lat0 = float(position_inputs["ESP_1"][0].value)
        lon0 = float(position_inputs["ESP_1"][1].value)

        for esp_id, (lat_input, lon_input, x_input, y_input) in position_inputs.items():
            x, y = latlon_to_xy(
                float(lat_input.value),
                float(lon_input.value),
                lat0,
                lon0,
            )
            x_input.value = round(x, 2)
            y_input.value = round(y, 2)

    def open_settings():
        options = get_mac_options()
        settings_mac_select.options = options
        settings_mac_select.value = None
        settings_mac_select.update()

        for esp_id, (lat_input, lon_input, x_input, y_input) in position_inputs.items():
            lat, lon = esp_geo_positions[esp_id]
            x, y = esp_positions[esp_id]
            lat_input.value = lat
            lon_input.value = lon
            x_input.value = x
            y_input.value = y

        settings_dialog.open()

    def save_settings():
        select_mac_value(settings_mac_select.value)

        for esp_id, (lat_input, lon_input, x_input, y_input) in position_inputs.items():
            esp_geo_positions[esp_id] = (float(lat_input.value), float(lon_input.value))
            esp_positions[esp_id] = (float(x_input.value), float(y_input.value))

        save_settings_to_file()

        add_log("Settings updated")
        update_esp_markers()
        fit_map_to_esp_positions()
        update_selected_label()
        update_page_state()
        settings_dialog.close()

    settings_button.on_click(open_settings)
    start_button.on_click(handle_start)
    stop_button.on_click(handle_stop)
    cancel_settings_button.on_click(close_settings)
    save_settings_button.on_click(save_settings)

    def update_buttons():
        if system_active:
            start_button.disable()
            stop_button.enable()
        else:
            start_button.enable()
            stop_button.disable()

    def update_mac_select():
        if system_active:
            return

        options = get_mac_options()
        current_value = settings_mac_select.value

        settings_mac_select.options = options

        if current_value in options:
            settings_mac_select.value = current_value
        else:
            settings_mac_select.value = None

        settings_mac_select.update()

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

    def update_logs():
        with data_lock:
            log_items = list(logs)

        log_container.clear()
        with log_container:
            for item in log_items:
                ui.label(item).classes("text-sm")

    def update_page_state():
        update_selected_label()

        if system_active:
            status_label.set_text("STATUS: ACTIVE")
            status_label.classes(replace="text-sm font-bold text-green-400")
        else:
            status_label.set_text("STATUS: STOPPED")
            status_label.classes(replace="text-sm font-bold text-red-400")

        selected_rssi = get_selected_rssi() if system_active else {}
        for esp_id in esp_positions.keys():
            value = selected_rssi.get(esp_id)

            if value is None:
                rssi_labels[esp_id].set_text(f"{esp_id}: -")
            else:
                rssi_labels[esp_id].set_text(f"{esp_id}: {value} dBm")

        position = update_position_state()

        if position:
            x, y, lat, lon = position
            x_label.set_text(f"X: {x:.2f} m")
            y_label.set_text(f"Y: {y:.2f} m")
            lat_label.set_text(f"Lat: {lat:.6f}")
            lon_label.set_text(f"Lon: {lon:.6f}")
            update_object_marker(lat, lon)
        elif not system_active:
            x_label.set_text("X: -")
            y_label.set_text("Y: -")
            lat_label.set_text("Lat: -")
            lon_label.set_text("Lon: -")
            remove_object_marker()

        update_buttons()
        update_logs()

    ui.timer(0.2, update_buttons)
    ui.timer(1.0, init_map)
    ui.timer(1.5, update_esp_markers)
    ui.timer(3.0, refresh_map_size)
    ui.timer(0.5, update_esp_statuses)
    ui.timer(0.5, update_mac_select)
    ui.timer(0.5, update_page_state)

ui.run(host="0.0.0.0", port=8080, reload=False, show=False)
