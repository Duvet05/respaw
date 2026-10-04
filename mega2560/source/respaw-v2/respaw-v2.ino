/* ResPaw v2: Mega controls the original TFT, FSR, MAX30102 and DFPlayer.
 * The connected server owns conversation. The recovered sketch is unmodified.
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
#include "transport.h"

constexpr uint8_t FSR_PIN = A8;
constexpr uint8_t BUSY_PIN = A13;
constexpr uint8_t DF_RX = A15;
constexpr uint8_t DF_TX = A14;
constexpr uint32_t WINDOW_MS = 30000UL;
constexpr uint32_t AUDIO_LIMIT_MS = 240000UL;
constexpr uint16_t BLACK = 0x0000;
constexpr uint16_t CYAN = 0x07FF;

MAX30105 sensor;
MCUFRIEND_kbv display;
SoftwareSerial audioSerial(DF_RX, DF_TX);
DFRobotDFPlayerMini audio;
respaw::RRStats stats;
respaw::BeatDetector detector;
respaw::CommandRouter commands;
respaw::ContactEdges contact;

bool sensorReady = false;
bool audioReady = false;
bool measuring = false;
bool playing = false;
bool audioWasBusy = false;
bool contactLost = false;
uint32_t windowStart = 0;
uint32_t sampleClock = 0;
uint32_t lastSample = 0;
uint32_t contactLostAt = 0;
uint32_t audioStart = 0;
uint32_t lastTelemetry = 0;
respaw::Transport audioTransport = respaw::Usb;

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

RespawText startMeasurement() {
  if (!sensorReady) return F("sensor_unavailable");
  if (measuring || playing) return F("busy");
  if (!pressed()) return F("no_contact");
  // Drawing comes before FIFO reset, avoiding a rendering gap in this window.
  drawFace("listening");
  respaw::writeHeartbeat(Serial1, sensorReady, audioReady, true, millis(), true);
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
  return nullptr;
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
    if (audioTransport == respaw::Usb) respaw::writeError(Serial, 0, F("audio_timeout"));
    else respaw::writeError(Serial1, 0, F("audio_timeout"));
  } else if (audioWasBusy && !busy) {
    playing = false;
    if (audioTransport == respaw::Usb) respaw::writeAnimationDone(Serial);
    else respaw::writeAnimationDone(Serial1);
  }
}

struct BodyControls {
  void stop() { stopAll(); }
  bool busy() const { return measuring || playing; }
  RespawText measure() { return startMeasurement(); }
  RespawText face(const char *expression) {
    if (measuring) return F("busy");
    drawFace(expression);
    return nullptr;
  }
  RespawText play(uint8_t track, respaw::Transport source) {
    if (!audioReady) return F("audio_unavailable");
    if (measuring) return F("busy");
    audio.play(track); playing = true; audioWasBusy = false;
    audioTransport = source;
    audioStart = millis();
    return nullptr;
  }
} body;

void setup() {
  Serial.begin(115200);
  Serial1.begin(115200);  // D18 TX1 -> adapted GP1 RX; GP0 TX -> D19 RX1, common GND.
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
  const uint32_t now = millis();
  contact.begin(analogRead(FSR_PIN), now);
  respaw::writeReady(Serial, sensorReady, audioReady, true);
  respaw::writeReady(Serial1, sensorReady, audioReady, true);
  respaw::writeContact(Serial, contact.pressed, now);
  respaw::writeContact(Serial1, contact.pressed, now);
}

void loop() {
  // Independent bounded buffers prevent interleaved USB/UART fragments from
  // becoming one command. Only the owner controls actions; either may STOP.
  const uint32_t beforeCommands = millis();
  commands.tick(beforeCommands, body, Serial, Serial1);
  commands.poll(respaw::Usb, Serial, body, beforeCommands);
  commands.poll(respaw::Uart, Serial1, body, beforeCommands);
  const uint32_t now = millis();
  tickMeasurement(now);
  tickAudio(now);
  // Avoid filling the serial TX buffer during PPG capture: a heartbeat may
  // block for several milliseconds at 115200 baud. The receiver allows the
  // bounded 30-second window after a heartbeat that announces measuring=true.
  if (respaw::elapsed(now, lastTelemetry, 2000UL) && !measuring) {
    respaw::writeHeartbeat(Serial1, sensorReady, audioReady, false, now, true);
    lastTelemetry = now;
  }
  respaw::tickContact(contact, analogRead(FSR_PIN), now, commands, body, Serial, Serial1);
}
