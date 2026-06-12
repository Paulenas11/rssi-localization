#include <WiFi.h>
#include <WiFiUdp.h>
#include <ArduinoJson.h>
#include "esp_wifi.h"

// First test sketch:
// ESP32-C3 listens for Wi-Fi frames and prints transmitter MAC addresses with RSSI.
// Use a 2.4 GHz Wi-Fi network for testing. ESP32-C3 cannot sniff 5 GHz traffic.

#define MAX_DEVICES 64
#define PRINT_INTERVAL_MS 2000
#define MIN_PACKETS_TO_SHOW 20
#define MIN_RSSI_IN_SHELTER -75
#define DEVICE_TIMEOUT_MS 15000
#define UDP_SEND_INTERVAL_MS 2000
#define ESP_ID 1

const char* WIFI_SSID = "paul";
const char* WIFI_PASS = "mptd7506";
const char* SERVER_IP = "192.168.137.1";
const int SERVER_PORT = 5005;

// Set these values from your shelter Wi-Fi / laptop hotspot.
// AP_BSSID is the MAC/BSSID of the access point, not the phone MAC.
#define WIFI_CHANNEL 6
uint8_t AP_BSSID[6] = {0x52, 0x2f, 0x9b, 0x81, 0x95, 0xd4};

struct DeviceInfo {
  uint8_t mac[6];
  int rssi;
  unsigned long last_seen;
  uint32_t packets;
  bool used;
};

DeviceInfo devices[MAX_DEVICES];

uint8_t current_channel = 1;
unsigned long last_print = 0;
unsigned long last_udp_send = 0;
WiFiUDP udp;

typedef struct {
  uint16_t frame_ctrl;
  uint16_t duration_id;
  uint8_t addr1[6];
  uint8_t addr2[6];
  uint8_t addr3[6];
  uint16_t sequence_ctrl;
} wifi_ieee80211_mac_hdr_t;

void copy_mac(uint8_t* destination, const uint8_t* source) {
  for (int i = 0; i < 6; i++) {
    destination[i] = source[i];
  }
}

bool same_mac(const uint8_t* first, const uint8_t* second) {
  for (int i = 0; i < 6; i++) {
    if (first[i] != second[i]) {
      return false;
    }
  }

  return true;
}

bool is_empty_mac(const uint8_t* mac) {
  for (int i = 0; i < 6; i++) {
    if (mac[i] != 0x00) {
      return false;
    }
  }

  return true;
}

bool is_broadcast_mac(const uint8_t* mac) {
  for (int i = 0; i < 6; i++) {
    if (mac[i] != 0xFF) {
      return false;
    }
  }

  return true;
}

bool is_multicast_mac(const uint8_t* mac) {
  return (mac[0] & 0x01) != 0;
}

bool is_ap_mac(const uint8_t* mac) {
  return same_mac(mac, AP_BSSID);
}

bool is_invalid_client_mac(const uint8_t* mac) {
  return is_empty_mac(mac) || is_broadcast_mac(mac) || is_multicast_mac(mac) || is_ap_mac(mac);
}

bool packet_belongs_to_ap(const wifi_ieee80211_mac_hdr_t* header) {
  return is_ap_mac(header->addr1) || is_ap_mac(header->addr2) || is_ap_mac(header->addr3);
}

bool get_client_mac(const wifi_ieee80211_mac_hdr_t* header, uint8_t* client_mac) {
  if (is_ap_mac(header->addr1) && !is_invalid_client_mac(header->addr2)) {
    copy_mac(client_mac, header->addr2);
    return true;
  }

  if (is_ap_mac(header->addr2) && !is_invalid_client_mac(header->addr1)) {
    copy_mac(client_mac, header->addr1);
    return true;
  }

  if (is_ap_mac(header->addr3) && !is_invalid_client_mac(header->addr2)) {
    copy_mac(client_mac, header->addr2);
    return true;
  }

  return false;
}

void update_device(const uint8_t* mac, int rssi) {
  if (is_invalid_client_mac(mac)) {
    return;
  }

  for (int i = 0; i < MAX_DEVICES; i++) {
    if (devices[i].used && same_mac(devices[i].mac, mac)) {
      devices[i].rssi = rssi;
      devices[i].last_seen = millis();
      devices[i].packets++;
      return;
    }
  }

  for (int i = 0; i < MAX_DEVICES; i++) {
    if (!devices[i].used) {
      copy_mac(devices[i].mac, mac);
      devices[i].rssi = rssi;
      devices[i].last_seen = millis();
      devices[i].packets = 1;
      devices[i].used = true;
      return;
    }
  }
}

void sniffer_callback(void* buf, wifi_promiscuous_pkt_type_t type) {
  if (type != WIFI_PKT_MGMT && type != WIFI_PKT_DATA) {
    return;
  }

  wifi_promiscuous_pkt_t* packet = (wifi_promiscuous_pkt_t*)buf;
  wifi_ieee80211_mac_hdr_t* header = (wifi_ieee80211_mac_hdr_t*)packet->payload;

  if (!packet_belongs_to_ap(header)) {
    return;
  }

  uint8_t client_mac[6];

  if (!get_client_mac(header, client_mac)) {
    return;
  }

  update_device(client_mac, packet->rx_ctrl.rssi);
}

void print_mac(const uint8_t* mac) {
  for (int i = 0; i < 6; i++) {
    if (mac[i] < 16) {
      Serial.print("0");
    }

    Serial.print(mac[i], HEX);

    if (i < 5) {
      Serial.print(":");
    }
  }
}

void print_devices() {
  Serial.println();
  Serial.print("=== Clients on AP ");
  print_mac(AP_BSSID);
  Serial.print(" channel ");
  Serial.print(WIFI_CHANNEL);
  Serial.println(" ===");

  unsigned long now = millis();

  for (int i = 0; i < MAX_DEVICES; i++) {
    if (!devices[i].used) {
      continue;
    }

    unsigned long age = now - devices[i].last_seen;

    if (age > DEVICE_TIMEOUT_MS) {
      continue;
    }

    if (devices[i].packets < MIN_PACKETS_TO_SHOW) {
      continue;
    }

    const char* status = "WEAK_SIGNAL";

    if (devices[i].rssi >= MIN_RSSI_IN_SHELTER) {
      status = "IN_SHELTER";
    }

    Serial.print("MAC: ");
    print_mac(devices[i].mac);
    Serial.print(" RSSI: ");
    Serial.print(devices[i].rssi);
    Serial.print(" dBm packets: ");
    Serial.print(devices[i].packets);
    Serial.print(" age: ");
    Serial.print(age / 1000.0, 1);
    Serial.print("s status: ");
    Serial.println(status);
  }
}

void mac_to_string(const uint8_t* mac, char* output) {
  snprintf(
    output,
    18,
    "%02X:%02X:%02X:%02X:%02X:%02X",
    mac[0],
    mac[1],
    mac[2],
    mac[3],
    mac[4],
    mac[5]
  );
}

const char* get_device_status(const DeviceInfo& device) {
  if (device.rssi >= MIN_RSSI_IN_SHELTER) {
    return "IN_SHELTER";
  }

  return "WEAK_SIGNAL";
}

void send_udp_data() {
  StaticJsonDocument<2048> doc;
  JsonArray arr = doc.to<JsonArray>();
  unsigned long now = millis();

  for (int i = 0; i < MAX_DEVICES; i++) {
    if (!devices[i].used) {
      continue;
    }

    unsigned long age = now - devices[i].last_seen;

    if (age > DEVICE_TIMEOUT_MS) {
      continue;
    }

    if (devices[i].packets < MIN_PACKETS_TO_SHOW) {
      continue;
    }

    char mac_string[18];
    mac_to_string(devices[i].mac, mac_string);

    JsonObject obj = arr.createNestedObject();
    obj["esp_id"] = ESP_ID;
    obj["mac"] = mac_string;
    obj["ssid"] = WIFI_SSID;
    obj["rssi"] = devices[i].rssi;
    obj["packets"] = devices[i].packets;
    obj["status"] = get_device_status(devices[i]);
  }

  if (arr.size() == 0) {
    return;
  }

  char buffer[2048];
  size_t len = serializeJson(doc, buffer);

  udp.beginPacket(SERVER_IP, SERVER_PORT);
  udp.write((uint8_t*)buffer, len);
  udp.endPacket();

  Serial.print("Sent UDP: ");
  Serial.println(buffer);
}

void setup() {
  Serial.begin(115200);
  delay(1000);

  WiFi.mode(WIFI_MODE_STA);
  WiFi.begin(WIFI_SSID, WIFI_PASS);

  Serial.print("Connecting to AP");
  while (WiFi.status() != WL_CONNECTED) {
    delay(500);
    Serial.print(".");
  }

  Serial.println();
  Serial.print("Connected, ESP IP: ");
  Serial.println(WiFi.localIP());

  esp_wifi_set_promiscuous(false);
  esp_wifi_set_channel(WIFI_CHANNEL, WIFI_SECOND_CHAN_NONE);
  esp_wifi_set_promiscuous_rx_cb(&sniffer_callback);
  esp_wifi_set_promiscuous(true);

  Serial.println("=== ESP32-C3 Wi-Fi sniffer started ===");
  Serial.println("Listening only for clients related to the configured AP BSSID.");
}

void loop() {
  unsigned long now = millis();

  if (now - last_print >= PRINT_INTERVAL_MS) {
    print_devices();
    last_print = now;
  }

  if (now - last_udp_send >= UDP_SEND_INTERVAL_MS) {
    send_udp_data();
    last_udp_send = now;
  }
}
