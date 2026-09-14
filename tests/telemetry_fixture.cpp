// Emit production telemetry with a desktop Print-compatible sink. Python tests
// parse these exact frames using the receiver shipped to the Pico.
#include "telemetry.h"
#include <iostream>
#include <iomanip>
#include <sstream>

struct Output {
  template <typename T> void print(T value) { std::cout << value; }
  void print(float value, int precision) {
    std::ostringstream text;
    text << std::fixed << std::setprecision(precision) << value;
    std::cout << text.str();
  }
  void println(char value) { std::cout << value << '\n'; }
};

int main() {
  Output out;
  respaw::writeReady(out, true, false);
  respaw::writeHeartbeat(out, true, false, true, 10000);
  respaw::RRStats stats;
  for (int i = 0; i < 20; ++i) stats.add(i % 2 ? 810 : 790);
  respaw::writeMeasurement(out, stats, true, 30000, nullptr);
  respaw::writeMeasurement(out, stats, false, 12000, "contact_lost");
  respaw::writeMeasurement(out, stats, false, 30000, nullptr);
}
