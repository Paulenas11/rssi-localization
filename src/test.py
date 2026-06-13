import json
import socket
import time


UDP_IP = "0.0.0.0"
UDP_PORT = 5005
DEVICE_TIMEOUT = 15
MIN_RSSI_IN_SHELTER = -75
IGNORED_MACS = {
    "AC:A7:04:BE:5F:F8",
    "AC:A7:04:BD:3B:20",
    "44:3E:07:1C:FB:5D",
}

devices = {}


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


def update_devices(packet):
    now = time.time()

    if isinstance(packet, dict):
        packet = [packet]

    for item in packet:
        mac = str(item.get("mac", "")).upper()

        if not mac or mac in IGNORED_MACS or is_multicast_or_broadcast_mac(mac):
            continue

        try:
            rssi = int(item.get("rssi"))
        except (TypeError, ValueError):
            continue

        esp_id = str(item.get("esp_id", "unknown"))

        if mac not in devices:
            devices[mac] = {}

        devices[mac][esp_id] = {
            "esp_id": item.get("esp_id"),
            "rssi": rssi,
            "packets": item.get("packets", 0),
            "status": item.get("status") or get_status(rssi),
            "last_seen": now,
        }


def print_state():
    now = time.time()
    active_devices = []

    for mac, esp_data in devices.items():
        active_nodes = []

        for esp_id, data in esp_data.items():
            age = now - data["last_seen"]

            if age <= DEVICE_TIMEOUT:
                active_nodes.append((esp_id, data, age))

        if active_nodes:
            active_devices.append((mac, active_nodes))

    occupancy = sum(
        1 for _, active_nodes in active_devices
        if any(data["status"] == "IN_SHELTER" for _, data, _ in active_nodes)
    )

    print("\n=== Shelter occupancy test ===")
    print(f"Occupancy: {occupancy}")

    for mac, active_nodes in active_devices:
        device_status = (
            "IN_SHELTER"
            if any(data["status"] == "IN_SHELTER" for _, data, _ in active_nodes)
            else "WEAK_SIGNAL"
        )

        print(f"{mac} | status: {device_status}")

        for esp_id, data, age in sorted(active_nodes, key=lambda item: item[0]):
            print(
                f"  ESP_{esp_id} | "
                f"RSSI: {data['rssi']} dBm | "
                f"packets: {data['packets']} | "
                f"age: {age:.1f}s | "
                f"status: {data['status']}"
            )


def main():
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind((UDP_IP, UDP_PORT))
    sock.settimeout(1.0)

    print("=== Sniffer UDP test server started ===")
    print(f"Listening on UDP port {UDP_PORT}")

    last_print = 0

    while True:
        try:
            data, addr = sock.recvfrom(4096)
        except socket.timeout:
            data = None

        if data:
            try:
                packet = json.loads(data.decode())
                update_devices(packet)
            except json.JSONDecodeError:
                print("Invalid JSON received")

        if time.time() - last_print >= 2:
            print_state()
            last_print = time.time()


if __name__ == "__main__":
    main()
