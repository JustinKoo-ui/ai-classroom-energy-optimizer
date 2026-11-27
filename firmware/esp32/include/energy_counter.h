#pragma once

#include <math.h>
#include <stdint.h>

#include "classroom_config.h"

class EnergyCounter {
 public:
  void restore(double totalWh) { totalWh_ = totalWh; }
  double totalWh() const { return totalWh_; }

  void sample(float powerW, uint32_t sampledAt) {
    if (!isfinite(powerW)) {
      havePrevious_ = false;
      return;
    }

    // Subtract unsigned counters so a normal interval can span millis() wrap.
    const uint32_t elapsedMs = sampledAt - previousAt_;
    if (havePrevious_ && elapsedMs == 0) return;
    const double consumptionW = powerW > 0.0F ? powerW : 0.0;
    if (havePrevious_ && elapsedMs <= classroom_config::kMaxEnergySampleGapMs) {
      totalWh_ += (previousW_ + consumptionW) * 0.5 * elapsedMs / 3600000.0;
    }
    previousW_ = consumptionW;
    previousAt_ = sampledAt;
    havePrevious_ = true;
  }

 private:
  double totalWh_ = 0.0;
  double previousW_ = 0.0;
  uint32_t previousAt_ = 0;
  bool havePrevious_ = false;
};
