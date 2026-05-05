import socket
import json
from collections import deque
import threading
import time
import tkinter as tk
from tkinter import ttk

import matplotlib
matplotlib.use("TkAgg")
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
import matplotlib.pyplot as plt

from utils import trilaterate

UDP_IP = "0.0.0.0"
UDP_PORT = 5000

sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
sock.bind((UDP_IP, UDP_PORT))
sock.setblocking(False)

windows = {
    1: deque(maxlen=10),
    2: deque(maxlen=10),
    3: deque(maxlen=10)
}

AP = {
    1: (0.6, 0),
    2: (0, 0.5),
    3: (-0.5, 0)
}

last_seen = {
    1: 0,
    2: 0,
    3: 0
}

running = False


def get_ap_status_labels():
    now = time.time()
    statuses = []

    for ap_id in [1, 2, 3]:
        if now - last_seen[ap_id] > 2:  
            statuses.append((f"AP{ap_id}: OFF", "red"))
        else:
            statuses.append((f"AP{ap_id}: ON", "green"))

    return statuses


def localization_loop(update_ui_callback, update_plot_callback):
    global running

    while running:
        try:
            data, addr = sock.recvfrom(1024)
        except BlockingIOError:
            time.sleep(0.05)
            continue

        try:
            msg = json.loads(data.decode())
        except:
            update_ui_callback("-", "-", None)
            continue

        node_id = msg.get("id")
        rssi = msg.get("rssi")

        if node_id not in windows:
            update_ui_callback("-", "-", None)
            continue

        windows[node_id].append(rssi)
        last_seen[node_id] = time.time()

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

            pos = trilaterate(AP[1], AP[2], AP[3], d1, d2, d3)

            if pos:
                x, y = pos
                update_ui_callback(f"{x:.2f}", f"{y:.2f}", None)
                update_plot_callback(x, y)
            else:
                update_ui_callback("-", "-", None)

        time.sleep(0.05)


def update_ui(x, y, _):
    coord_label.config(text=f"Coordinates: ({x}, {y})")

    statuses = get_ap_status_labels()

    ap1_label.config(text=statuses[0][0], foreground=statuses[0][1])
    ap2_label.config(text=statuses[1][0], foreground=statuses[1][1])
    ap3_label.config(text=statuses[2][0], foreground=statuses[2][1])


def start_localization():
    global running
    if running:
        return
    running = True

    thread = threading.Thread(
        target=localization_loop,
        args=(update_ui, update_plot),
        daemon=True
    )
    thread.start()


def stop_localization():
    global running
    running = False


root = tk.Tk()
root.title("Positioning system")

start_btn = ttk.Button(root, text="Start", command=start_localization)
stop_btn = ttk.Button(root, text="Stop", command=stop_localization)

coord_label = ttk.Label(root, text="Coordinates: (-, -)")

ap1_label = ttk.Label(root, text="AP1: -")
ap2_label = ttk.Label(root, text="AP2: -")
ap3_label = ttk.Label(root, text="AP3: -")

start_btn.pack(pady=5)
stop_btn.pack(pady=5)
coord_label.pack(pady=5)

ap1_label.pack()
ap2_label.pack()
ap3_label.pack()

fig, ax = plt.subplots(figsize=(5, 4))
ax.set_xlim(-1, 1)
ax.set_ylim(-0.5, 1)
ax.set_aspect('equal')
ax.set_title("Real-time Positioning")

ax.scatter([AP[1][0], AP[2][0], AP[3][0]],
           [AP[1][1], AP[2][1], AP[3][1]],
           c=['red','red','red'])

ax.text(AP[1][0], AP[1][1], "AP1")
ax.text(AP[2][0], AP[2][1], "AP2")
ax.text(AP[3][0], AP[3][1], "AP3")

point, = ax.plot([], [], 'bo', markersize=10)

canvas = FigureCanvasTkAgg(fig, master=root)
canvas.get_tk_widget().pack()


def update_plot(x, y):
    point.set_data([x], [y])
    canvas.draw_idle()


root.mainloop()
