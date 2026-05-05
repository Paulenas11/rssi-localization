import socket
import json
from collections import deque
from utils import rssi_to_distance, trilaterate

UDP_IP = "0.0.0.0"
UDP_PORT = 5000

print("STEP 1: Creating socket")
sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

print("STEP 2: Binding...")
sock.bind((UDP_IP, UDP_PORT))

print("STEP 3: Bound successfully!")


print(f"Listening on UDP port {UDP_PORT}...")

windows = {
    1: deque(maxlen=10),
    2: deque(maxlen=10),
    3: deque(maxlen=10)
}

AP = {
    1: (0, 0),
    2: (-0.6, 0.5),
    3: (-1, 0)
}

while True:
    data, addr = sock.recvfrom(1024)

    try:
        msg = json.loads(data.decode())
    except:
        continue

    node_id = msg["id"]
    rssi = msg["rssi"]

    windows[node_id].append(rssi)
    filtered = sum(windows[node_id]) / len(windows[node_id])

    print(f"Node {node_id}: {filtered:.1f} dBm")

    if all(len(windows[i]) > 0 for i in [1, 2, 3]):
        f1 = sum(windows[1]) / len(windows[1])
        f2 = sum(windows[2]) / len(windows[2])
        f3 = sum(windows[3]) / len(windows[3])

        d1 = abs(f1)
        d2 = abs(f2)
        d3 = abs(f3)

        max_d = max(d1, d2, d3)
        d1 /= max_d
        d2 /= max_d
        d3 /= max_d



        print(f"D1={d1:.2f}  D2={d2:.2f}  D3={d3:.2f}")

        pos = trilaterate(AP[1], AP[2], AP[3], d1, d2, d3)
        print("Position:", pos)
        print("---")
