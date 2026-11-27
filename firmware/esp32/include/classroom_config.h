#pragma once

#include <stdint.h>

namespace classroom_config {

constexpr uint8_t kDhtPin = 4;
constexpr uint8_t kPirPin = 23;
constexpr uint8_t kLuxSda = 21;
constexpr uint8_t kLuxScl = 22;
constexpr uint8_t kInaSda = 32;
constexpr uint8_t kInaScl = 33;
constexpr uint8_t kLightPin = 12;
constexpr uint8_t kFanPin = 14;
constexpr uint8_t kAcPin = 27;

constexpr bool kLocalAutomation = true;
constexpr float kLuxThreshold = 300.0F;
constexpr float kTemperatureThresholdC = 26.0F;
constexpr float kHumidityThresholdPct = 65.0F;
constexpr uint32_t kSampleIntervalMs = 2000;
constexpr uint32_t kPirWarmupMs = 30000;
constexpr uint32_t kMaxEnergySampleGapMs = 6000;
constexpr uint32_t kEnergyCheckpointIntervalMs = 60000;

}  // namespace classroom_config
