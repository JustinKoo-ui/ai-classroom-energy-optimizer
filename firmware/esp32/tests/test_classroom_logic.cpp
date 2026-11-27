#include <assert.h>
#include <math.h>
#include <stdint.h>

#include "energy_counter.h"
#include "local_automation.h"
#include "relay_config.h"

namespace {

void expectTotal(const EnergyCounter &counter, double wh) {
  assert(fabs(counter.totalWh() - wh) < 1e-10);
}

void testEnergy() {
  EnergyCounter counter;
  counter.sample(2.0F, 1000);
  expectTotal(counter, 0.0);
  counter.sample(4.0F, 3500);
  expectTotal(counter, 3.0 * 2500 / 3600000);
  counter.sample(100.0F, 3500);  // Duplicate timestamp changes neither power nor Wh.
  counter.sample(NAN, 5500);
  counter.sample(5.0F, 7500);  // Invalid intervening sample breaks integration.
  counter.sample(5.0F, 14000);  // Excessive gap breaks integration independently.
  expectTotal(counter, 3.0 * 2500 / 3600000);
  counter.sample(-5.0F, 16000);  // Reverse flow does not subtract from consumption.
  expectTotal(counter, (3.0 * 2500 + 2.5 * 2000) / 3600000);

  EnergyCounter rebooted;
  rebooted.restore(counter.totalWh());
  rebooted.sample(3.0F, UINT32_MAX - 999);
  expectTotal(rebooted, counter.totalWh());
  rebooted.sample(3.0F, 1000);
  expectTotal(rebooted, counter.totalWh() + 3.0 * 2000 / 3600000);
}

void testLocalAutomation() {
  RelayTargets state = localRelayTargets(false, true, 150.0F, 28.0F, 80.0F);
  assert(!state.fan && !state.light && !state.airConditioner);
  state = localRelayTargets(true, true, 299.9F, 26.0F, 65.0F);
  assert(state.fan && state.light && !state.airConditioner);
  state = localRelayTargets(true, true, 300.0F, NAN, 65.1F);
  assert(state.fan && !state.light && state.airConditioner);
  state = localRelayTargets(true, false, 150.0F, 26.1F, NAN);
  assert(state.fan && !state.light && state.airConditioner);
  state = localRelayTargets(true, false, NAN, NAN, NAN);
  assert(state.fan && !state.light && !state.airConditioner);
  assert(relay_config::outputHigh(true, relay_config::kLightActiveLow));
  assert(relay_config::outputHigh(true, relay_config::kFanActiveLow));
  assert(relay_config::outputHigh(true, relay_config::kAcActiveLow));
}

}  // namespace

int main() {
  testEnergy();
  testLocalAutomation();
}
