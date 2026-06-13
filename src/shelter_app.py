from nicegui import ui
import json
import socket
import time
from pathlib import Path


UDP_IP = "0.0.0.0"
UDP_PORT = 5005
DEVICE_TIMEOUT = 15
MIN_RSSI_IN_SHELTER = -75
MIN_NODES_IN_SHELTER = 1
STATE_FILE = Path(__file__).with_name("shelter_state.json")
SETTINGS_FILE = Path(__file__).with_name("settings.json")

SHELTER_NAME = "Test shelter"
SHELTER_CAPACITY = 10
ESP_IDS = ["ESP_1", "ESP_2", "ESP_3", "ESP_4"]
ESP_MARKERS = [
    {"id": "ESP_1", "x": 50, "y": 20},
    {"id": "ESP_2", "x": 25, "y": 70},
    {"id": "ESP_3", "x": 70, "y": 55},
    {"id": "ESP_4", "x": 88, "y": 38},
]
FLOOR_PLAN = {
    "width": 7.4,
    "height": 3.0,
    "rooms": [
        {"name": "Room", "x": 0.0, "y": 0.0, "width": 4.5, "height": 2.85},
        {"name": "Entrance", "x": 4.8, "y": 0.0, "width": 2.6, "height": 1.0},
        {"name": "WC", "x": 4.8, "y": 1.3, "width": 2.35, "height": 1.65},
    ],
}

IGNORED_MACS = {
    "AC:A7:04:BE:5F:F8",
    "AC:A7:04:BD:3B:20",
    "44:3E:07:1C:FB:5D",
}

devices = {}
esp_last_seen = {}
udp_socket = None
udp_socket_error_shown = False
event_logs = []
previous_active_macs = set()
previous_active_esp_ids = set()
previous_occupancy = None
previous_state = None
event_state_initialized = False


def load_settings():
    global UDP_PORT
    global DEVICE_TIMEOUT
    global MIN_RSSI_IN_SHELTER
    global MIN_NODES_IN_SHELTER
    global SHELTER_NAME
    global SHELTER_CAPACITY
    global ESP_IDS
    global ESP_MARKERS
    global FLOOR_PLAN
    global IGNORED_MACS

    if not SETTINGS_FILE.exists():
        return

    try:
        data = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as error:
        print("Settings load error:", error)
        return

    shelter_settings = data.get("shelter", {})

    UDP_PORT = int(shelter_settings.get("udp_port", UDP_PORT))
    DEVICE_TIMEOUT = int(shelter_settings.get("device_timeout", DEVICE_TIMEOUT))
    MIN_RSSI_IN_SHELTER = int(shelter_settings.get("min_rssi_in_shelter", MIN_RSSI_IN_SHELTER))
    MIN_NODES_IN_SHELTER = int(shelter_settings.get("min_nodes_in_shelter", MIN_NODES_IN_SHELTER))
    SHELTER_NAME = str(shelter_settings.get("name", SHELTER_NAME))
    SHELTER_CAPACITY = int(shelter_settings.get("capacity", SHELTER_CAPACITY))

    ignored_macs = shelter_settings.get("ignored_macs")
    if isinstance(ignored_macs, list):
        IGNORED_MACS = {str(mac).upper() for mac in ignored_macs}

    esp_ids = shelter_settings.get("esp_ids")
    if isinstance(esp_ids, list) and esp_ids:
        ESP_IDS = [str(esp_id) for esp_id in esp_ids]

    esp_markers = shelter_settings.get("esp_markers")
    if isinstance(esp_markers, list) and esp_markers:
        ESP_MARKERS = esp_markers

    floor_plan = shelter_settings.get("floor_plan")
    if isinstance(floor_plan, dict):
        FLOOR_PLAN.update(floor_plan)


load_settings()


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


def is_multicast_or_broadcast_mac(mac: str) -> bool:
    parts = mac.split(":")

    if len(parts) != 6:
        return True

    try:
        first_byte = int(parts[0], 16)
    except ValueError:
        return True

    return mac == "FF:FF:FF:FF:FF:FF" or bool(first_byte & 1)


def get_status(rssi: int) -> str:
    if rssi >= MIN_RSSI_IN_SHELTER:
        return "IN_SHELTER"

    return "WEAK_SIGNAL"


def get_shelter_state(occupancy: int) -> str:
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


def get_state_display(state: str) -> str:
    names = {
        "AVAILABLE": "Available",
        "FILLING": "Filling",
        "ALMOST_FULL": "Almost full",
        "FULL": "Full",
        "UNKNOWN": "Unknown",
    }
    return names.get(state, state.title())


def update_devices(packet):
    now = time.time()

    if isinstance(packet, dict):
        packet = [packet]

    print("Received packet:", packet)

    for item in packet:
        mac = str(item.get("mac", "")).upper()
        esp_id = str(item.get("esp_id", "unknown"))

        if esp_id != "unknown":
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
            "status": item.get("status") or get_status(rssi),
            "last_seen": now,
        }
        print("Updated device:", mac, "ESP", esp_id, "RSSI", rssi)

    save_state()


def save_state():
    state = {
        "devices": devices,
        "esp_last_seen": esp_last_seen,
    }

    STATE_FILE.write_text(
        json.dumps(state, ensure_ascii=False),
        encoding="utf-8",
    )


def load_state():
    global devices, esp_last_seen

    if not STATE_FILE.exists():
        return

    try:
        state = json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return

    if "devices" in state:
        devices = state.get("devices", {})
        esp_last_seen = state.get("esp_last_seen", {})
    else:
        devices = state


def get_active_devices():
    load_state()
    now = time.time()
    active_devices = []

    for mac, esp_data in devices.items():
        active_nodes = []

        for esp_id, data in esp_data.items():
            age = now - data["last_seen"]

            if age <= DEVICE_TIMEOUT:
                active_nodes.append((esp_id, dict(data), age))

        if active_nodes:
            active_devices.append((mac, active_nodes))

    return active_devices


def calculate_occupancy(active_devices):
    return sum(
        1 for _, active_nodes in active_devices
        if sum(1 for _, data, _ in active_nodes if data["status"] == "IN_SHELTER") >= MIN_NODES_IN_SHELTER
    )


def get_active_esp_ids():
    load_state()
    now = time.time()
    active_ids = []

    for esp_id, last_seen in esp_last_seen.items():
        if now - last_seen <= DEVICE_TIMEOUT:
            active_ids.append(f"ESP_{esp_id}")

    return sorted(active_ids)


def get_strongest_rssi(active_nodes):
    return max(data["rssi"] for _, data, _ in active_nodes)


def get_active_macs(active_devices):
    return {mac for mac, _ in active_devices}


def add_event(message: str):
    timestamp = time.strftime("%H:%M:%S")
    event_logs.insert(0, f"[{timestamp}] {message}")

    if len(event_logs) > 14:
        event_logs.pop()


def update_event_log(active_devices, active_esp_ids, occupancy, state):
    global previous_active_macs
    global previous_active_esp_ids
    global previous_occupancy
    global previous_state
    global event_state_initialized

    active_macs = get_active_macs(active_devices)
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

        if previous_occupancy is not None and occupancy != previous_occupancy:
            add_event(f"Occupancy changed: {occupancy}/{SHELTER_CAPACITY}")

        if previous_state is not None and state != previous_state:
            add_event(f"Availability changed: {get_state_display(state)}")

    previous_active_macs = active_macs
    previous_active_esp_ids = active_esp_set
    previous_occupancy = occupancy
    previous_state = state


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


def device_rows(active_devices):
    rows = []

    for mac, active_nodes in active_devices:
        in_shelter = any(data["status"] == "IN_SHELTER" for _, data, _ in active_nodes)
        status = "IN_SHELTER" if in_shelter else "WEAK_SIGNAL"
        strongest_rssi = get_strongest_rssi(active_nodes)
        rssi_values = []

        for esp_id, data, age in sorted(active_nodes, key=lambda item: item[0]):
            rssi_values.append(
                f"ESP_{esp_id}: {data['rssi']} dBm ({age:.1f}s)"
            )

        rows.append({
            "mac": mac,
            "status": status,
            "strongest": f"{strongest_rssi} dBm",
            "nodes": ", ".join(rssi_values),
        })

    return rows


def build_zone_html(active_devices):
    device_positions = [
        (37, 35),
        (55, 35),
        (46, 55),
        (30, 70),
        (62, 64),
        (8, 35),
        (50, 88),
        (83, 40),
        (24, 24),
        (74, 76),
    ]
    markers = []
    plan_width = float(FLOOR_PLAN.get("width", 0))
    plan_height = float(FLOOR_PLAN.get("height", 0))
    width_label = f"{plan_width:g} m" if plan_width else ""
    height_label = f"{plan_height:g} m" if plan_height else ""

    for marker in ESP_MARKERS:
        esp_id = str(marker.get("id", "ESP"))
        left = float(marker.get("x", 50))
        top = float(marker.get("y", 50))
        markers.append(
            f"""
            <div class="marker esp" style="left:{left}%; top:{top}%;">
                <span>{esp_id.replace('ESP_', '')}</span>
            </div>
            """
        )

    for index, (mac, active_nodes) in enumerate(active_devices[:len(device_positions)]):
        left, top = device_positions[index]
        in_shelter = any(data["status"] == "IN_SHELTER" for _, data, _ in active_nodes)
        marker_class = "device-in" if in_shelter else "device-out"
        label = index + 1
        markers.append(
            f"""
            <div class="marker {marker_class}" style="left:{left}%; top:{top}%;" title="{mac}">
                <span>{label}</span>
            </div>
            """
        )

    return f"""
    <div class="zone-panel">
        <div class="zone-header">
            <div>
                <div class="zone-title">Shelter area</div>
                <div class="zone-subtitle">Local monitoring plane</div>
            </div>
        </div>
        <div class="outer-zone">
            <div class="grid-line horizontal h1"></div>
            <div class="grid-line horizontal h2"></div>
            <div class="grid-line vertical v1"></div>
            <div class="grid-line vertical v2"></div>
            <div class="shelter-shape">
                <div class="shape-label">DORM ROOM AREA</div>
                <div class="dimension horizontal">{width_label}</div>
                <div class="dimension vertical">{height_label}</div>
                <div class="inner-wall vertical-wall"></div>
                <div class="inner-wall horizontal-wall"></div>
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
            background:
                radial-gradient(circle at 20% 20%, rgba(59, 130, 246, 0.10), transparent 28%),
                linear-gradient(135deg, #0f172a, #111827);
            border: 1px solid #475569;
            border-radius: 6px;
        }}
        .shelter-shape {{
            position: absolute;
            left: 10%;
            top: 14%;
            width: 80%;
            height: 72%;
            background: rgba(30, 41, 59, 0.76);
            border: 2px solid #60a5fa;
            border-radius: 6px;
            box-shadow: inset 0 0 35px rgba(96, 165, 250, 0.10);
            color: #93c5fd;
        }}
        .shape-label {{
            position: absolute;
            left: 16px;
            top: 14px;
            font-size: 13px;
            font-weight: 700;
            letter-spacing: 0.04em;
        }}
        .dimension {{
            position: absolute;
            color: #cbd5e1;
            font-size: 12px;
            font-weight: 700;
        }}
        .dimension.horizontal {{
            left: 50%;
            top: -24px;
            transform: translateX(-50%);
        }}
        .dimension.vertical {{
            left: -38px;
            top: 50%;
            transform: translateY(-50%) rotate(-90deg);
        }}
        .inner-wall {{
            position: absolute;
            background: #cbd5e1;
            opacity: 0.9;
            z-index: 1;
        }}
        .vertical-wall {{
            left: 61%;
            top: 33%;
            width: 2px;
            height: 67%;
        }}
        .horizontal-wall {{
            left: 61%;
            top: 33%;
            width: 39%;
            height: 2px;
        }}
        .grid-line {{
            position: absolute;
            background: rgba(148, 163, 184, 0.10);
        }}
        .grid-line.horizontal {{
            left: 0;
            width: 100%;
            height: 1px;
        }}
        .grid-line.vertical {{
            top: 0;
            height: 100%;
            width: 1px;
        }}
        .h1 {{
            top: 33%;
        }}
        .h2 {{
            top: 66%;
        }}
        .v1 {{
            left: 33%;
        }}
        .v2 {{
            left: 66%;
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
            color: white;
            font-weight: 700;
            box-shadow: 0 8px 20px rgba(2, 6, 23, 0.45);
        }}
        .marker span {{
            font-size: 11px;
            pointer-events: none;
        }}
        .esp {{
            background: #2563eb;
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
    font-family: Inter, sans-serif;
}
.card-clean {
    background-color: #1e293b;
    border: 1px solid #334155;
    padding: 12px;
    border-radius: 8px;
}
</style>
""", shared=True)

ui.dark_mode().enable()
ui.query("body").classes("bg-[#0f172a] text-slate-200")

with ui.row().classes("w-full h-screen p-4 gap-4"):
    with ui.column().classes("w-64 gap-4"):
        ui.label(f"Shelter: {SHELTER_NAME}").classes("text-sm font-bold text-blue-400")
        status_label = ui.label("STATUS: ONLINE").classes("text-sm font-bold text-green-400")

        ui.button("SETTINGS").classes("bg-slate-600 w-full text-white")

        with ui.element("div").classes("card-clean w-full"):
            ui.label("Shelter status").classes("font-bold text-lg mb-1")
            occupancy_label = ui.label("0 / 10").classes("text-4xl font-bold text-blue-300")
            header_state_label = ui.label("Available").classes("text-green-400 font-bold")
            detected_label = ui.label("Detected devices: 0").classes("text-sm text-slate-300")
            inside_label = ui.label("Inside shelter: 0").classes("text-green-400")
            outside_label = ui.label("Outside shelter: 0").classes("text-yellow-300")
            active_nodes_label = ui.label("Active nodes: 0/4").classes("text-blue-300")

        with ui.element("div").classes("card-clean w-full"):
            ui.label("Node status").classes("font-bold text-lg mb-1")
            esp_status_labels = {}
            for esp_name in ESP_IDS:
                esp_status_labels[esp_name] = ui.label(f"{esp_name}: OFFLINE").classes("text-red-400")

    with ui.column().classes("flex-1"):
        ui.label("Shelter Area View").classes("text-xl font-bold mb-2 text-blue-300")
        zone_html = ui.html(build_zone_html([])).classes("w-full")
        with ui.element("div").classes("card-clean w-full mt-3"):
            ui.label("Markers").classes("font-bold text-lg mb-1")
            with ui.row().classes("gap-6 flex-wrap"):
                with ui.row().classes("items-center gap-2"):
                    ui.label("").classes("w-3 h-3 rounded-full bg-blue-500")
                    ui.label("ESP node").classes("text-sm")
                with ui.row().classes("items-center gap-2"):
                    ui.label("").classes("w-3 h-3 rounded-full bg-green-400")
                    ui.label("Device inside shelter").classes("text-sm")
                with ui.row().classes("items-center gap-2"):
                    ui.label("").classes("w-3 h-3 rounded-full bg-yellow-300")
                    ui.label("Device outside shelter").classes("text-sm")

    with ui.column().classes("w-80 gap-4"):
        with ui.element("div").classes("card-clean w-full h-[640px]"):
            ui.label("Events").classes("font-bold text-lg mb-1")
            event_log_container = ui.column().classes("gap-1")


def refresh_ui():
    poll_udp_packets()
    active_devices = get_active_devices()
    occupancy = calculate_occupancy(active_devices)
    state = get_shelter_state(occupancy)
    active_esp_ids = get_active_esp_ids()
    outside_count = max(0, len(active_devices) - occupancy)
    update_event_log(active_devices, active_esp_ids, occupancy, state)

    print(f"UI refresh: devices={len(active_devices)}, occupancy={occupancy}")
    occupancy_label.text = f"{occupancy} / {SHELTER_CAPACITY}"
    status_label.text = "STATUS: ONLINE"
    header_state_label.text = f"Availability: {get_state_display(state)}"
    detected_label.text = f"Detected devices: {len(active_devices)}"
    inside_label.text = f"Inside shelter: {occupancy}"
    outside_label.text = f"Outside shelter: {outside_count}"
    active_nodes_label.text = f"Active nodes: {len(active_esp_ids)}/{len(ESP_IDS)}"

    for esp_name in ESP_IDS:
        if esp_name in active_esp_ids:
            esp_status_labels[esp_name].text = f"{esp_name}: ACTIVE"
            esp_status_labels[esp_name].classes(replace="text-green-400")
        else:
            esp_status_labels[esp_name].text = f"{esp_name}: OFFLINE"
            esp_status_labels[esp_name].classes(replace="text-red-400")

    state_classes = {
        "AVAILABLE": "text-green-400 font-bold",
        "FILLING": "text-yellow-400 font-bold",
        "ALMOST_FULL": "text-orange-400 font-bold",
        "FULL": "text-red-400 font-bold",
        "UNKNOWN": "text-slate-300 font-bold",
    }
    header_state_label.classes(replace=state_classes.get(state, state_classes["UNKNOWN"]))
    zone_html.content = build_zone_html(active_devices)
    zone_html.update()

    event_log_container.clear()
    with event_log_container:
        if event_logs:
            for event in event_logs:
                ui.label(event).classes("text-sm")
        else:
            ui.label("No events").classes("text-sm text-slate-400")


ui.timer(1.0, refresh_ui)

ui.run(host="0.0.0.0", port=8080, reload=False)
