#pragma once

#include <math.h>
#include <stdint.h>

enum class SolarYawPhase : uint8_t {
  Idle,
  Clockwise,
  Pause,
  Anticlockwise,
};

enum class SolarYawResult : uint8_t {
  None,
  Completed,
  UserStopped,
  FeedbackUnavailable,
  SamplingInterrupted,
  WrongDirection,
  Stalled,
  TimedOut,
};

const char *solarYawPhaseName(SolarYawPhase phase);
const char *solarYawResultName(SolarYawResult result);

struct SolarYawReport {
  bool started = false;
  bool shortTest = false;
  SolarYawPhase phase = SolarYawPhase::Idle;
  SolarYawResult result = SolarYawResult::None;
  float legProgressDeg = NAN;
  float legTargetDeg = NAN;
  float netRotationDeg = NAN;
};

class SolarYawSequence {
 public:
  void start(bool shortTest, uint32_t nowUs, uint32_t nowMs);
  void stop();
  void reset();
  bool active() const;
  bool sampleDue(uint32_t nowUs) const;
  bool sampleInterrupted(uint32_t nowUs) const;
  void sample(int16_t raw, bool valid, double gyroBias, uint32_t nowUs, uint32_t nowMs);
  bool clockwise() const;
  uint8_t duty() const;
  const SolarYawReport &report() const;

 private:
  void beginLeg(SolarYawPhase phase, uint32_t nowMs);
  void finish(SolarYawResult result);

  SolarYawReport report_;
  uint32_t lastSampleUs_ = 0;
  uint32_t legStartMs_ = 0;
  uint32_t lastProgressMs_ = 0;
  uint32_t pauseStartMs_ = 0;
  float legStartAngle_ = 0.0f;
  float bestProgress_ = 0.0f;
  uint8_t duty_ = 0;
};
