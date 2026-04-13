from collections import deque
from utils import rssi_to_distance
import serial

def main():
    rssi0 = -45
    n = 3.0

    ser = serial.Serial("COM8", 115200)
    print("Klausau RSSI...")

    window = deque(maxlen=10) 

    while True:
        line = ser.readline().decode().strip()
        if not line:
            continue

        try:
            rssi = int(line)
        except ValueError:
            continue

        # Filtering
        window.append(rssi)
        filtered_rssi = sum(window) / len(window)

        d = rssi_to_distance(rssi0, filtered_rssi, n)

        print(f"RSSI(raw): {rssi}, RSSI(avg): {filtered_rssi:.1f} -> atstumas: {d:.2f} m")

if __name__ == "__main__":
    main()
