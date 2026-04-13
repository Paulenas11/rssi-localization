import math 

def rssi_to_distance(rssi0, rssi, n=2.0):
    """
    rssi0 - RSSI at 1 meter distance (dBm)
    rssi - measured RSSI (dBm)
    n - path loss exponent (environment dependent, typically 2-4)
    Returns distance in meters
    """
    
    exponent = (rssi0 - rssi) / (10 * n)
    return 10 ** exponent

def trilaterate(p1, p2, p3, r1, r2, r3):
    """
    p1, p2, p3 - AP coordinates (x,y)
    r1, r2, r3 - distances to APs (meters)
    Returns (x,y) position of the device
    """

    x1, y1 = p1
    x2, y2 = p2
    x3, y3 = p3

    A = 2 * (x2 - x1)
    B = 2 * (y2 - y1)
    C = r1**2 - r2**2 - x1**2 + x2**2 - y1**2 + y2**2

    D = 2 * (x3 - x1)
    E = 2 * (y3 - y1)
    F = r1**2 - r3**2 - x1**2 + x3**2 - y1**2 + y3**2

    # Solve the system of equations using Cramer's rule
    denominator = A * E - B * D
    if denominator == 0:
        raise ValueError("The circles do not intersect in a single point.")

    x = (C * E - B * F) / denominator
    y = (A * F - C * D) / denominator

    return (x, y)