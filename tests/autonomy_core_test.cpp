#include "autonomy.h"
#include <assert.h>
#include <array>
#include <stdexcept>

using namespace respaw_auto;

struct FakeEeprom {
  std::array<uint8_t, 4096> bytes;
  int remaining = -1;
  unsigned writes = 0;
  FakeEeprom() { bytes.fill(255); }
  uint16_t length() const { return bytes.size(); }
  uint8_t read(uint16_t at) const { return bytes.at(at); }
  void update(uint16_t at, uint8_t value) {
    if (remaining == 0) throw std::runtime_error("power loss");
    if (remaining > 0) --remaining;
    ++writes;
    bytes.at(at) = value;
  }
};

void testLearning() {
  Preference p;
  unsigned neutral[3] = {}, learned[3] = {};
  for (uint32_t i = 0; i < 10000; ++i) ++neutral[recommend(p, i)];
  for (uint8_t i = 0; i < 30; ++i) {
    p.rate(0, true);
    p.rate(1, false);
    p.rate(2, false);
  }
  for (uint32_t i = 0; i < 10000; ++i) ++learned[recommend(p, i)];
  assert(learned[0] > neutral[0] * 2);
  assert(learned[1] > 0);  // Exploration remains possible.
  assert(learned[2] == 0);  // Do not repeat the option just rejected.
  for (unsigned i = 0; i < 2000; ++i) {
    p.rate(0, false);
    p.rate(1, true);
  }
  unsigned changed[3] = {};
  for (uint32_t i = 0; i < 10000; ++i) ++changed[recommend(p, i)];
  assert(changed[1] > changed[0]);
  assert(p.rated[0] <= 60 && p.rated[1] <= 60);
  assert(p.helpful[0] <= p.rated[0] && p.helpful[1] <= p.rated[1]);
  const uint8_t last = p.last;
  p.rate(255, true);
  assert(p.last == last);
}

void testPersistence() {
  FakeEeprom eeprom;
  Journal journal;
  Memory memory;
  assert(!journal.load(eeprom, memory));
  assert(eeprom.writes == 0 && memory.profiles[0].last == NO_ACTIVITY);
  memory.profiles[0].rate(1, true);
  assert(journal.save(eeprom, memory));
  Journal restarted;
  Memory restored;
  assert(restarted.load(eeprom, restored));
  assert(restored.profiles[0].helpful[1] == 1);
  assert(restored.profiles[1].last == NO_ACTIVITY);

  // Interrupt every individual write in a save and recover the previous record.
  const FakeEeprom before = eeprom;
  memory.profiles[0].rate(1, false);
  for (int interruption = 0; interruption < 33; ++interruption) {
    FakeEeprom interrupted = before;
    interrupted.remaining = interruption;
    Journal writer;
    Memory loaded;
    assert(writer.load(interrupted, loaded));
    bool failed = false;
    try { writer.save(interrupted, memory); }
    catch (const std::runtime_error &) { failed = true; }
    assert(failed);
    Journal reader;
    assert(reader.load(interrupted, loaded));
    assert(loaded.profiles[0].rated[1] == 1 && loaded.profiles[0].lastRating == 1);
  }
  assert(restarted.save(eeprom, memory));
  assert(journal.load(eeprom, restored));
  assert(restored.profiles[0].rated[1] == 2 && restored.profiles[0].lastRating == 2);
  // Every corrupted payload/header/checksum byte in the newest slot falls back.
  for (unsigned i = 0; i < 32; ++i) {
    FakeEeprom corrupt = eeprom;
    corrupt.bytes[journal.slot * 32 + i] ^= 0x08;
    Journal reader;
    assert(reader.load(corrupt, restored));
    assert(restored.profiles[0].rated[1] == 1);
  }
  for (unsigned i = 0; i < 260; ++i) {
    memory.profiles[1].rate(0, i % 2 == 0);
    assert(journal.save(eeprom, memory));
  }
  assert(restarted.load(eeprom, restored));
  assert(restored.profiles[1].rated[0] == memory.profiles[1].rated[0]);
  // Sequence wrap is newer than 65535, without requiring 65536 writes.
  FakeEeprom wrapping;
  Journal rollover;
  rollover.sequence = 65534;
  assert(rollover.save(wrapping, memory));
  memory.profiles[2].rate(2, true);
  assert(rollover.save(wrapping, memory));
  assert(restarted.load(wrapping, restored));
  assert(restarted.sequence == 0 && restored.profiles[2].helpful[2] == 1);

  memory.profiles[0] = Preference();
  assert(journal.save(eeprom, memory));
  journal.clearOldSlots(eeprom);
  assert(restarted.load(eeprom, restored));
  assert(restored.profiles[0].last == NO_ACTIVITY);
  assert(restored.profiles[1].rated[0] == memory.profiles[1].rated[0]);
  for (uint16_t s = 0; s < 128; ++s) {
    if (s == journal.slot) continue;
    for (uint8_t i = 0; i < 31; ++i) assert(eeprom.read(s * 32 + i) == 255);
    assert(eeprom.read(s * 32 + 31) == 0);
  }
}

void testGestures() {
  PressureButton button;
  assert(button.sample(0, 50) == Gesture::NONE);
  assert(button.sample(400, 100) == Gesture::NONE);
  assert(button.sample(400, 150) == Gesture::NONE);
  assert(button.sample(270, 170) == Gesture::NONE);  // Hysteresis retains contact.
  assert(button.sample(0, 200) == Gesture::NONE);
  assert(button.sample(0, 250) == Gesture::NEXT);
  assert(button.sample(0, 300) == Gesture::NONE);
  button.sample(400, 400);
  button.sample(400, 450);
  button.sample(0, 1460);
  assert(button.sample(0, 1510) == Gesture::SELECT);
  button.sample(400, 2000);
  button.sample(400, 2050);
  assert(button.sample(400, 5050) == Gesture::STOP);
  assert(button.sample(400, 6000) == Gesture::NONE);
  button.sample(0, 6050);
  assert(button.sample(0, 6100) == Gesture::NONE);

  PressureButton bootHeld;
  bootHeld.sample(400, 0);
  bootHeld.sample(400, 50);
  assert(bootHeld.sample(400, 8000) == Gesture::NONE);
  bootHeld.sample(0, 8050);
  assert(bootHeld.sample(0, 8100) == Gesture::NONE);
  PressureButton bouncing;
  bouncing.sample(0, 100);
  for (uint32_t now = 200; now < 400; now += 10)
    assert(bouncing.sample(now % 20 ? 400 : 0, now) == Gesture::NONE);
  assert(bouncing.sample(0, 500) == Gesture::NONE);
  assert(elapsed(20, UINT32_MAX - 10, 31));
}

void testInteraction() {
  Memory memory;
  Controller c;
  assert(c.handle(Gesture::NEXT, memory, 100) == Persist::NONE);
  assert(c.screen == Screen::PROFILE && c.selected == 0);
  c.handle(Gesture::SELECT, memory, 200);  // Guest.
  c.handle(Gesture::SELECT, memory, 300);  // Accept recommendation.
  assert(c.screen == Screen::ACTIVITY);
  c.tick(50000);
  assert(c.screen == Screen::FEEDBACK && c.selected == 2);
  c.handle(Gesture::NEXT, memory, 50001);  // "Helped" for a guest still cannot save.
  assert(c.handle(Gesture::SELECT, memory, 50002) == Persist::NONE);
  for (const auto &p : memory.profiles) assert(p.last == NO_ACTIVITY);

  c.handle(Gesture::NEXT, memory, 60000);
  c.handle(Gesture::NEXT, memory, 60001);  // Explicit profile 1.
  c.handle(Gesture::SELECT, memory, 60002);
  assert(c.profile == 1);
  c.handle(Gesture::SELECT, memory, 60003);
  const uint8_t chosen = c.activity;
  c.handle(Gesture::NEXT, memory, 60004);  // End activity.
  c.handle(Gesture::NEXT, memory, 60005);  // Explicit helpful feedback.
  assert(c.handle(Gesture::SELECT, memory, 60006) == Persist::SAVE);
  assert(memory.profiles[0].helpful[chosen] == 1);
  assert(memory.profiles[1].last == NO_ACTIVITY);

  c.enter(Screen::FEEDBACK, 70000, 2);
  assert(c.handle(Gesture::SELECT, memory, 70001) == Persist::NONE);
  assert(memory.profiles[0].rated[chosen] == 1);
  c.enter(Screen::FEEDBACK, 80000, 0);
  assert(c.handle(Gesture::STOP, memory, 80001) == Persist::NONE);
  assert(memory.profiles[0].rated[chosen] == 1 && c.profile == 0);
  c.profile = 1;
  c.enter(Screen::FEEDBACK, UINT32_MAX - 100, 0);
  c.tick(90000);
  assert(c.screen == Screen::IDLE && memory.profiles[0].rated[chosen] == 1);

  c.profile = 1;
  c.enter(Screen::FORGET, 100000);
  assert(c.handle(Gesture::SELECT, memory, 100001) == Persist::NONE);  // Defaults to cancel.
  assert(memory.profiles[0].last == chosen);
  c.enter(Screen::FORGET, 110000);
  c.handle(Gesture::NEXT, memory, 110001);
  assert(c.handle(Gesture::SELECT, memory, 110002) == Persist::FORGET);
  assert(memory.profiles[0].last == NO_ACTIVITY);
}

int main() {
  testLearning();
  testPersistence();
  testGestures();
  testInteraction();
}
