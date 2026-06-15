from nicegui import ui
import json
import socket
import time
from pathlib import Path


UDP_IP = "0.0.0.0"
SETTINGS_FILE = Path(__file__).with_name("settings.json")
STATE_FILE = Path(__file__).with_name("shelter_state.json")


def load_settings():
    if not SETTINGS_FILE.exists():
        return {}

    try:
        settings = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}

    return settings.get("shelter", {})


def save_shelter_settings(esp_markers, capacity=None, device_timeout=None, min_rssi=None, min_nodes=None):
    global SHELTER_SETTINGS, SHELTER_CAPACITY, DEVICE_TIMEOUT
    global MIN_RSSI_IN_SHELTER, MIN_NODES_IN_SHELTER, ESP_MARKERS

    try:
        settings = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        settings = {}

    shelter_settings = settings.setdefault("shelter", {})
    capacity = SHELTER_CAPACITY if capacity is None else capacity
    device_timeout = DEVICE_TIMEOUT if device_timeout is None else device_timeout
    min_rssi = MIN_RSSI_IN_SHELTER if min_rssi is None else min_rssi
    min_nodes = MIN_NODES_IN_SHELTER if min_nodes is None else min_nodes

    shelter_settings["capacity"] = int(capacity)
    shelter_settings["device_timeout"] = float(device_timeout)
    shelter_settings["min_rssi_in_shelter"] = int(min_rssi)
    shelter_settings["min_nodes_in_shelter"] = int(min_nodes)
    shelter_settings["esp_markers"] = esp_markers

    SETTINGS_FILE.write_text(
        json.dumps(settings, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    SHELTER_SETTINGS = shelter_settings
    SHELTER_CAPACITY = int(capacity)
    DEVICE_TIMEOUT = float(device_timeout)
    MIN_RSSI_IN_SHELTER = int(min_rssi)
    MIN_NODES_IN_SHELTER = int(min_nodes)
    ESP_MARKERS = esp_markers
    add_event("Settings updated")


SHELTER_SETTINGS = load_settings()
SHELTER_NAME = SHELTER_SETTINGS.get("name", "Shelter")
SHELTER_CAPACITY = int(SHELTER_SETTINGS.get("capacity", 10))
UDP_PORT = int(SHELTER_SETTINGS.get("udp_port", 5005))
DEVICE_TIMEOUT = float(SHELTER_SETTINGS.get("device_timeout", 15))
MIN_RSSI_IN_SHELTER = int(SHELTER_SETTINGS.get("min_rssi_in_shelter", -75))
MIN_NODES_IN_SHELTER = int(SHELTER_SETTINGS.get("min_nodes_in_shelter", 1))
IGNORED_MACS = {str(mac).upper() for mac in SHELTER_SETTINGS.get("ignored_macs", [])}
ESP_IDS = SHELTER_SETTINGS.get("esp_ids", ["ESP_1", "ESP_2", "ESP_3", "ESP_4"])
FLOOR_PLAN = SHELTER_SETTINGS.get("floor_plan", {"width": 7.4, "height": 3.0})
ESP_MARKERS = SHELTER_SETTINGS.get("esp_markers", [])
SHELTER_SHAPE = {
    "left": 11,
    "top": 16,
    "width": 78,
    "height": 68,
}

devices = {}
esp_last_seen = {}
event_logs = []
previous_active_macs = set()
previous_active_esp_ids = set()
previous_occupancy = None
previous_state = None
event_state_initialized = False
udp_socket = None
udp_socket_error_shown = False


def normalize_esp_id(esp_id):
    esp_id = str(esp_id)

    if esp_id.startswith("ESP_"):
        return esp_id

    return f"ESP_{esp_id}"


def init_udp_socket():
    global udp_socket, udp_socket_error_shown

    if udp_socket is not None:
        return udp_socket

    try:
        udp_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        udp_socket.bind((UDP_IP, UDP_PORT))
        udp_socket.setblocking(False)
        print("=== Shelter UDP listener started ===")
        print(f"Listening on UDP port {UDP_PORT}")
    except OSError as error:
        udp_socket = None

        if not udp_socket_error_shown:
            print(f"UDP listener not started: {error}")
            print("This UI instance will read data from the shared state file.")
            udp_socket_error_shown = True

    return udp_socket


def add_event(message):
    timestamp = time.strftime("%H:%M:%S")
    event_logs.insert(0, f"[{timestamp}] {message}")
    del event_logs[20:]


def is_multicast_or_broadcast_mac(mac):
    parts = mac.split(":")

    if len(parts) != 6:
        return True

    try:
        first_byte = int(parts[0], 16)
    except ValueError:
        return True

    return mac == "FF:FF:FF:FF:FF:FF" or bool(first_byte & 1)


def get_status(rssi):
    if rssi >= MIN_RSSI_IN_SHELTER:
        return "IN_SHELTER"

    return "WEAK_SIGNAL"


def get_shelter_state(occupancy):
    if SHELTER_CAPACITY <= 0:
        return "UNKNOWN"

    fill_ratio = occupancy / SHELTER_CAPACITY

    if fill_ratio >= 1:
        return "FULL"

    if fill_ratio >= 0.8:
        return "ALMOST_FULL"

    if fill_ratio >= 0.5:
        return "FILLING"

    return "AVAILABLE"


def get_state_display(state):
    displays = {
        "AVAILABLE": "Available",
        "FILLING": "Filling",
        "ALMOST_FULL": "Almost full",
        "FULL": "Full",
        "UNKNOWN": "Unknown",
    }

    return displays.get(state, state.title())


def update_devices(packet):
    now = time.time()

    if isinstance(packet, dict):
        packet = [packet]

    print("Received packet:", packet)

    for item in packet:
        mac = str(item.get("mac", "")).upper()
        esp_id = normalize_esp_id(item.get("esp_id", "unknown"))

        if esp_id != "ESP_unknown":
            esp_last_seen[esp_id] = now

        if not mac or mac in IGNORED_MACS or is_multicast_or_broadcast_mac(mac):
            print("Ignored MAC:", mac)
            continue

        try:
            rssi = int(item.get("rssi"))
        except (TypeError, ValueError):
            print("Ignored invalid RSSI:", item)
            continue

        if mac not in devices:
            devices[mac] = {}

        devices[mac][esp_id] = {
            "esp_id": esp_id,
            "rssi": rssi,
            "packets": int(item.get("packets", 0)),
            "status": get_status(rssi),
            "last_seen": now,
        }
        print("Updated device:", mac, esp_id, "RSSI", rssi)

    save_state()


def save_state():
    state = {
        "devices": devices,
        "esp_last_seen": esp_last_seen,
    }

    STATE_FILE.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")


def load_state():
    global devices, esp_last_seen

    if not STATE_FILE.exists():
        return

    try:
        state = json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return

    devices = state.get("devices", {})
    esp_last_seen = state.get("esp_last_seen", {})


def get_active_devices():
    load_state()
    now = time.time()
    active_devices = []

    for mac, esp_data in devices.items():
        active_nodes = []

        for esp_id, data in esp_data.items():
            age = now - float(data.get("last_seen", 0))

            if age <= DEVICE_TIMEOUT:
                active_nodes.append((normalize_esp_id(esp_id), dict(data), age))

        if active_nodes:
            active_devices.append((mac, active_nodes))

    return active_devices


def get_active_esp_ids():
    load_state()
    now = time.time()
    active_ids = []

    for esp_id, last_seen in esp_last_seen.items():
        if now - float(last_seen) <= DEVICE_TIMEOUT:
            active_ids.append(normalize_esp_id(esp_id))

    return sorted(active_ids)


def is_device_inside(active_nodes):
    inside_nodes = sum(
        1 for _, data, _ in active_nodes
        if data.get("status") == "IN_SHELTER"
    )

    return inside_nodes >= MIN_NODES_IN_SHELTER


def calculate_occupancy(active_devices):
    return sum(1 for _, active_nodes in active_devices if is_device_inside(active_nodes))


def get_strongest_rssi(active_nodes):
    return max(int(data["rssi"]) for _, data, _ in active_nodes)


def poll_udp_packets():
    socket_instance = init_udp_socket()

    if socket_instance is None:
        return

    while True:
        try:
            data, _ = socket_instance.recvfrom(4096)
        except BlockingIOError:
            break

        try:
            packet = json.loads(data.decode())
        except json.JSONDecodeError:
            print("Invalid JSON received")
            continue

        update_devices(packet)


def update_event_log(active_devices, occupancy, state, active_esp_ids):
    global previous_active_macs, previous_active_esp_ids
    global previous_occupancy, previous_state, event_state_initialized

    active_macs = {mac for mac, _ in active_devices}
    active_esp_set = set(active_esp_ids)

    if not event_state_initialized:
        add_event("System monitoring started")
        event_state_initialized = True
    else:
        for mac in sorted(active_macs - previous_active_macs):
            add_event(f"Device detected: {mac}")

        for mac in sorted(previous_active_macs - active_macs):
            add_event(f"Device signal lost: {mac}")

        for esp_id in sorted(active_esp_set - previous_active_esp_ids):
            add_event(f"{esp_id} active")

        for esp_id in sorted(previous_active_esp_ids - active_esp_set):
            add_event(f"{esp_id} offline")

        if previous_occupancy != occupancy:
            add_event(f"Occupancy changed: {occupancy}/{SHELTER_CAPACITY}")

        if previous_state != state:
            add_event(f"Availability changed: {get_state_display(state)}")

    previous_active_macs = active_macs
    previous_active_esp_ids = active_esp_set
    previous_occupancy = occupancy
    previous_state = state


def get_marker_position_for_device(active_nodes):
    marker_positions = {
        str(marker.get("id", "")).replace("ESP_", ""): (
            *get_marker_position_percent(marker),
        )
        for marker in ESP_MARKERS
    }
    weighted_x = 0
    weighted_y = 0
    total_weight = 0

    for esp_id, data, _ in active_nodes:
        normalized_id = esp_id.replace("ESP_", "")
        position = marker_positions.get(normalized_id)

        if not position:
            continue

        weight = max(1, int(data.get("rssi", -100)) + 100)
        weighted_x += position[0] * weight
        weighted_y += position[1] * weight
        total_weight += weight

    if total_weight == 0:
        return 50, 50

    left = max(6, min(94, weighted_x / total_weight))
    top = max(8, min(92, weighted_y / total_weight))

    return left, top


def keep_outside_shelter(left, top):
    shelter_left = SHELTER_SHAPE["left"]
    shelter_top = SHELTER_SHAPE["top"]
    shelter_right = shelter_left + SHELTER_SHAPE["width"]
    shelter_bottom = shelter_top + SHELTER_SHAPE["height"]

    is_inside_visual_bounds = (
        shelter_left <= left <= shelter_right
        and shelter_top <= top <= shelter_bottom
    )

    if not is_inside_visual_bounds:
        return left, top

    margin = 7
    distances = {
        "left": abs(left - shelter_left),
        "right": abs(shelter_right - left),
        "top": abs(top - shelter_top),
        "bottom": abs(shelter_bottom - top),
    }
    nearest_side = min(distances, key=distances.get)

    if nearest_side == "left":
        left = shelter_left - margin
    elif nearest_side == "right":
        left = shelter_right + margin
    elif nearest_side == "top":
        top = shelter_top - margin
    else:
        top = shelter_bottom + margin

    return max(4, min(96, left)), max(6, min(94, top))


def get_marker_position_percent(marker):
    floor_width = float(FLOOR_PLAN.get("width", 7.4))
    floor_height = float(FLOOR_PLAN.get("height", 3.0))

    if "x_m" in marker and "y_m" in marker:
        left = (float(marker["x_m"]) / floor_width) * 100
        top = (float(marker["y_m"]) / floor_height) * 100
    else:
        left = float(marker.get("x", 50))
        top = float(marker.get("y", 50))

    return max(0, min(100, left)), max(0, min(100, top))


def build_zone_html(active_devices):
    markers = []

    for marker in ESP_MARKERS:
        esp_id = str(marker.get("id", "ESP"))
        left, top = get_marker_position_percent(marker)
        label = esp_id.replace("ESP_", "")
        markers.append(
            f"""
            <div class="marker esp" style="left:{left}%; top:{top}%;">
                <span>{label}</span>
            </div>
            """
        )

    for index, (mac, active_nodes) in enumerate(active_devices):
        left, top = get_marker_position_for_device(active_nodes)
        in_shelter = is_device_inside(active_nodes)

        if not in_shelter:
            left, top = keep_outside_shelter(left, top)

        marker_class = "device-in" if in_shelter else "device-out"
        label = index + 1
        markers.append(
            f"""
            <div class="marker {marker_class}" style="left:{left}%; top:{top}%;" title="{mac}">
                <span>{label}</span>
            </div>
            """
        )

    width = FLOOR_PLAN.get("width", 7.4)
    height = FLOOR_PLAN.get("height", 3.0)

    return f"""
    <div class="zone-panel">
        <div class="zone-header">
            <div>
                <div class="zone-title">Shelter area</div>
                <div class="zone-subtitle"> {width} m x {height} m</div>
            </div>
        </div>
        <div class="outer-zone">
            <div class="grid-background"></div>
            <div class="shelter-shape">
                <div class="wall wall-main"></div>
                <div class="wall wall-corridor"></div>
                <div class="entry-label">ENTRY</div>
            </div>
            {''.join(markers)}
        </div>
    </div>
    <style>
        .zone-panel {{
            width: 100%;
            background: #1e293b;
            border: 1px solid #334155;
            border-radius: 8px;
            padding: 12px;
        }}
        .zone-header {{
            display: flex;
            align-items: center;
            justify-content: space-between;
            margin-bottom: 10px;
        }}
        .zone-title {{
            color: #93c5fd;
            font-size: 20px;
            font-weight: 700;
        }}
        .zone-subtitle {{
            color: #94a3b8;
            font-size: 13px;
        }}
        .outer-zone {{
            position: relative;
            width: 100%;
            min-height: 560px;
            overflow: hidden;
            background: #0f172a;
            border: 1px solid #475569;
            border-radius: 6px;
        }}
        .grid-background {{
            position: absolute;
            inset: 0;
            background-image:
                linear-gradient(rgba(148, 163, 184, 0.10) 1px, transparent 1px),
                linear-gradient(90deg, rgba(148, 163, 184, 0.10) 1px, transparent 1px);
            background-size: 32px 32px;
        }}
        .shelter-shape {{
            position: absolute;
            left: {SHELTER_SHAPE["left"]}%;
            top: {SHELTER_SHAPE["top"]}%;
            width: {SHELTER_SHAPE["width"]}%;
            height: {SHELTER_SHAPE["height"]}%;
            border: 2px solid #e2e8f0;
            background: rgba(30, 41, 59, 0.70);
            border-radius: 2px;
            box-shadow: inset 0 0 28px rgba(96, 165, 250, 0.08);
        }}
        .wall {{
            position: absolute;
            background: #e2e8f0;
        }}
        .wall-main {{
            left: 52%;
            top: 20%;
            width: 2px;
            height: 80%;
        }}
        .wall-corridor {{
            left: 52%;
            top: 20%;
            width: 48%;
            height: 2px;
        }}
        .entry-label {{
            position: absolute;
            right: 8px;
            top: 8px;
            color: #94a3b8;
            font-size: 11px;
            font-weight: 700;
            letter-spacing: 0.08em;
        }}
        .marker {{
            position: absolute;
            width: 34px;
            height: 34px;
            transform: translate(-50%, -50%);
            border-radius: 9999px;
            border: 2px solid #e2e8f0;
            display: flex;
            align-items: center;
            justify-content: center;
            font-weight: 700;
            box-shadow: 0 8px 20px rgba(2, 6, 23, 0.45);
            z-index: 4;
        }}
        .marker span {{
            font-size: 11px;
            pointer-events: none;
        }}
        .esp {{
            background: #2563eb;
            color: white;
        }}
        .device-in {{
            background: #22c55e;
            color: #052e16;
        }}
        .device-out {{
            background: #facc15;
            color: #422006;
        }}
    </style>
    """


ui.add_head_html("""
<style>
body {
    background-color: #0f172a;
    color: #e2e8f0;
    font-family: Inter, Arial, sans-serif;
}
.card-clean {
    background-color: #1e293b;
    border: 1px solid #334155;
    padding: 12px;
    border-radius: 8px;
}
.metric-label {
    color: #94a3b8;
    font-size: 12px;
    font-weight: 700;
    text-transform: uppercase;
}
.metric-value {
    color: #e2e8f0;
    font-size: 18px;
    font-weight: 700;
}
.event-line {
    border-bottom: 1px solid #334155;
    color: #cbd5e1;
    font-size: 13px;
    padding: 7px 0;
}
.dot {
    width: 12px;
    height: 12px;
    border-radius: 9999px;
    display: inline-block;
    margin-right: 8px;
    border: 1px solid #e2e8f0;
}
</style>
""", shared=True)

def get_current_summary():
    poll_udp_packets()
    active_devices = get_active_devices()
    occupancy = calculate_occupancy(active_devices)
    state = get_shelter_state(occupancy)
    active_esp_ids = get_active_esp_ids()
    inside_count = occupancy
    not_confirmed_count = max(0, len(active_devices) - inside_count)

    update_event_log(active_devices, occupancy, state, active_esp_ids)

    print(f"UI refresh: devices={len(active_devices)}, occupancy={occupancy}")

    return {
        "active_devices": active_devices,
        "occupancy": occupancy,
        "state": state,
        "active_esp_ids": active_esp_ids,
        "inside_count": inside_count,
        "not_confirmed_count": not_confirmed_count,
    }


def get_recommendation(state):
    recommendations = {
        "AVAILABLE": "This shelter has available capacity.",
        "FILLING": "The shelter is filling, but capacity is still available.",
        "ALMOST_FULL": "The shelter is almost full. Consider another nearby shelter if possible.",
        "FULL": "The shelter is full. Choose another nearby shelter.",
        "UNKNOWN": "Shelter status is currently unknown.",
    }

    return recommendations.get(state, recommendations["UNKNOWN"])


def get_state_text_class(state):
    state_classes = {
        "AVAILABLE": "text-lg font-semibold text-emerald-300",
        "FILLING": "text-lg font-semibold text-yellow-300",
        "ALMOST_FULL": "text-lg font-semibold text-orange-300",
        "FULL": "text-lg font-semibold text-red-400",
        "UNKNOWN": "text-lg font-semibold text-slate-300",
    }

    return state_classes.get(state, state_classes["UNKNOWN"])


def get_state_badge_class(state):
    state_classes = {
        "AVAILABLE": "bg-emerald-500 text-emerald-950",
        "FILLING": "bg-yellow-400 text-yellow-950",
        "ALMOST_FULL": "bg-orange-400 text-orange-950",
        "FULL": "bg-red-500 text-red-950",
        "UNKNOWN": "bg-slate-400 text-slate-950",
    }

    return state_classes.get(state, state_classes["UNKNOWN"])


@ui.page("/")
def public_page():
    ui.dark_mode().enable()
    ui.query("body").classes("bg-[#0f172a] text-slate-200")

    with ui.column().classes("w-full min-h-screen items-center justify-center p-6"):
        with ui.element("div").classes("card-clean w-full max-w-2xl"):
            ui.label("Shelter availability").classes("text-sm font-bold text-blue-400 uppercase tracking-wide")
            ui.label(SHELTER_NAME).classes("text-3xl font-bold text-slate-100 mt-2")

            state_badge = ui.label("Available").classes("inline-flex px-4 py-2 rounded-full mt-6 font-bold bg-emerald-500 text-emerald-950")
            occupancy_label = ui.label(f"0 / {SHELTER_CAPACITY}").classes("text-6xl font-bold text-blue-300 mt-8")
            ui.label("occupied places").classes("metric-label")
            progress = ui.linear_progress(value=0).classes("w-full mt-6")
            recommendation_label = ui.label("This shelter has available capacity.").classes("text-lg text-slate-200 mt-6")
            updated_label = ui.label("Last update: -").classes("text-sm text-slate-400 mt-6")

    def refresh_public_ui():
        summary = get_current_summary()
        occupancy = summary["occupancy"]
        state = summary["state"]
        fill_ratio = 0 if SHELTER_CAPACITY <= 0 else min(1, occupancy / SHELTER_CAPACITY)

        state_badge.text = get_state_display(state)
        state_badge.classes(replace=f"inline-flex px-4 py-2 rounded-full mt-6 font-bold {get_state_badge_class(state)}")
        occupancy_label.text = f"{occupancy} / {SHELTER_CAPACITY}"
        progress.value = fill_ratio
        recommendation_label.text = get_recommendation(state)
        updated_label.text = f"Last update: {time.strftime('%H:%M:%S')}"

    ui.timer(1.0, refresh_public_ui)


@ui.page("/admin")
def admin_page():
    ui.dark_mode().enable()
    ui.query("body").classes("bg-[#0f172a] text-slate-200")

    with ui.row().classes("w-full h-screen p-4 gap-4"):
        with ui.column().classes("w-72 gap-4"):
            ui.label(f"Shelter: {SHELTER_NAME}").classes("text-sm font-bold text-blue-400")
            status_label = ui.label("STATUS: ONLINE").classes("text-sm font-bold text-green-400")

            with ui.dialog() as settings_dialog, ui.card().classes("bg-slate-800 text-slate-100 min-w-[640px]"):
                ui.label("Settings").classes("text-xl font-bold text-blue-300")

                with ui.grid(columns=2).classes("w-full gap-4 mt-3"):


                    marker_inputs = []

                for esp_id in ESP_IDS:
                    existing_marker = next(
                        (marker for marker in ESP_MARKERS if marker.get("id") == esp_id),
                        {"id": esp_id, "x_m": 0, "y_m": 0},
                    )

                    with ui.row().classes("w-full items-center gap-3"):
                        ui.label(esp_id).classes("w-16 font-bold text-blue-300")
                        x_input = ui.number(
                            "x_m",
                            value=float(existing_marker.get("x_m", existing_marker.get("x", 0))),
                            min=0,
                            max=float(FLOOR_PLAN.get("width", 7.4)),
                            step=0.1,
                        ).classes("w-40")
                        y_input = ui.number(
                            "y_m",
                            value=float(existing_marker.get("y_m", existing_marker.get("y", 0))),
                            min=0,
                            max=float(FLOOR_PLAN.get("height", 3.0)),
                            step=0.1,
                        ).classes("w-40")
                        marker_inputs.append((esp_id, x_input, y_input))

                def save_settings_from_dialog():
                    markers = [
                        {
                            "id": esp_id,
                            "x_m": round(float(x_input.value or 0), 2),
                            "y_m": round(float(y_input.value or 0), 2),
                        }
                        for esp_id, x_input, y_input in marker_inputs
                    ]

                    save_shelter_settings(
                        esp_markers=markers,
                    )
                    zone_html.content = build_zone_html(get_active_devices())
                    zone_html.update()
                    settings_dialog.close()

                def open_settings_dialog():
                    for esp_id, x_input, y_input in marker_inputs:
                        existing_marker = next(
                            (marker for marker in ESP_MARKERS if marker.get("id") == esp_id),
                            {"id": esp_id, "x_m": 0, "y_m": 0},
                        )
                        x_input.value = float(existing_marker.get("x_m", existing_marker.get("x", 0)))
                        y_input.value = float(existing_marker.get("y_m", existing_marker.get("y", 0)))

                    settings_dialog.open()

                with ui.row().classes("w-full justify-end gap-2 mt-4"):
                    ui.button("CANCEL", on_click=settings_dialog.close).classes("bg-slate-600 text-white")
                    ui.button("SAVE", on_click=save_settings_from_dialog).classes("bg-blue-600 text-white")

            ui.button("SETTINGS", on_click=open_settings_dialog).classes("bg-slate-600 w-full text-white")

            with ui.element("div").classes("card-clean w-full"):
                ui.label("Shelter status").classes("font-bold text-lg mb-2")
                header_state_label = ui.label("Available").classes("text-lg font-semibold text-emerald-300")
                occupancy_label = ui.label(f"0 / {SHELTER_CAPACITY}").classes("text-3xl font-bold text-blue-300 mt-2")
                ui.label("occupancy").classes("metric-label")

                with ui.column().classes("gap-1 mt-4"):
                    detected_label = ui.label("Detected devices: 0").classes("text-sm")
                    inside_label = ui.label("Inside shelter: 0").classes("text-sm")
                    not_confirmed_label = ui.label("Not confirmed inside: 0").classes("text-sm")
                    active_nodes_label = ui.label(f"Active nodes: 0/{len(ESP_IDS)}").classes("text-sm")

            with ui.element("div").classes("card-clean w-full"):
                ui.label("Node status").classes("font-bold text-lg mb-2")
                node_status_labels = {}

                for esp_id in ESP_IDS:
                    node_status_labels[esp_id] = ui.label(f"{esp_id}: OFFLINE").classes("text-red-400 text-sm font-semibold")

        with ui.column().classes("flex-1 gap-3"):
            zone_html = ui.html(build_zone_html([])).classes("w-full")

            with ui.element("div").classes("card-clean w-full"):
                ui.label("Markers").classes("font-bold text-base mb-2")
                with ui.row().classes("gap-6 items-center"):
                    ui.html('<span class="dot" style="background:#2563eb;"></span>ESP node')
                    ui.html('<span class="dot" style="background:#22c55e;"></span>Device inside shelter')
                    ui.html('<span class="dot" style="background:#facc15;"></span>Device not confirmed inside')

        with ui.column().classes("w-80 gap-4"):
            with ui.element("div").classes("card-clean w-full h-[720px] overflow-hidden"):
                ui.label("Event log").classes("font-bold text-lg mb-2")
                event_log_container = ui.column().classes("w-full gap-0")

    def refresh_event_log():
        event_log_container.clear()

        with event_log_container:
            if not event_logs:
                ui.label("No events yet").classes("text-sm text-slate-400")
                return

            for event in event_logs:
                ui.label(event).classes("event-line w-full")

    def refresh_admin_ui():
        summary = get_current_summary()
        active_devices = summary["active_devices"]
        occupancy = summary["occupancy"]
        state = summary["state"]
        active_esp_ids = summary["active_esp_ids"]

        occupancy_label.text = f"{occupancy} / {SHELTER_CAPACITY}"
        status_label.text = "STATUS: ONLINE"
        header_state_label.text = get_state_display(state)
        detected_label.text = f"Detected devices: {len(active_devices)}"
        inside_label.text = f"Inside shelter: {summary['inside_count']}"
        not_confirmed_label.text = f"Not confirmed inside: {summary['not_confirmed_count']}"
        active_nodes_label.text = f"Active nodes: {len(active_esp_ids)}/{len(ESP_IDS)}"

        header_state_label.classes(replace=get_state_text_class(state))

        for esp_id, label in node_status_labels.items():
            if esp_id in active_esp_ids:
                label.text = f"{esp_id}: ACTIVE"
                label.classes(replace="text-green-400 text-sm font-semibold")
            else:
                label.text = f"{esp_id}: OFFLINE"
                label.classes(replace="text-red-400 text-sm font-semibold")

        zone_html.content = build_zone_html(active_devices)
        zone_html.update()
        refresh_event_log()


    ui.timer(1.0, refresh_admin_ui)

ui.run(host="0.0.0.0", port=8080, reload=False)
