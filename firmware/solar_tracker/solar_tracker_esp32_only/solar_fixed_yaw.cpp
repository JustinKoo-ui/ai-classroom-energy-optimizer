#include "solar_fixed_yaw.h"

#include <algorithm>
#include <cmath>
#include <limits>

namespace {
using Status = SolarFixedYawStatus;

constexpr float kYawMin = 55.0f;
constexpr float kYawMax = 305.0f;
constexpr float kYawStartError = 5.0f;
constexpr float kYawStopError = 2.5f;
constexpr float kYawGain = 6.0f;
constexpr int kMinDuty = 40;
constexpr int kMaxDuty = 90;
constexpr uint32_t kBoostStepMs = 2000;
constexpr int kBoostStepDuty = 5;
constexpr float kProgressStep = 0.5f;
constexpr float kWrongDirectionThreshold = -2.0f;
constexpr uint32_t kStallMs = 12000;

constexpr float kClockwiseGyroSign = -1.0f;
constexpr float kGyroCountsPerDegreePerSecond = 131.0f;
constexpr uint16_t kCalibrationSamples = 150;
constexpr float kMaxStationaryRate = 2.0f;
constexpr uint32_t kGyroIntervalMs = 20;
constexpr uint32_t kGyroTimeoutMs = 250;
constexpr uint32_t kReferenceLifetimeMs = 600000;
constexpr uint32_t kControlIntervalMs = 100;
constexpr uint32_t kControlTimeoutMs = 500;

bool withinTravel(double bearing) {
  return bearing >= kYawMin && bearing <= kYawMax;
}
}  // namespace

const char *solarFixedYawStatusName(SolarFixedYawStatus status) {
  switch (status) {
    case Status::WaitingForClock: return "waiting_for_clock";
    case Status::WaitingForReference: return "waiting_for_reference";
    case Status::Calibrating: return "calibrating";
    case Status::Ready: return "ready";
    case Status::Tracking: return "tracking";
    case Status::Aligned: return "aligned";
    case Status::SunBelowHorizon: return "sun_below_horizon";
    case Status::OutsideTravel: return "outside_travel";
    case Status::FeedbackUnavailable: return "feedback_unavailable";
    case Status::ReferenceExpired: return "reference_expired";
    case Status::BaseMovedDuringCalibration: return "base_moved_during_calibration";
    case Status::SamplingInterrupted: return "sampling_interrupted";
    case Status::ControlDelayed: return "control_delayed";
    case Status::WrongDirection: return "wrong_direction";
    case Status::Stalled: return "stalled";
  }
  return "feedback_unavailable";
}

void SolarFixedYaw::resetReference() {
  clearReference(Status::WaitingForReference);
  gyroStarted_ = false;
  gyroValid_ = false;
  controlStarted_ = false;
}

void SolarFixedYaw::startReference(float bearing, uint32_t nowMs) {
  resetReference();
  if (hardFault() || !(bearing >= 0.0f && bearing < 360.0f)) return;

  knownBearing_ = bearing;
  gyroBias_ = 0.0f;
  calibrationSamples_ = 0;
  calibrating_ = true;
  gyroStarted_ = true;
  gyroAt_ = nowMs;
  status_ = Status::Calibrating;
}

void SolarFixedYaw::disarm() {
  stopDrive();
  if (calibrating_) {
    clearReference(Status::WaitingForReference);
  } else if (status_ == Status::Tracking || status_ == Status::Aligned) {
    status_ = Status::Ready;
  }
}

void SolarFixedYaw::requestArm() {
  armRequested_ = true;
}

bool SolarFixedYaw::gyroSampleDue(uint32_t nowMs) const {
  return !gyroStarted_ || nowMs - gyroAt_ >= kGyroIntervalMs;
}

bool SolarFixedYaw::controlDue(uint32_t nowMs) const {
  return !controlStarted_ || nowMs - controlAt_ >= kControlIntervalMs;
}

float SolarFixedYaw::bearing() const {
  return referenced_ ? bearing_ : std::numeric_limits<float>::quiet_NaN();
}

bool SolarFixedYaw::hardFault() const {
  return status_ == Status::WrongDirection || status_ == Status::Stalled;
}

void SolarFixedYaw::stopDrive() {
  duty_ = 0;
  armed_ = false;
  armRequested_ = false;
}

void SolarFixedYaw::clearReference(SolarFixedYawStatus reason) {
  stopDrive();
  referenced_ = false;
  calibrating_ = false;
  referenceStatus_ = reason;
  if (!hardFault()) status_ = reason;
}

void SolarFixedYaw::updateGyro(int16_t raw, bool valid, uint32_t nowMs) {
  if (valid && !gyroSampleDue(nowMs)) return;

  const uint32_t elapsed = gyroStarted_ ? nowMs - gyroAt_ : 0;
  gyroStarted_ = true;
  gyroAt_ = nowMs;
  gyroValid_ = valid && elapsed <= kGyroTimeoutMs;
  if (!gyroValid_) {
    clearReference(valid ? Status::SamplingInterrupted : Status::FeedbackUnavailable);
    return;
  }

  const float rawRate = raw / kGyroCountsPerDegreePerSecond;
  if (calibrating_) {
    if (std::fabs(rawRate) > kMaxStationaryRate) {
      clearReference(Status::BaseMovedDuringCalibration);
      return;
    }
    ++calibrationSamples_;
    gyroBias_ += (rawRate - gyroBias_) / calibrationSamples_;
    if (calibrationSamples_ == kCalibrationSamples) {
      bearing_ = knownBearing_;
      lastRate_ = 0.0f;
      referenceAt_ = nowMs;
      referenced_ = true;
      calibrating_ = false;
      status_ = Status::Ready;
    }
    return;
  }

  if (!referenced_) return;
  if (nowMs - referenceAt_ >= kReferenceLifetimeMs) {
    clearReference(Status::ReferenceExpired);
    return;
  }

  const float rate = kClockwiseGyroSign * (rawRate - gyroBias_);
  bearing_ += (lastRate_ + rate) * 0.5f * (elapsed / 1000.0f);
  lastRate_ = rate;
  if (!withinTravel(bearing_)) {
    stopDrive();
    if (!hardFault()) status_ = Status::OutsideTravel;
  }
}

SolarFixedYawStatus SolarFixedYaw::blockedStatus(
    bool clockSet, double target, bool sunAboveHorizon) const {
  if (!clockSet) return Status::WaitingForClock;
  if (!referenced_ && !calibrating_ && referenceStatus_ != Status::WaitingForReference) {
    return referenceStatus_;
  }
  if (calibrating_) return Status::Calibrating;
  if (!gyroValid_) return Status::FeedbackUnavailable;
  if (!referenced_) return Status::WaitingForReference;
  if (!withinTravel(bearing_)) return Status::OutsideTravel;
  if (!sunAboveHorizon) return Status::SunBelowHorizon;
  if (!withinTravel(target)) return Status::OutsideTravel;
  return Status::Ready;
}

void SolarFixedYaw::updateControl(
    bool clockSet, double target, bool sunAboveHorizon, uint32_t nowMs) {
  if (!controlDue(nowMs)) return;
  const bool delayed = controlStarted_ && nowMs - controlAt_ > kControlTimeoutMs;
  controlStarted_ = true;
  controlAt_ = nowMs;
  reachable_ = clockSet && sunAboveHorizon && withinTravel(target);

  if (gyroStarted_ && nowMs - gyroAt_ > kGyroTimeoutMs) {
    gyroValid_ = false;
    clearReference(Status::SamplingInterrupted);
  } else if (referenced_ && nowMs - referenceAt_ >= kReferenceLifetimeMs) {
    clearReference(Status::ReferenceExpired);
  }

  if (hardFault()) {
    stopDrive();
    return;
  }
  status_ = blockedStatus(clockSet, target, sunAboveHorizon);
  if (status_ != Status::Ready || delayed) {
    if (status_ == Status::Ready) status_ = Status::ControlDelayed;
    stopDrive();
    return;
  }

  if (armRequested_) {
    armRequested_ = false;
    armed_ = true;
  }
  if (armed_) driveYaw(target, nowMs);
}

void SolarFixedYaw::driveYaw(double target, uint32_t nowMs) {
  const float error = static_cast<float>(target - bearing_);
  const float magnitude = std::fabs(error);
  if (magnitude < (duty_ != 0 ? kYawStopError : kYawStartError)) {
    duty_ = 0;
    status_ = Status::Aligned;
    return;
  }

  const bool clockwise = error > 0;
  if (duty_ == 0 || clockwise != clockwise_) {
    clockwise_ = clockwise;
    progressAnchor_ = bearing_;
    progressAt_ = nowMs;
  }
  const float progress = (bearing_ - progressAnchor_) * (clockwise ? 1.0f : -1.0f);
  if (progress > kProgressStep) {
    progressAnchor_ = bearing_;
    progressAt_ = nowMs;
  }
  if (progress < kWrongDirectionThreshold || nowMs - progressAt_ > kStallMs) {
    status_ = progress < kWrongDirectionThreshold ? Status::WrongDirection : Status::Stalled;
    stopDrive();
    return;
  }

  // Raise the minimum duty in bounded steps only until signed progress resumes.
  const int steps = static_cast<int>((nowMs - progressAt_) / kBoostStepMs);
  const int minimum = kMinDuty + kBoostStepDuty * steps;
  const int requested = static_cast<int>(kYawGain * magnitude);
  duty_ = static_cast<uint8_t>(std::min(kMaxDuty, std::max(minimum, requested)));
  status_ = Status::Tracking;
}
