// Run the same command dispatch and contact path used by the Mega sketch.
#include "transport.h"
#include <assert.h>
#include <iostream>
#include <sstream>
#include <string>

struct Stream {
  std::string incoming;
  std::string outgoing;
  size_t offset = 0;
  size_t available() const { return incoming.size() - offset; }
  int read() { return static_cast<unsigned char>(incoming[offset++]); }
  void feed(const std::string &data) { incoming += data; }
  template <typename T> void print(T value) { std::ostringstream text; text << value; outgoing += text.str(); }
  template <typename T> void println(T value) { print(value); outgoing += '\n'; }
  void clear() { outgoing.clear(); }
};

struct Body {
  bool measuring = false;
  bool playing = false;
  bool sensor = true;
  unsigned stops = 0;
  unsigned measurements = 0;
  unsigned faces = 0;
  std::string expression = "neutral";
  respaw::Transport audioSource = respaw::NoController;
  Stream *faceOutput = nullptr;
  bool ackWasAlreadySent = false;
  void stop() { ++stops; measuring = playing = false; expression = "neutral"; }
  bool busy() const { return measuring || playing; }
  RespawText measure() {
    if (!sensor) return "sensor_unavailable";
    if (busy()) return "busy";
    ++measurements; measuring = true;
    return nullptr;
  }
  RespawText face(const char *value) {
    if (measuring) return "busy";
    if (faceOutput) ackWasAlreadySent = faceOutput->outgoing.find("\"type\":\"ack\"") != std::string::npos;
    ++faces; expression = value;
    return nullptr;
  }
  RespawText play(uint8_t, respaw::Transport source) {
    if (measuring) return "busy";
    playing = true; audioSource = source;
    return nullptr;
  }
};

void drain(respaw::CommandRouter &router, respaw::Transport port, Stream &stream, Body &body, uint32_t now) {
  while (stream.available()) router.poll(port, stream, body, now);
}

void command(respaw::CommandRouter &router, respaw::Transport port, Stream &stream, Body &body,
             uint32_t now, const std::string &line) {
  stream.feed(line);
  drain(router, port, stream, body, now);
}

int main() {
  using namespace respaw;
  Stream usb, uart;
  Body body;
  CommandRouter router;

  command(router, Uart, uart, body, 0, "V1 9 FACE warm\n");
  assert(uart.outgoing == "{\"v\":1,\"type\":\"error\",\"id\":9,\"reason\":\"controller_required\"}\n");
  assert(usb.outgoing.empty() && body.faces == 0);
  uart.clear();
  uart.feed("V1 22 PI"); drain(router, Uart, uart, body, 10);
  command(router, Usb, usb, body, 10, "V1 1 PING\r\n");
  assert(router.owner == Usb);
  assert(usb.outgoing == "{\"v\":1,\"type\":\"ack\",\"id\":1,\"command\":\"PING\"}\n");
  command(router, Uart, uart, body, 11, "NG\n");
  assert(uart.outgoing == "{\"v\":1,\"type\":\"error\",\"id\":22,\"reason\":\"controller_busy\"}\n");
  assert(router.lastPing == 10);

  usb.clear(); uart.clear();
  body.faceOutput = &usb;
  command(router, Usb, usb, body, 12, "V1 2 FACE warm\n");
  assert(body.faces == 1 && body.expression == "warm" && !body.ackWasAlreadySent);
  assert(usb.outgoing == "{\"v\":1,\"type\":\"ack\",\"id\":2,\"command\":\"FACE\"}\n");
  assert(uart.outgoing.empty());
  command(router, Uart, uart, body, 13, "V1 3 FACE sleeping\n");
  assert(body.faces == 1 && uart.outgoing.find("controller_busy") != std::string::npos);
  uart.clear();
  command(router, Uart, uart, body, 14, "V1 4 STOP\n");
  assert(body.stops == 1 && body.expression == "neutral" && router.owner == Usb);
  assert(uart.outgoing == "{\"v\":1,\"type\":\"ack\",\"id\":4,\"command\":\"STOP\"}\n");

  usb.clear(); uart.clear();
  command(router, Usb, usb, body, 15, "V1 5 MEASURE\n");
  assert(body.measuring && body.measurements == 1);
  assert(usb.outgoing.find("\"command\":\"MEASURE\"") != std::string::npos && uart.outgoing.empty());
  usb.clear();
  command(router, Usb, usb, body, 16, "V1 6 FACE thinking\n");
  assert(usb.outgoing.find("\"reason\":\"busy\"") != std::string::npos && body.faces == 1);
  uart.clear();
  command(router, Uart, uart, body, 17, "V1 7 STOP\n");
  assert(!body.measuring && body.stops == 2);
  assert(uart.outgoing.find("\"command\":\"STOP\"") != std::string::npos);

  usb.clear(); uart.clear();
  const char *invalid[] = {"V1 65536 STOP\n", "V1 0 STOP\n", "V1 -1 STOP\n", "V2 9 STOP\n",
                           "V1 9 PING extra\n", "V1 9 FACE invalid\n", "V1 9 PLAY 10\n", "V1 9 EXEC x\n"};
  for (const char *line : invalid) command(router, Uart, uart, body, 20, line);
  assert(body.stops == 2 && body.faces == 1 && usb.outgoing.empty());
  assert(uart.outgoing.find("bad_id") != std::string::npos && uart.outgoing.find("unknown_command") != std::string::npos);
  uart.clear();
  command(router, Uart, uart, body, 20, std::string(100, 'x') + "V1 9 STOP\n");
  assert(body.stops == 2);
  assert(uart.outgoing == "{\"v\":1,\"type\":\"error\",\"id\":0,\"reason\":\"line_too_long\"}\n");
  uart.clear();
  command(router, Uart, uart, body, 21, "V1 10 STOP\n");
  assert(body.stops == 3 && uart.outgoing.find("\"id\":10") != std::string::npos);
  uart.clear(); usb.clear();
  router.tick(6009, body, usb, uart);
  assert(router.owner == Usb && body.stops == 3);
  router.tick(6010, body, usb, uart);
  assert(router.owner == NoController && body.stops == 4);
  assert(usb.outgoing == "{\"v\":1,\"type\":\"error\",\"id\":0,\"reason\":\"host_timeout\"}\n" && uart.outgoing.empty());
  router.tick(9000, body, usb, uart);
  assert(body.stops == 4);

  usb.clear(); uart.clear(); body.faceOutput = &uart;
  command(router, Uart, uart, body, 9000, "V1 20 PING\n");
  assert(router.owner == Uart);
  uart.clear();
  command(router, Uart, uart, body, 9001, "V1 21 FACE listening\nV1 22 PLAY 2\n");
  assert(body.expression == "listening" && body.audioSource == Uart && body.playing);
  assert(uart.outgoing.find("\"command\":\"FACE\"") != std::string::npos && uart.outgoing.find("\"command\":\"PLAY\"") != std::string::npos);
  assert(usb.outgoing.empty());
  uart.clear();
  router.lastPing = UINT32_MAX - 3000UL;
  router.tick(2998, body, usb, uart);
  assert(router.owner == Uart);
  router.tick(2999, body, usb, uart);
  assert(router.owner == NoController && !body.playing);
  assert(uart.outgoing.find("host_timeout") != std::string::npos && usb.outgoing.empty());

  // Contact emits one stable edge per press/release to both listeners. During
  // remote control it never starts a physiological capture as a side effect.
  CommandRouter controlled;
  controlled.owner = Uart;
  ContactEdges contact;
  contact.begin(100, 0);
  Body contactBody;
  usb.clear(); uart.clear();
  tickContact(contact, 500, 100, controlled, contactBody, usb, uart);
  tickContact(contact, 500, 139, controlled, contactBody, usb, uart);
  assert(usb.outgoing.empty());
  tickContact(contact, 500, 140, controlled, contactBody, usb, uart);
  assert(usb.outgoing == "{\"v\":1,\"type\":\"contact\",\"sensor\":\"fsr_a8\",\"pressed\":true,\"uptime_ms\":140}\n");
  assert(uart.outgoing == usb.outgoing && contactBody.measurements == 0);
  usb.clear(); uart.clear();
  tickContact(contact, 275, 200, controlled, contactBody, usb, uart);
  tickContact(contact, 500, 500, controlled, contactBody, usb, uart);
  assert(usb.outgoing.empty());
  tickContact(contact, 100, 600, controlled, contactBody, usb, uart);
  tickContact(contact, 100, 640, controlled, contactBody, usb, uart);
  assert(usb.outgoing.find("\"pressed\":false") != std::string::npos && uart.outgoing == usb.outgoing);

  // Offline legacy behavior remains available once no controller owns it.
  controlled.owner = NoController;
  usb.clear(); uart.clear();
  tickContact(contact, 500, 700, controlled, contactBody, usb, uart);
  tickContact(contact, 500, 740, controlled, contactBody, usb, uart);
  assert(contactBody.measurements == 1 && contactBody.measuring);
  contactBody.measuring = false;
  tickContact(contact, 500, 1000, controlled, contactBody, usb, uart);
  assert(contactBody.measurements == 1);
  ContactEdges wrapped;
  wrapped.begin(0, UINT32_MAX - 30);
  assert(!wrapped.sample(500, UINT32_MAX - 20));
  assert(!wrapped.sample(500, 18));
  assert(wrapped.sample(500, 19));

  // Machine-readable production examples are checked by the Python fixture.
  Stream examples;
  writeReady(examples, false, false, true);
  writeHeartbeat(examples, false, false, false, 123, true);
  writeContact(examples, true, 124);
  writeContact(examples, false, 125);
  writeAck(examples, 42, "FACE");
  writeError(examples, 43, "controller_busy");
  std::cout << examples.outgoing;
}
