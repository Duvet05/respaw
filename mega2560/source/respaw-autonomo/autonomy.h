#pragma once

#include <stdint.h>

namespace respaw_auto {

constexpr uint8_t PROFILE_COUNT = 3;
constexpr uint8_t ACTIVITY_COUNT = 3;
constexpr uint8_t NO_ACTIVITY = 255;

inline bool elapsed(uint32_t now, uint32_t start, uint32_t duration) {
  return static_cast<uint32_t>(now - start) >= duration;
}

struct Preference {
  uint8_t helpful[ACTIVITY_COUNT] = {};
  uint8_t rated[ACTIVITY_COUNT] = {};
  uint8_t last = NO_ACTIVITY;
  uint8_t lastRating = 0;

  void rate(uint8_t activity, bool helped) {
    if (activity >= ACTIVITY_COUNT) return;
    // Bound storage and let newer feedback change a long-established preference.
    if (rated[activity] >= 60) {
      helpful[activity] /= 2;
      rated[activity] /= 2;
    }
    ++rated[activity];
    if (helped) ++helpful[activity];
    last = activity;
    lastRating = helped ? 1 : 2;
  }
};

struct Memory {
  Preference profiles[PROFILE_COUNT];
};

// Weighted exploration, with a Beta(1,1) prior. Only explicit feedback trains it.
// No pulse, pressure intensity, inferred emotions or diagnostic labels are inputs.
inline uint8_t recommend(const Preference &p, uint32_t random) {
  uint16_t weights[ACTIVITY_COUNT];
  uint16_t total = 0;
  for (uint8_t i = 0; i < ACTIVITY_COUNT; ++i) {
    weights[i] = 1 + 100U * (p.helpful[i] + 1U) / (p.rated[i] + 2U);
    if (p.last == i && p.lastRating == 2) weights[i] = 0;
    total += weights[i];
  }
  uint16_t choice = random % total;
  for (uint8_t i = 0; i < ACTIVITY_COUNT; ++i) {
    if (choice < weights[i]) return i;
    choice -= weights[i];
  }
  return 0;
}

// A committed 32-byte record contains all profiles, a version, sequence and CRC.
// Invalidating the next slot first and committing it last preserves the previous
// complete snapshot across interrupted writes. No heap or raw struct serialization.
struct Journal {
  int16_t slot = -1;
  uint16_t sequence = 0;

  static uint16_t crc(const uint8_t *bytes, uint8_t length) {
    uint16_t value = 0xFFFF;
    for (uint8_t i = 0; i < length; ++i) {
      value ^= static_cast<uint16_t>(bytes[i]) << 8;
      for (uint8_t bit = 0; bit < 8; ++bit)
        value = value & 0x8000 ? (value << 1) ^ 0x1021 : value << 1;
    }
    return value;
  }

  static bool decode(const uint8_t *bytes, Memory &memory) {
    if (bytes[0] != 'R' || bytes[1] != 'P' || bytes[2] != 1 || bytes[31] != 0xA5)
      return false;
    const uint16_t expected = bytes[29] | (static_cast<uint16_t>(bytes[30]) << 8);
    if (crc(bytes, 29) != expected) return false;
    Memory candidate;
    for (uint8_t i = 0; i < PROFILE_COUNT; ++i) {
      Preference &p = candidate.profiles[i];
      const uint8_t at = 5 + i * 8;
      for (uint8_t a = 0; a < ACTIVITY_COUNT; ++a) {
        p.helpful[a] = bytes[at + a];
        p.rated[a] = bytes[at + 3 + a];
        if (p.rated[a] > 60 || p.helpful[a] > p.rated[a]) return false;
      }
      p.last = bytes[at + 6];
      p.lastRating = bytes[at + 7];
      if (p.last == NO_ACTIVITY) {
        if (p.lastRating != 0) return false;
      } else if (p.last >= ACTIVITY_COUNT || p.lastRating < 1 || p.lastRating > 2 ||
                 !p.rated[p.last]) return false;
    }
    memory = candidate;
    return true;
  }

  template <class Eeprom> bool load(Eeprom &eeprom, Memory &memory) {
    memory = Memory();
    slot = -1;
    sequence = 0;
    for (uint16_t s = 0; s < slots(eeprom); ++s) {
      uint8_t bytes[32];
      for (uint8_t i = 0; i < 32; ++i) bytes[i] = eeprom.read(s * 32 + i);
      Memory candidate;
      if (!decode(bytes, candidate)) continue;
      const uint16_t seq = bytes[3] | (static_cast<uint16_t>(bytes[4]) << 8);
      const uint16_t difference = static_cast<uint16_t>(seq - sequence);
      if (slot < 0 || (difference != 0 && difference < 0x8000)) {
        memory = candidate;
        slot = s;
        sequence = seq;
      }
    }
    return slot >= 0;
  }

  template <class Eeprom> bool save(Eeprom &eeprom, const Memory &memory) {
    if (slots(eeprom) < 2) return false;
    const uint16_t nextSlot = (slot + 1) % slots(eeprom);
    const uint16_t nextSequence = sequence + 1;
    uint8_t bytes[32] = {'R', 'P', 1};
    bytes[3] = nextSequence & 255;
    bytes[4] = nextSequence >> 8;
    for (uint8_t i = 0; i < PROFILE_COUNT; ++i) {
      const Preference &p = memory.profiles[i];
      const uint8_t at = 5 + i * 8;
      for (uint8_t a = 0; a < ACTIVITY_COUNT; ++a) {
        bytes[at + a] = p.helpful[a];
        bytes[at + 3 + a] = p.rated[a];
      }
      bytes[at + 6] = p.last;
      bytes[at + 7] = p.lastRating;
    }
    const uint16_t checksum = crc(bytes, 29);
    bytes[29] = checksum & 255;
    bytes[30] = checksum >> 8;
    bytes[31] = 0xA5;
    Memory verified;
    if (!decode(bytes, verified)) return false;
    const uint16_t base = nextSlot * 32;
    eeprom.update(base + 31, 0);
    for (uint8_t i = 0; i < 31; ++i) eeprom.update(base + i, bytes[i]);
    eeprom.update(base + 31, 0xA5);
    for (uint8_t i = 0; i < 32; ++i)
      if (eeprom.read(base + i) != bytes[i]) return false;
    slot = nextSlot;
    sequence = nextSequence;
    return true;
  }

  template <class Eeprom> void clearOldSlots(Eeprom &eeprom) const {
    if (slot < 0) return;
    // Used only after a successful explicit forget. Preserve other profiles in
    // the newly committed snapshot, while erasing their older shared snapshots.
    for (uint16_t s = 0; s < slots(eeprom); ++s) {
      if (s == static_cast<uint16_t>(slot)) continue;
      eeprom.update(s * 32 + 31, 0);
      for (uint8_t i = 0; i < 31; ++i) eeprom.update(s * 32 + i, 0xFF);
    }
  }

 private:
  template <class Eeprom> static uint16_t slots(Eeprom &eeprom) {
    const uint16_t count = eeprom.length() / 32;
    return count > 128 ? 128 : count;
  }
};

enum class Gesture : uint8_t { NONE, NEXT, SELECT, STOP };

struct PressureButton {
  bool down = false, candidate = false, armed = false, stopped = false;
  uint32_t changedAt = 0, pressedAt = 0;

  Gesture sample(uint16_t value, uint32_t now) {
    const bool next = value > 300 ? true : value < 220 ? false : candidate;
    if (next != candidate) { candidate = next; changedAt = now; }
    if (!elapsed(now, changedAt, 40)) return Gesture::NONE;
    if (down != candidate) {
      down = candidate;
      if (down) { pressedAt = now; stopped = false; }
      else {
        const bool wasArmed = armed;
        armed = true;
        if (!wasArmed || stopped) return Gesture::NONE;
        return elapsed(now, pressedAt, 1000) ? Gesture::SELECT : Gesture::NEXT;
      }
    }
    if (!down) armed = true;
    if (armed && down && !stopped && elapsed(now, pressedAt, 3000)) {
      stopped = true;
      return Gesture::STOP;
    }
    return Gesture::NONE;
  }
};

enum class Screen : uint8_t { IDLE, PROFILE, CHOICE, ACTIVITY, FEEDBACK, FORGET, DONE };
enum class Persist : uint8_t { NONE, SAVE, FORGET };

struct Controller {
  Screen screen = Screen::IDLE;
  uint8_t profile = 0;  // Guest; 1..3 are profiles explicitly selected by the person.
  uint8_t selected = 0, activity = 0;
  uint32_t since = 0, randomState = 1;
  bool saved = false, forgotten = false, storageError = false;

  void enter(Screen next, uint32_t now, uint8_t selection = 0) {
    screen = next;
    since = now;
    selected = selection;
  }

  uint32_t random() {
    randomState ^= randomState << 13;
    randomState ^= randomState >> 17;
    randomState ^= randomState << 5;
    return randomState;
  }

  void choose(const Memory &memory, uint32_t now) {
    Preference guest;
    enter(Screen::CHOICE, now, recommend(profile ? memory.profiles[profile - 1] : guest, random()));
  }

  void tick(uint32_t now) {
    if (screen == Screen::ACTIVITY && elapsed(now, since, activity == 2 ? 45000 : 30000))
      enter(Screen::FEEDBACK, now, 2);  // Default is to leave without saving.
    else if (screen == Screen::DONE && elapsed(now, since, 6000))
      enter(Screen::IDLE, now);
    else if (screen != Screen::IDLE && screen != Screen::ACTIVITY &&
             screen != Screen::DONE && elapsed(now, since, 90000))
      enter(Screen::IDLE, now);  // Silence and timeouts never count as feedback.
  }

  Persist handle(Gesture gesture, Memory &memory, uint32_t now) {
    if (gesture == Gesture::NONE) return Persist::NONE;
    if (gesture == Gesture::STOP) {
      profile = 0;
      enter(Screen::IDLE, now);
      return Persist::NONE;
    }
    since = now;
    if (screen == Screen::IDLE || screen == Screen::DONE) {
      profile = 0;
      saved = forgotten = storageError = false;
      enter(Screen::PROFILE, now);
    } else if (screen == Screen::PROFILE) {
      if (gesture == Gesture::NEXT) selected = (selected + 1) % (PROFILE_COUNT + 1);
      else { profile = selected; choose(memory, now); }
    } else if (screen == Screen::CHOICE) {
      if (gesture == Gesture::NEXT) selected = (selected + 1) % (profile ? 5 : 4);
      else if (selected < ACTIVITY_COUNT) {
        activity = selected;
        enter(Screen::ACTIVITY, now);
      } else if (profile && selected == 3) enter(Screen::FORGET, now);
      else enter(Screen::IDLE, now);
    } else if (screen == Screen::ACTIVITY) {
      enter(Screen::FEEDBACK, now, 2);
    } else if (screen == Screen::FEEDBACK) {
      if (gesture == Gesture::NEXT) selected = (selected + 1) % 3;
      else {
        if (profile && selected < 2) {
          memory.profiles[profile - 1].rate(activity, selected == 0);
          enter(Screen::DONE, now);
          return Persist::SAVE;
        }
        enter(Screen::DONE, now);
      }
    } else if (screen == Screen::FORGET) {
      if (gesture == Gesture::NEXT) selected = 1 - selected;
      else if (selected == 1) {
        memory.profiles[profile - 1] = Preference();
        enter(Screen::DONE, now);
        return Persist::FORGET;
      } else choose(memory, now);
    }
    return Persist::NONE;
  }
};

}  // namespace respaw_auto
