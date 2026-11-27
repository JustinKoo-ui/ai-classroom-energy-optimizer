#pragma once

#include "classroom_config.h"

struct RelayTargets {
  bool fan;
  bool light;
  bool airConditioner;
};

inline RelayTargets localRelayTargets(
    bool motion,
    bool luxFresh,
    float lux,
    float temperatureC,
    float humidityPct) {
  using namespace classroom_config;
  return {
      motion,
      motion && luxFresh && lux < kLuxThreshold,
      motion && (temperatureC > kTemperatureThresholdC || humidityPct > kHumidityThresholdPct),
  };
}
