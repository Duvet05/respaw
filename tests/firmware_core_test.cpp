#include "core.h"
#include <assert.h>
#include <string.h>

int main() {
  respaw::RRStats stats;
  assert(!stats.valid());
  assert(stats.bpm() == 0 && stats.rmssd() == 0 && stats.sdnn() == 0);
  stats.add(800);
  assert(!stats.valid() && stats.rmssd() == 0);
  stats.reset();
  for (int i = 0; i < 90; ++i) assert(stats.add(800));
  assert(stats.valid() && stats.count == 90 && stats.pairs == 89);
  assert(fabs(stats.bpm() - 75) < 0.001 && stats.rmssd() == 0);
  for (int i = 0; i < 40; ++i) stats.add(800);
  assert(stats.count == 128 && stats.overflow && !stats.valid());

  stats.reset();
  for (int i = 0; i < 20; ++i) stats.add(i % 2 ? 810 : 790);
  assert(stats.valid() && fabs(stats.rmssd() - 20) < 0.001);
  assert(fabs(stats.sdnn() - sqrtf(2000.0f / 19)) < 0.001);
  const uint16_t pairs = stats.pairs;
  assert(!stats.add(100));
  stats.add(800);
  assert(stats.pairs == pairs);  // Rejection breaks adjacency.
  assert(!stats.add(300) && !stats.add(1500));
  stats.reset();
  assert(stats.count == 0 && stats.pairs == 0 && stats.rejected == 0 && !stats.overflow);

  assert(!respaw::elapsed(33999, 0, 34000UL));
  assert(respaw::elapsed(34000, 0, 34000UL));
  assert(!respaw::elapsed(87999, 0, 88000UL));
  assert(respaw::elapsed(88000, 0, 88000UL));
  assert(respaw::elapsed(20, UINT32_MAX - 10, 31));
  assert(!respaw::elapsed(20, UINT32_MAX - 10, 32));

  respaw::LineBuffer line;
  for (int i = 0; i < 100; ++i) line.feed('x');
  const char *suffix = "V1 1 STOP\n";
  for (const char *p = suffix; *p; ++p) line.feed(*p);
  assert(line.overflow);
  line.reset();
  for (const char *p = suffix; *p; ++p) line.feed(*p);
  assert(!line.overflow && !strcmp(line.data, "V1 1 STOP"));
  line.reset();
  line.feed('\0');
  assert(line.overflow);

  respaw::BeatDetector detector;
  uint32_t interval = 0;
  for (uint32_t t = 0; t < 10000; t += 10) assert(!detector.sample(50000, t, interval));
  detector.reset();
  stats.reset();
  for (uint32_t t = 0; t < 30000; t += 10) {
    const uint32_t ir = static_cast<uint32_t>(50000 + 3000 * sin(t * 6.283185307 / 800));
    if (detector.sample(ir, t, interval)) stats.add(interval);
  }
  assert(stats.valid() && fabs(stats.bpm() - 75) < 1);
  detector.reset();
  assert(!detector.haveBeat && !detector.rising && detector.filled == 0);
  for (uint32_t t = 0; t < 1000; t += 10) assert(!detector.sample(50000, t, interval));
}
