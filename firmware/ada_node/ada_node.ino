/*
  A.D.A. node: the ESP32 that replaces house_sim's pretend hardware.

  Step 1, no mains anywhere:
    - joins your Wi-Fi and reports to hub.py once a second (POST /device)
    - the BOOT button stands in for the power source: each press moves
      NEPA -> Gen -> Inverter -> No power -> NEPA ...
    - the relay module follows the loads the hub says should be on
    - if the hub stops answering for FAILSAFE_MS, every relay goes to its
      fail-safe state (pump and kettle off)
    - onboard LED: slow blink = talking to the hub, fast blink = Wi-Fi up but
      no hub, solid = no Wi-Fi

  Board: ESP32 Dev Module (WROOM-32). Arduino IDE with the "esp32" boards
  package by Espressif. No extra libraries needed.

  Fill in the CONFIG block, flash, then open Serial Monitor at 115200.
*/

#include <WiFi.h>
#include <HTTPClient.h>

// ============================== CONFIG ==============================
const char* WIFI_SSID   = "your-wifi-name";
const char* WIFI_PASS   = "your-wifi-password";
const char* HUB_HOST    = "192.168.1.50";   // the address hub.py prints after "(same wifi)"
const int   HUB_PORT    = 8000;
const char* DEVICE_KEY  = "paste-the-device-key-hub-prints";
const char* DEVICE_ID   = "ada-node-01";

const int   BUTTON_PIN  = 0;     // BOOT button on DevKit boards
const int   LED_PIN     = 2;     // onboard blue LED on most DevKit boards

// One line per relay channel: hub load name, GPIO, fail-safe state.
// Fail-safe is what the relay does if the hub disappears.
struct Relay { const char* load; int pin; bool failsafeOn; bool on; };
Relay RELAYS[] = {
  { "pump",   26, false, false },   // relay IN1
  { "kettle", 27, false, false },   // relay IN2
};
// Most cheap blue relay boards switch ON when the input is pulled LOW.
// If your relays are on when they should be off, set this to false.
const bool RELAY_ACTIVE_LOW = true;

const unsigned long REPORT_MS   = 1000;
const unsigned long FAILSAFE_MS = 10000;
// ====================================================================

const int RELAY_COUNT = sizeof(RELAYS) / sizeof(RELAYS[0]);
const char* SOURCES[] = { "grid", "gen", "inverter", "none" };
const char* SOURCE_NAMES[] = { "NEPA", "Gen", "Inverter", "No power" };
int sourceIndex = 0;

unsigned long lastReport = 0, lastHubOk = 0, lastButton = 0;
bool failsafeActive = false, hubOk = false;
int lastButtonState = HIGH;

void writeRelay(Relay& r, bool on) {
  r.on = on;
  digitalWrite(r.pin, (on ^ RELAY_ACTIVE_LOW) ? HIGH : LOW);
}

void applyFailsafe() {
  if (failsafeActive) return;
  failsafeActive = true;
  Serial.println("[failsafe] hub silent, relays to safe states");
  for (int i = 0; i < RELAY_COUNT; i++) writeRelay(RELAYS[i], RELAYS[i].failsafeOn);
}

// Reads "pump=1;kettle=0;..." from the hub's reply and sets matching relays.
void applyCmd(const String& body) {
  int k = body.indexOf("\"cmd\":\"");
  if (k < 0) return;
  int start = k + 7, end = body.indexOf('"', start);
  if (end < 0) return;
  String cmd = body.substring(start, end) + ";";
  for (int i = 0; i < RELAY_COUNT; i++) {
    String tag = String(RELAYS[i].load) + "=";
    int p = cmd.indexOf(tag);
    if (p < 0) continue;
    // match whole names only: start of string or after ';'
    if (p > 0 && cmd.charAt(p - 1) != ';') continue;
    bool want = cmd.charAt(p + tag.length()) == '1';
    if (want != RELAYS[i].on) {
      Serial.printf("[relay] %s -> %s\n", RELAYS[i].load, want ? "ON" : "OFF");
      writeRelay(RELAYS[i], want);
    }
  }
}

void report() {
  if (WiFi.status() != WL_CONNECTED) return;
  String json = "{\"id\":\"" + String(DEVICE_ID) + "\",\"source\":\"" + SOURCES[sourceIndex] + "\",\"relays\":{";
  for (int i = 0; i < RELAY_COUNT; i++) {
    json += "\"" + String(RELAYS[i].load) + "\":" + (RELAYS[i].on ? "true" : "false");
    if (i < RELAY_COUNT - 1) json += ",";
  }
  json += "},\"rssi\":" + String(WiFi.RSSI()) + ",\"uptime\":" + String(millis() / 1000) + "}";

  HTTPClient http;
  http.setTimeout(1500);
  http.begin(String("http://") + HUB_HOST + ":" + HUB_PORT + "/device");
  http.addHeader("Content-Type", "application/json");
  http.addHeader("X-Device-Key", DEVICE_KEY);
  int code = http.POST(json);
  if (code == 200) {
    String body = http.getString();
    if (!hubOk || failsafeActive) Serial.println("[hub] connected");
    hubOk = true; failsafeActive = false; lastHubOk = millis();
    applyCmd(body);
  } else {
    if (hubOk) Serial.printf("[hub] lost (%s)\n", code > 0 ? String(code).c_str() : http.errorToString(code).c_str());
    if (code == 403) Serial.println("[hub] 403: DEVICE_KEY doesn't match the key hub.py printed");
    hubOk = false;
  }
  http.end();
}

void ensureWifi() {
  static unsigned long lastTry = 0;
  if (WiFi.status() == WL_CONNECTED) return;
  if (millis() - lastTry < 5000 && lastTry != 0) return;
  lastTry = millis();
  Serial.printf("[wifi] connecting to %s ...\n", WIFI_SSID);
  WiFi.disconnect();
  WiFi.begin(WIFI_SSID, WIFI_PASS);
}

void checkButton() {
  int s = digitalRead(BUTTON_PIN);
  if (s == LOW && lastButtonState == HIGH && millis() - lastButton > 250) {
    lastButton = millis();
    sourceIndex = (sourceIndex + 1) % 4;
    Serial.printf("[source] %s\n", SOURCE_NAMES[sourceIndex]);
    report();                       // tell the hub straight away
    lastReport = millis();
  }
  lastButtonState = s;
}

void updateLed() {
  unsigned long period = WiFi.status() != WL_CONNECTED ? 0 : hubOk ? 1000 : 200;
  digitalWrite(LED_PIN, period == 0 ? HIGH : ((millis() / (period / 2)) % 2 ? HIGH : LOW));
}

void setup() {
  Serial.begin(115200);
  delay(200);
  Serial.println("\nA.D.A. node " + String(DEVICE_ID));
  pinMode(BUTTON_PIN, INPUT_PULLUP);
  pinMode(LED_PIN, OUTPUT);
  for (int i = 0; i < RELAY_COUNT; i++) {
    pinMode(RELAYS[i].pin, OUTPUT);
    writeRelay(RELAYS[i], RELAYS[i].failsafeOn);   // start safe until the hub speaks
  }
  WiFi.mode(WIFI_STA);
  ensureWifi();
  lastHubOk = millis();
}

void loop() {
  ensureWifi();
  static bool announced = false;
  if (WiFi.status() == WL_CONNECTED && !announced) {
    announced = true;
    Serial.print("[wifi] connected, my address is ");
    Serial.println(WiFi.localIP());
  } else if (WiFi.status() != WL_CONNECTED) {
    announced = false;
  }
  checkButton();
  if (millis() - lastReport >= REPORT_MS) { lastReport = millis(); report(); }
  if (millis() - lastHubOk > FAILSAFE_MS) applyFailsafe();
  updateLed();
  delay(10);
}
