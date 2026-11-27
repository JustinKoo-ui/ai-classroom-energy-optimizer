#pragma once

#include <math.h>
#include <stdint.h>

namespace classroom_sensors {

struct Readings {
  float temperatureC = NAN;
  float humidityPct = NAN;
  float lux = NAN;
  float voltageV = NAN;
  float currentA = NAN;
  float powerW = NAN;
  double energyWh = NAN;
  bool bh1750Available = false;
  bool bh1750Fresh = false;
  bool ina219Available = false;
  bool energyStorageAvailable = false;
  uint64_t luxAgeMs = 0;
};

void begin(bool persistEnergy);
void sample();
const Readings &latest();
bool pirReady();
bool motionDetected();
uint64_t uptimeMs();
void printReadings();

}  // namespace classroom_sensors
