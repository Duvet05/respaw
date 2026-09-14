/* ResPaw v2: Mega controls the original TFT, FSR, MAX30102 and DFPlayer.
 * The Mac owns conversation. The recovered sketch remains unmodified.
 * No emotion is diagnosed from a physiological window.
 */
#include <Wire.h>
#include <MAX30105.h>
#include <Adafruit_GFX.h>
#include <MCUFRIEND_kbv.h>
#include <DFRobotDFPlayerMini.h>
#include <SoftwareSerial.h>
#include <stdlib.h>
#include <string.h>
#include "core.h"
#include "telemetry.h"

constexpr uint8_t FSR_PIN = A8;
constexpr uint8_t BUSY_PIN = A13;
constexpr uint8_t DF_RX = A15;
constexpr uint8_t DF_TX = A14;
constexpr uint32_t WINDOW_MS = 30000UL;
constexpr uint32_t AUDIO_LIMIT_MS = 240000UL;
constexpr uint32_t HOST_TIMEOUT_MS = 6000UL;
constexpr uint16_t BLACK = 0x0000;
constexpr uint16_t CYAN = 0x07FF;

MAX30105 sensor;
MCUFRIEND_kbv display;
SoftwareSerial audioSerial(DF_RX, DF_TX);
DFRobotDFPlayerMini audio;
respaw::RRStats stats;
respaw::BeatDetector detector;
respaw::LineBuffer input;

bool sensorReady = false;
bool audioReady = false;
bool measuring = false;
bool playing = false;
bool audioWasBusy = false;
bool contactWasDown = false;
bool contactLost = false;
bool hostSeen = false;
uint32_t windowStart = 0;
uint32_t sampleClock = 0;
uint32_t lastSample = 0;
uint32_t contactLostAt = 0;
uint32_t audioStart = 0;
uint32_t lastHost = 0;
uint32_t lastTelemetry = 0;

void prefix(const __FlashStringHelper *type) {
  Serial.print(F("{\"v\":1,\"type\":\""));
  Serial.print(type);
  Serial.print('"');
}

void errorEvent(uint16_t id, const __FlashStringHelper *reason) {
  prefix(F("error"));
  Serial.print(F(",\"id\":")); Serial.print(id);
  Serial.print(F(",\"reason\":\"")); Serial.print(reason); Serial.println(F("\"}"));
}

void ack(uint16_t id) {
  prefix(F("ack")); Serial.print(F(",\"id\":")); Serial.print(id); Serial.println('}');
}

void drawFace(const char *expression) {
  const int cx = display.width() / 2, cy = display.height() / 2;
  int width = 100, height = 140;
  if (!strcmp(expression, "sleeping")) height = 12;
  else if (!strcmp(expression, "warm")) height = 85;
  else if (!strcmp(expression, "thinking")) width = 80;
  display.fillScreen(BLACK);
  display.fillRoundRect(cx - 110 - width / 2, cy - height / 2, width, height, 20, CYAN);
  display.fillRoundRect(cx + 110 - width / 2, cy - height / 2, width, height, 20, CYAN);
}

bool knownFace(const char *name) {
  return !strcmp(name, "neutral") || !strcmp(name, "warm") || !strcmp(name, "listening") ||
         !strcmp(name, "thinking") || !strcmp(name, "sleeping");
}

bool pressed() { return analogRead(FSR_PIN) > 300; }

void finishMeasurement(const __FlashStringHelper *reason = nullptr) {
  measuring = false;
  const bool valid = reason == nullptr && stats.valid() && detector.maximum > detector.minimum + 100;
  const uint32_t duration = millis() - windowStart;
  respaw::writeMeasurement(Serial, stats, valid, duration, reason);
  respaw::writeMeasurement(Serial1, stats, valid, duration, reason);
  drawFace("neutral");
}

void stopAll() {
  if (measuring) finishMeasurement(F("cancelled"));
  if (audioReady && playing) audio.stop();
  playing = false;
  drawFace("neutral");
}

bool startMeasurement(uint16_t id) {
  if (!sensorReady) { errorEvent(id, F("sensor_unavailable")); return false; }
  if (measuring || playing) { errorEvent(id, F("busy")); return false; }
  if (!pressed()) { errorEvent(id, F("no_contact")); return false; }
  // Drawing comes before FIFO reset, avoiding a rendering gap in this window.
  drawFace("listening");
  respaw::writeHeartbeat(Serial1, sensorReady, audioReady, true, millis());
  Serial1.flush();  // Finish announcing the window before sampling starts.
  lastTelemetry = millis();
  stats.reset();
  detector.reset();
  sensor.clearFIFO();
  while (sensor.available()) sensor.nextSample();
  sampleClock = 0;
  windowStart = lastSample = millis();
  contactLost = false;
  measuring = true;
  return true;
}

void tickMeasurement(uint32_t now) {
  if (!measuring) return;
  if (!pressed()) {
    if (!contactLost) { contactLost = true; contactLostAt = now; }
    if (respaw::elapsed(now, contactLostAt, 100UL)) { finishMeasurement(F("contact_lost")); return; }
  } else contactLost = false;

  // SparkFun's software FIFO retains at most 3 unread samples. Reject gaps.
  const uint16_t received = sensor.check();
  if (received > 3) { finishMeasurement(F("fifo_gap")); return; }
  while (sensor.available()) {
    const uint32_t ir = sensor.getFIFOIR();
    sensor.nextSample();
    lastSample = now;
    sampleClock += 10UL;  // Explicit 100 Hz, FIFO averaging disabled.
    if (ir >= 260000UL) { finishMeasurement(F("saturated")); return; }
    uint32_t interval = 0;
    if (detector.sample(ir, sampleClock, interval)) stats.add(interval);
  }
  if (respaw::elapsed(now, lastSample, 250UL)) { finishMeasurement(F("sensor_stalled")); return; }
  if (respaw::elapsed(now, windowStart, WINDOW_MS)) finishMeasurement();
}

void tickAudio(uint32_t now) {
  if (!playing) return;
  const bool busy = digitalRead(BUSY_PIN) == LOW;
  if (busy) audioWasBusy = true;
  if (respaw::elapsed(now, audioStart, AUDIO_LIMIT_MS) ||
      (!audioWasBusy && respaw::elapsed(now, audioStart, 2000UL))) {
    audio.stop(); playing = false;
    errorEvent(0, F("audio_timeout"));
  } else if (audioWasBusy && !busy) {
    playing = false;
    prefix(F("animation_done")); Serial.println('}');
  }
}

void processCommand() {
  char *save = nullptr;
  char *version = strtok_r(input.data, " ", &save);
  char *idText = strtok_r(nullptr, " ", &save);
  char *command = strtok_r(nullptr, " ", &save);
  char *argument = strtok_r(nullptr, " ", &save);
  char *extra = strtok_r(nullptr, " ", &save);
  if (!version || strcmp(version, "V1") || !idText || !command || extra) {
    errorEvent(0, F("bad_command")); return;
  }
  for (const char *p = idText; *p; ++p) {
    if (*p < '0' || *p > '9') { errorEvent(0, F("bad_id")); return; }
  }
  const unsigned long parsed = strtoul(idText, nullptr, 10);
  if (!parsed || parsed > 65535UL) { errorEvent(0, F("bad_id")); return; }
  const uint16_t id = static_cast<uint16_t>(parsed);
  if ((!strcmp(command, "PING") || !strcmp(command, "STOP") || !strcmp(command, "MEASURE")) && argument) {
    errorEvent(id, F("unexpected_argument")); return;
  }
  if (!strcmp(command, "PING")) {
    hostSeen = true; lastHost = millis(); ack(id);
  } else if (!strcmp(command, "STOP")) {
    stopAll(); ack(id);
  } else if (!strcmp(command, "MEASURE")) {
    if (startMeasurement(id)) ack(id);
  } else if (!strcmp(command, "FACE")) {
    if (!argument || !knownFace(argument)) { errorEvent(id, F("bad_face")); return; }
    if (measuring) { errorEvent(id, F("busy")); return; }
    drawFace(argument); ack(id);
  } else if (!strcmp(command, "PLAY")) {
    if (!argument || strlen(argument) != 1 || argument[0] < '1' || argument[0] > '9') {
      errorEvent(id, F("bad_track")); return;
    }
    if (!audioReady) { errorEvent(id, F("audio_unavailable")); return; }
    if (measuring) { errorEvent(id, F("busy")); return; }
    audio.play(argument[0] - '0'); playing = true; audioWasBusy = false;
    audioStart = millis(); ack(id);
  } else errorEvent(id, F("unknown_command"));
}

void setup() {
  Serial.begin(115200);
  Serial1.begin(115200);  // TX1/D18 -> level shifter -> Pico GP1; receive only at Pico.
  pinMode(FSR_PIN, INPUT);
  pinMode(BUSY_PIN, INPUT_PULLUP);
  Wire.begin();
  Wire.setWireTimeout(3000UL, true);
  uint16_t id = display.readID();
  if (id == 0xD3D3) id = 0x9488;
  display.begin(id); display.setRotation(1); drawFace("neutral");
  audioSerial.begin(9600);
  audio.setTimeOut(300);
  audioReady = audio.begin(audioSerial, true, true);
  // Rebind without reset to disable ACK through the library's public API.
  // Preserve the real probe result; begin(..., false, false) always returns true.
  audio.begin(audioSerial, false, false);
  if (audioReady) audio.volume(20);
  sensorReady = sensor.begin(Wire, I2C_SPEED_STANDARD);
  if (sensorReady) {
    sensor.setup(0x1F, 1, 2, 100, 411, 16384);
    sensor.setPulseAmplitudeRed(0x0A);
  }
  contactWasDown = pressed();
  respaw::writeReady(Serial, sensorReady, audioReady);
  respaw::writeReady(Serial1, sensorReady, audioReady);
}

void loop() {
  // Bound parsing work so sensor acquisition also runs under sustained input.
  for (uint8_t i = 0; i < 48 && Serial.available(); ++i) {
    if (input.feed(static_cast<char>(Serial.read()))) {
      if (input.overflow) errorEvent(0, F("line_too_long"));
      else processCommand();
      input.reset();
    }
  }
  const uint32_t now = millis();
  tickMeasurement(now);
  tickAudio(now);
  // Avoid filling the serial TX buffer during PPG capture: a heartbeat may
  // block for several milliseconds at 115200 baud. The receiver allows the
  // bounded 30-second window after a heartbeat that announces measuring=true.
  if (respaw::elapsed(now, lastTelemetry, 2000UL) && !measuring) {
    respaw::writeHeartbeat(Serial1, sensorReady, audioReady, false, now);
    lastTelemetry = now;
  }
  const bool down = pressed();
  if (down && !contactWasDown && !measuring && !playing) startMeasurement(0);
  contactWasDown = down;
  if (hostSeen && respaw::elapsed(now, lastHost, HOST_TIMEOUT_MS)) {
    hostSeen = false;
    stopAll();
    errorEvent(0, F("host_timeout"));
  }
}
