#pragma once

#include "core.h"

#ifdef ARDUINO
#include <Arduino.h>
#define RESPAW_TEXT(value) F(value)
typedef const __FlashStringHelper *RespawText;
#else
#define RESPAW_TEXT(value) value
typedef const char *RespawText;
#endif

namespace respaw {

template <typename Output>
void writeReady(Output &out, bool sensor, bool audio) {
  out.print(RESPAW_TEXT("{\"v\":1,\"type\":\"ready\",\"board\":\"mega2560\",\"sensor\":"));
  out.print(sensor ? RESPAW_TEXT("true") : RESPAW_TEXT("false"));
  out.print(RESPAW_TEXT(",\"audio\":"));
  out.print(audio ? RESPAW_TEXT("true") : RESPAW_TEXT("false"));
  out.println('}');
}

template <typename Output>
void writeHeartbeat(Output &out, bool sensor, bool audio, bool measuring, uint32_t uptime) {
  out.print(RESPAW_TEXT("{\"v\":1,\"type\":\"heartbeat\",\"board\":\"mega2560\",\"uptime_ms\":"));
  out.print(uptime);
  out.print(RESPAW_TEXT(",\"sensor\":"));
  out.print(sensor ? RESPAW_TEXT("true") : RESPAW_TEXT("false"));
  out.print(RESPAW_TEXT(",\"audio\":"));
  out.print(audio ? RESPAW_TEXT("true") : RESPAW_TEXT("false"));
  out.print(RESPAW_TEXT(",\"measuring\":"));
  out.print(measuring ? RESPAW_TEXT("true") : RESPAW_TEXT("false"));
  out.println('}');
}

// The caller supplies only fixed firmware reason strings, never serial input.
template <typename Output>
void writeMeasurement(Output &out, const RRStats &stats, bool valid, uint32_t duration, RespawText reason) {
  out.print(RESPAW_TEXT("{\"v\":1,\"type\":\"measurement\",\"valid\":"));
  out.print(valid ? RESPAW_TEXT("true") : RESPAW_TEXT("false"));
  out.print(RESPAW_TEXT(",\"rr_count\":")); out.print(stats.count);
  out.print(RESPAW_TEXT(",\"rejected\":")); out.print(stats.rejected);
  out.print(RESPAW_TEXT(",\"rmssd_pairs\":")); out.print(stats.pairs);
  out.print(RESPAW_TEXT(",\"motion_checked\":false,\"window_ms\":")); out.print(duration);
  if (valid) {
    out.print(RESPAW_TEXT(",\"bpm\":")); out.print(stats.bpm(), 2);
    out.print(RESPAW_TEXT(",\"sdnn\":")); out.print(stats.sdnn(), 2);
    out.print(RESPAW_TEXT(",\"rmssd\":")); out.print(stats.rmssd(), 2);
  } else {
    out.print(RESPAW_TEXT(",\"reason\":\""));
    out.print(reason ? reason : RESPAW_TEXT("insufficient_signal")); out.print('"');
  }
  out.println('}');
}

}  // namespace respaw

#undef RESPAW_TEXT
