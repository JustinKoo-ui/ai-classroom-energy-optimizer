#pragma once

namespace relay_config {

// Calibrate each output independently against the connected low-voltage load.
constexpr bool kFanActiveLow = false;
// Yellow LED: logical OFF drives GPIO 12 LOW; logical ON drives it HIGH.
constexpr bool kLightActiveLow = false;
constexpr bool kAcActiveLow = false;

constexpr bool outputHigh(bool turnOn, bool activeLow) {
  return turnOn != activeLow;
}

static_assert(!outputHigh(false, false), "Active-HIGH OFF must drive LOW");
static_assert(outputHigh(true, false), "Active-HIGH ON must drive HIGH");
static_assert(outputHigh(false, true), "Active-LOW OFF must drive HIGH");
static_assert(!outputHigh(true, true), "Active-LOW ON must drive LOW");

}  // namespace relay_config
