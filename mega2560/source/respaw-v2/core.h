#pragma once

#include <math.h>
#include <stdint.h>
#include <stddef.h>

namespace respaw {

inline bool elapsed(uint32_t now, uint32_t start, uint32_t duration) {
  return static_cast<uint32_t>(now - start) >= duration;
}

// Online statistics remove the old 50-element buffer and bound the window.
struct RRStats {
  uint16_t count = 0;
  uint16_t rejected = 0;
  uint16_t pairs = 0;
  bool overflow = false;
  bool adjacent = false;
  float mean = 0;
  float m2 = 0;
  float squaredDifferences = 0;
  uint32_t previous = 0;

  void reset() { *this = RRStats(); }

  bool add(uint32_t interval) {
    if (interval <= 300 || interval >= 1500) {
      if (rejected < UINT16_MAX) ++rejected;
      adjacent = false;  // Do not create a successive pair across discarded beats.
      return false;
    }
    if (count >= 128) {
      overflow = true;
      return false;
    }
    ++count;
    const float delta = interval - mean;
    mean += delta / count;
    m2 += delta * (interval - mean);
    if (adjacent) {
      const float difference = static_cast<float>(interval) - previous;
      squaredDifferences += difference * difference;
      ++pairs;
    }
    previous = interval;
    adjacent = true;
    return true;
  }

  bool valid() const {
    return !overflow && count >= 10 && pairs >= 9 && rejected <= count / 4;
  }
  float bpm() const { return count ? 60000.0f / mean : 0.0f; }
  float sdnn() const { return count > 1 ? sqrtf(m2 / (count - 1)) : 0.0f; }
  float rmssd() const { return pairs ? sqrtf(squaredDifferences / pairs) : 0.0f; }
};

struct BeatDetector {
  uint32_t buffer[8] = {};
  uint8_t index = 0;
  uint8_t filled = 0;
  uint32_t minimum = UINT32_MAX;
  uint32_t maximum = 0;
  uint32_t last = 0;
  uint32_t lastBeat = 0;
  bool rising = false;
  bool haveBeat = false;

  void reset() { *this = BeatDetector(); }

  bool sample(uint32_t ir, uint32_t timestamp, uint32_t &interval) {
    buffer[index] = ir;
    index = (index + 1) % 8;
    if (filled < 8) ++filled;
    uint32_t smooth = 0;
    for (uint8_t i = 0; i < filled; ++i) smooth += buffer[i];
    smooth /= filled;
    if (filled < 8) { last = smooth; return false; }
    if (smooth < minimum) minimum = smooth;
    if (smooth > maximum) maximum = smooth;
    const uint32_t threshold = minimum + (maximum - minimum) * 3UL / 5UL;
    bool beat = false;
    if (smooth > threshold && !rising && smooth > last) rising = true;
    if (rising && smooth < last) {
      if (haveBeat) {
        interval = timestamp - lastBeat;
        beat = true;
      }
      lastBeat = timestamp;
      haveBeat = true;
      rising = false;
    }
    last = smooth;
    return beat;
  }
};

// Overflow discards the entire frame through newline; never interpret its suffix.
struct LineBuffer {
  char data[80] = {};
  uint8_t length = 0;
  bool overflow = false;

  bool feed(char value) {
    if (value == '\r') return false;
    if (value == '\n') {
      data[length] = '\0';
      return true;
    }
    if (value < 32 || value > 126 || length >= sizeof(data) - 1) {
      overflow = true;
    } else if (!overflow) {
      data[length++] = value;
    }
    return false;
  }
  void reset() { length = 0; overflow = false; data[0] = '\0'; }
};

}  // namespace respaw
