#pragma once

#include <math.h>
#include <stdint.h>
#include <time.h>
#include "solar_fixed_yaw.h"
#include "solar_yaw_sequence.h"

inline constexpr time_t kEarliestSolarNetworkTime = 1704067200;  // 2024-01-01 UTC

enum class SolarStopReason : uint8_t {
  None,
  WaitingForGps,
  WaitingForClock,
  SpaError,
  FeedbackUnavailable,
  ZenithLimit,
  LocalHold,
};

struct SolarFixedYawSample {
  bool enabled = false;
  bool clockSet = false;
  bool referenced = false;
  bool calibrating = false;
  bool armed = false;
  bool reachable = false;
  SolarFixedYawStatus status = SolarFixedYawStatus::WaitingForClock;
  uint16_t demoSpeed = 1;
  time_t solarUtc = 0;
};

struct SolarTrackingSample {
  double targetAzimuth = NAN;
  double actualAzimuth = NAN;
  double targetZenith = NAN;
  double actualZenith = NAN;
  SolarStopReason stopReason = SolarStopReason::None;
  SolarYawReport yawSequence;
  SolarFixedYawSample fixedYaw;
};

struct SolarPowerSample {
  double voltage = NAN;
  double current = NAN;
  double power = NAN;
  double energy = NAN;
};

struct SolarTelemetrySnapshot {
  int64_t capturedAtUs;
  int64_t lastFixAgeMs;
  bool gpsEnabled;
  bool gpsFix;
  bool gpsTimeValid;
  double latitude;
  double longitude;
  bool manualMode;
  uint32_t azimuthPwm;
  uint32_t zenithPwm;
  SolarTrackingSample tracking;
  SolarPowerSample solarPower;
  bool ina219Available;
  bool azimuthMpuAvailable;
  bool zenithMpuAvailable;
};

void startSolarTelemetry();
bool solarTelemetryDue(uint32_t nowMs);
void queueSolarTelemetry(const SolarTelemetrySnapshot &snapshot);
