#include "solar_yaw_sequence.h"

namespace {
constexpr float kClockwiseGyroSign = -1.0f;
constexpr float kGyroCountsPerDegreePerSecond = 131.0f;
constexpr uint32_t kSampleIntervalUs = 10000;
constexpr uint32_t kMaximumSampleGapUs = 200000;
constexpr uint32_t kReversePauseMs = 500;
constexpr uint32_t kStallMs = 3000;
constexpr uint32_t kLegTimeoutMs = 180000;
constexpr float kProgressStepDeg = 0.25f;
constexpr float kWrongDirectionDeg = 2.0f;
constexpr float kSlowZoneDeg = 5.0f;
constexpr float kShortLegDeg = 10.0f;
constexpr float kClockwiseLegDeg = 360.0f;
constexpr float kAnticlockwiseLegDeg = 300.0f;
constexpr uint8_t kDriveDuty = 64;
constexpr uint8_t kSlowDuty = 48;
}  // namespace

const char *solarYawPhaseName(SolarYawPhase phase) {
  switch (phase) {
    case SolarYawPhase::Idle: return "idle";
    case SolarYawPhase::Clockwise: return "clockwise";
    case SolarYawPhase::Pause: return "pause";
    case SolarYawPhase::Anticlockwise: return "anticlockwise";
  }
  return "idle";
}

const char *solarYawResultName(SolarYawResult result) {
  switch (result) {
    case SolarYawResult::None: return nullptr;
    case SolarYawResult::Completed: return "completed";
    case SolarYawResult::UserStopped: return "user_stopped";
    case SolarYawResult::FeedbackUnavailable: return "feedback_unavailable";
    case SolarYawResult::SamplingInterrupted: return "sampling_interrupted";
    case SolarYawResult::WrongDirection: return "wrong_direction";
    case SolarYawResult::Stalled: return "stalled";
    case SolarYawResult::TimedOut: return "timed_out";
  }
  return nullptr;
}

void SolarYawSequence::start(bool shortTest, uint32_t nowUs, uint32_t nowMs) {
  if (active()) return;
  reset();
  report_.started = true;
  report_.shortTest = shortTest;
  report_.netRotationDeg = 0.0f;
  lastSampleUs_ = nowUs;
  beginLeg(SolarYawPhase::Clockwise, nowMs);
}

void SolarYawSequence::stop() {
  if (report_.started) finish(SolarYawResult::UserStopped);
}

void SolarYawSequence::reset() {
  *this = SolarYawSequence{};
}

bool SolarYawSequence::active() const {
  return report_.phase != SolarYawPhase::Idle;
}

bool SolarYawSequence::sampleDue(uint32_t nowUs) const {
  return active() && nowUs - lastSampleUs_ >= kSampleIntervalUs;
}

bool SolarYawSequence::sampleInterrupted(uint32_t nowUs) const {
  return active() && nowUs - lastSampleUs_ > kMaximumSampleGapUs;
}

void SolarYawSequence::sample(int16_t raw, bool valid, double gyroBias,
                              uint32_t nowUs, uint32_t nowMs) {
  if (!sampleDue(nowUs)) return;
  if (sampleInterrupted(nowUs)) {
    finish(SolarYawResult::SamplingInterrupted);
    return;
  }
  const float calibratedBias = static_cast<float>(gyroBias);
  if (!valid || !isfinite(calibratedBias)) {
    finish(SolarYawResult::FeedbackUnavailable);
    return;
  }

  const uint32_t elapsedUs = nowUs - lastSampleUs_;
  lastSampleUs_ = nowUs;
  const float rate = kClockwiseGyroSign * (raw - calibratedBias) /
      kGyroCountsPerDegreePerSecond;
  report_.netRotationDeg += rate * (elapsedUs / 1000000.0f);

  if (report_.phase == SolarYawPhase::Pause) {
    // Include any coast during the pause before recording the reverse baseline.
    if (nowMs - pauseStartMs_ >= kReversePauseMs) beginLeg(SolarYawPhase::Anticlockwise, nowMs);
    return;
  }

  const float netLegAngle = report_.netRotationDeg - legStartAngle_;
  const float progress = clockwise() ? netLegAngle : -netLegAngle;
  report_.legProgressDeg = progress;
  const float remaining = report_.legTargetDeg - progress;
  if (remaining <= 0.0f) {
    if (clockwise()) {
      duty_ = 0;
      report_.phase = SolarYawPhase::Pause;
      report_.legProgressDeg = NAN;
      report_.legTargetDeg = NAN;
      pauseStartMs_ = nowMs;
    } else {
      finish(SolarYawResult::Completed);
    }
    return;
  }

  if (progress >= bestProgress_ + kProgressStepDeg) {
    bestProgress_ = progress;
    lastProgressMs_ = nowMs;
  }
  if (progress < bestProgress_ - kWrongDirectionDeg) {
    finish(SolarYawResult::WrongDirection);
  } else if (nowMs - lastProgressMs_ >= kStallMs) {
    finish(SolarYawResult::Stalled);
  } else if (nowMs - legStartMs_ >= kLegTimeoutMs) {
    finish(SolarYawResult::TimedOut);
  } else {
    duty_ = remaining <= kSlowZoneDeg ? kSlowDuty : kDriveDuty;
  }
}

bool SolarYawSequence::clockwise() const {
  return report_.phase == SolarYawPhase::Clockwise;
}

uint8_t SolarYawSequence::duty() const {
  return duty_;
}

const SolarYawReport &SolarYawSequence::report() const {
  return report_;
}

void SolarYawSequence::beginLeg(SolarYawPhase phase, uint32_t nowMs) {
  duty_ = 0;
  report_.phase = phase;
  report_.legProgressDeg = 0.0f;
  report_.legTargetDeg = report_.shortTest ? kShortLegDeg :
      clockwise() ? kClockwiseLegDeg : kAnticlockwiseLegDeg;
  legStartAngle_ = report_.netRotationDeg;
  bestProgress_ = 0.0f;
  legStartMs_ = nowMs;
  lastProgressMs_ = nowMs;
}

void SolarYawSequence::finish(SolarYawResult result) {
  duty_ = 0;
  report_.phase = SolarYawPhase::Idle;
  report_.result = result;
}
