#pragma once

#include <stdlib.h>
#include <string.h>
#include "telemetry.h"

#ifdef ARDUINO
#define RESPAW_COMMAND_TEXT(value) F(value)
#else
#define RESPAW_COMMAND_TEXT(value) value
#endif

namespace respaw {

enum Transport : uint8_t { NoController = 0, Usb = 1, Uart = 2 };

inline bool knownFace(const char *name) {
  return !strcmp(name, "neutral") || !strcmp(name, "warm") || !strcmp(name, "listening") ||
         !strcmp(name, "thinking") || !strcmp(name, "sleeping");
}

// The sketch and host fixture use the same bounded parser, ownership rules and
// dispatch. Body methods return fixed errors after checking actual peripherals.
struct CommandRouter {
  static const uint32_t TimeoutMs = 6000UL;
  LineBuffer inputs[2];
  Transport owner = NoController;
  uint32_t lastPing = 0;

  template <typename Body, typename Output>
  void dispatch(Transport source, char *line, Output &out, Body &body, uint32_t now) {
    char *save = nullptr;
    char *version = strtok_r(line, " ", &save);
    char *idText = strtok_r(nullptr, " ", &save);
    char *command = strtok_r(nullptr, " ", &save);
    char *argument = strtok_r(nullptr, " ", &save);
    char *extra = strtok_r(nullptr, " ", &save);
    if (!version || strcmp(version, "V1") || !idText || !command || extra) {
      writeError(out, 0, RESPAW_COMMAND_TEXT("bad_command")); return;
    }
    for (const char *p = idText; *p; ++p) {
      if (*p < '0' || *p > '9') { writeError(out, 0, RESPAW_COMMAND_TEXT("bad_id")); return; }
    }
    const unsigned long parsed = strtoul(idText, nullptr, 10);
    if (!parsed || parsed > 65535UL) { writeError(out, 0, RESPAW_COMMAND_TEXT("bad_id")); return; }
    const uint16_t id = static_cast<uint16_t>(parsed);
    const bool ping = !strcmp(command, "PING"), stop = !strcmp(command, "STOP");
    const bool measure = !strcmp(command, "MEASURE"), face = !strcmp(command, "FACE");
    const bool play = !strcmp(command, "PLAY");
    if (!ping && !stop && !measure && !face && !play) {
      writeError(out, id, RESPAW_COMMAND_TEXT("unknown_command")); return;
    }
    if ((ping || stop || measure) && argument) {
      writeError(out, id, RESPAW_COMMAND_TEXT("unexpected_argument")); return;
    }
    if (face && (!argument || !knownFace(argument))) {
      writeError(out, id, RESPAW_COMMAND_TEXT("bad_face")); return;
    }
    if (play && (!argument || strlen(argument) != 1 || argument[0] < '1' || argument[0] > '9')) {
      writeError(out, id, RESPAW_COMMAND_TEXT("bad_track")); return;
    }
    if (stop) {
      // Either transport may stop the body without stealing its controller.
      body.stop(); writeAck(out, id, RESPAW_COMMAND_TEXT("STOP")); return;
    }
    if (owner != NoController && owner != source) {
      writeError(out, id, RESPAW_COMMAND_TEXT("controller_busy")); return;
    }
    if (ping) {
      owner = source; lastPing = now;
      writeAck(out, id, RESPAW_COMMAND_TEXT("PING")); return;
    }
    if (owner == NoController) {
      writeError(out, id, RESPAW_COMMAND_TEXT("controller_required")); return;
    }
    RespawText error = nullptr;
    if (measure) error = body.measure();
    else if (face) error = body.face(argument);
    else if (play) error = body.play(static_cast<uint8_t>(argument[0] - '0'), source);
    if (error) { writeError(out, id, error); return; }
    writeAck(out, id, measure ? RESPAW_COMMAND_TEXT("MEASURE") :
                      face ? RESPAW_COMMAND_TEXT("FACE") : RESPAW_COMMAND_TEXT("PLAY"));
  }

  template <typename Stream, typename Body>
  void poll(Transport source, Stream &stream, Body &body, uint32_t now, uint8_t budget = 48) {
    LineBuffer &input = inputs[source == Usb ? 0 : 1];
    for (uint8_t count = 0; count < budget && stream.available(); ++count) {
      if (input.feed(static_cast<char>(stream.read()))) {
        if (input.overflow) writeError(stream, 0, RESPAW_COMMAND_TEXT("line_too_long"));
        else dispatch(source, input.data, stream, body, now);
        input.reset();
      }
    }
  }

  template <typename Body, typename UsbOutput, typename UartOutput>
  void tick(uint32_t now, Body &body, UsbOutput &usb, UartOutput &uart) {
    if (owner == NoController || !elapsed(now, lastPing, TimeoutMs)) return;
    const Transport expired = owner;
    owner = NoController;
    body.stop();
    if (expired == Usb) writeError(usb, 0, RESPAW_COMMAND_TEXT("host_timeout"));
    else writeError(uart, 0, RESPAW_COMMAND_TEXT("host_timeout"));
  }
};

struct ContactEdges {
  static const uint32_t DebounceMs = 40;
  bool pressed = false;
  bool candidate = false;
  uint32_t changedAt = 0;

  void begin(uint16_t reading, uint32_t now) {
    pressed = candidate = reading > 300;
    changedAt = now;
  }

  bool sample(uint16_t reading, uint32_t now) {
    const bool down = reading > 300 ? true : reading < 250 ? false : candidate;
    if (down != candidate) { candidate = down; changedAt = now; }
    if (candidate == pressed || !elapsed(now, changedAt, DebounceMs)) return false;
    pressed = candidate;
    return true;
  }
};

// Broadcasting state is separate from directing a command response. While
// controlled, contact becomes an event and never forces a 30-second capture.
template <typename Body, typename UsbOutput, typename UartOutput>
void tickContact(ContactEdges &contact, uint16_t reading, uint32_t now,
                 const CommandRouter &commands, Body &body, UsbOutput &usb, UartOutput &uart) {
  if (!contact.sample(reading, now)) return;
  writeContact(usb, contact.pressed, now);
  writeContact(uart, contact.pressed, now);
  if (contact.pressed && commands.owner == NoController && !body.busy()) {
    const RespawText error = body.measure();
    if (error) {
      writeError(usb, 0, error);
      writeError(uart, 0, error);
    }
  }
}

}  // namespace respaw

#undef RESPAW_COMMAND_TEXT
