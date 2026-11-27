#pragma once

#include <stdint.h>

enum class SolarFixedYawStatus : uint8_t {
  WaitingForClock,
  WaitingForReference,
  Calibrating,
  Ready,
  Tracking,
  Aligned,
  SunBelowHorizon,
  OutsideTravel,
  FeedbackUnavailable,
  ReferenceExpired,
  BaseMovedDuringCalibration,
  SamplingInterrupted,
  ControlDelayed,
  WrongDirection,
  Stalled,
};

const char *solarFixedYawStatusName(SolarFixedYawStatus status);

class SolarFixedYaw {
 public:
  void resetReference();
  void startReference(float bearing, uint32_t nowMs);
  void disarm();
  void requestArm();
  void updateGyro(int16_t raw, bool valid, uint32_t nowMs);
  void updateControl(bool clockSet, double target, bool sunAboveHorizon, uint32_t nowMs);
  bool gyroSampleDue(uint32_t nowMs) const;
  bool controlDue(uint32_t nowMs) const;

  bool referenced() const { return referenced_; }
  bool calibrating() const { return calibrating_; }
  bool armed() const { return armed_; }
  bool reachable() const { return reachable_; }
  bool clockwise() const { return clockwise_; }
  uint8_t duty() const { return duty_; }
  float bearing() const;
  SolarFixedYawStatus status() const { return status_; }

 private:
  bool hardFault() const;
  void stopDrive();
  void clearReference(SolarFixedYawStatus reason);
  SolarFixedYawStatus blockedStatus(
      bool clockSet, double target, bool sunAboveHorizon) const;
  void driveYaw(double target, uint32_t nowMs);

  SolarFixedYawStatus status_ = SolarFixedYawStatus::WaitingForClock;
  SolarFixedYawStatus referenceStatus_ = SolarFixedYawStatus::WaitingForReference;
  bool gyroStarted_ = false;
  bool gyroValid_ = false;
  bool controlStarted_ = false;
  bool referenced_ = false;
  bool calibrating_ = false;
  bool armRequested_ = false;
  bool armed_ = false;
  bool reachable_ = false;
  bool clockwise_ = false;
  uint8_t duty_ = 0;
  uint16_t calibrationSamples_ = 0;
  uint32_t gyroAt_ = 0;
  uint32_t controlAt_ = 0;
  uint32_t referenceAt_ = 0;
  uint32_t progressAt_ = 0;
  float bearing_ = 0.0f;
  float knownBearing_ = 0.0f;
  float gyroBias_ = 0.0f;
  float lastRate_ = 0.0f;
  float progressAnchor_ = 0.0f;
};
