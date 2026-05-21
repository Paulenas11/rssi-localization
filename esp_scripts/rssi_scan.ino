#include <WiFi.h>
#include <WiFiUdp.h>
#include <ArduinoJson.h>

#define ESP_ID 1   

const char* WIFI_SSID = "tinklo_pavadinimas";
const char* WIFI_PASS = "tinklo_slaptazodis";

const char* SERVER_IP = "serverio_ip";
const int SERVER_PORT = 5005;

WiFiUDP udp;

void setup() {
  Serial.begin(115200);

  WiFi.mode(WIFI_STA);
  WiFi.begin(WIFI_SSID, WIFI_PASS);

  Serial.print("Connecting to AP");
  while (WiFi.status() != WL_CONNECTED) {
    delay(500);
    Serial.print(".");
  }

  Serial.println("\nConnected!");
  Serial.print("ESP IP: ");
  Serial.println(WiFi.localIP());
}

void loop() {
  int n = WiFi.scanNetworks();
  if (n <= 0) {
    Serial.println("No networks found");
    delay(500);
    return;
  }

  StaticJsonDocument<1500> doc;
  JsonArray arr = doc.to<JsonArray>();

    for (int i = 0; i < n; i++) {
      JsonObject obj = arr.createNestedObject();

      obj["esp_id"] = ESP_ID;
      obj["ssid"] = WiFi.SSID(i);
      obj["mac"] = WiFi.BSSIDstr(i);
      obj["rssi"] = WiFi.RSSI(i);
  }

  char buffer[1500];
  size_t len = serializeJson(doc, buffer);

  udp.beginPacket(SERVER_IP, SERVER_PORT);
  udp.write((uint8_t*)buffer, len);
  udp.endPacket();

  Serial.println("Sent scan results:");
  Serial.println(buffer);

  delay(500);
}
