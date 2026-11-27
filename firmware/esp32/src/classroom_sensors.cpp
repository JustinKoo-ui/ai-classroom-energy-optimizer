#include "classroom_sensors.h"

#include <Adafruit_INA219.h>
#include <Arduino.h>
#include <BH1750.h>
#include <DHT.h>
#include <Preferences.h>
#include <Wire.h>
#include <esp_timer.h>

#include "classroom_config.h"
#include "energy_counter.h"

namespace classroom_sensors {
namespace {

using namespace classroom_config;

constexpr uint8_t kLuxAddress = 0x23;
constexpr uint8_t kInaAddress = 0x40;
constexpr uint32_t kI2cFrequency = 100000;
constexpr uint32_t kSensorRetryMs = 5000;
constexpr uint16_t kI2cTimeoutMs = 20;
constexpr uint16_t kInaMathOverflow = 0x0001;
constexpr uint16_t kInaConversionReady = 0x0002;
constexpr float kInaMaxBusVoltageV = 26.0F;
constexpr char kEnergyKey[] = "totalWh";

DHT dht(kDhtPin, DHT22);
BH1750 lightMeter;
TwoWire inaWire(1);
Adafruit_INA219 ina219(kInaAddress);
Preferences energyStorage;
EnergyCounter energy;
Readings readings;
uint64_t lastLuxAt = 0;
uint32_t lastLuxInitAt = 0;
uint32_t lastInaInitAt = 0;
uint32_t lastCheckpointAt = 0;
double checkpointWh = 0.0;
bool wantPersistentEnergy = false;
bool energyRestored = false;
bool energyUsable = false;
bool luxBusReady = false;
bool inaBusReady = false;
bool inaConfigured = false;

void initializeLux() {
  lastLuxInitAt = millis();
  if (!luxBusReady) luxBusReady = Wire.begin(kLuxSda, kLuxScl, kI2cFrequency);
  readings.bh1750Available = luxBusReady &&
      lightMeter.begin(BH1750::CONTINUOUS_HIGH_RES_MODE, kLuxAddress, &Wire);
}

void initializeIna() {
  lastInaInitAt = millis();
  if (!inaBusReady) inaBusReady = inaWire.begin(kInaSda, kInaScl, kI2cFrequency);
  inaConfigured = inaBusReady && ina219.begin(&inaWire) && ina219.success();
}

void sampleLux() {
  readings.bh1750Fresh = false;
  if (!readings.bh1750Available) {
    if (millis() - lastLuxInitAt >= kSensorRetryMs) initializeLux();
  } else if (lightMeter.measurementReady(true)) {
    const float lux = lightMeter.readLightLevel();
    if (isfinite(lux) && lux >= 0.0F) {
      readings.lux = lux;
      lastLuxAt = uptimeMs();
      readings.bh1750Fresh = true;
    } else {
      readings.bh1750Available = false;
      lastLuxInitAt = millis();
    }
  }
  readings.luxAgeMs = isfinite(readings.lux) ? uptimeMs() - lastLuxAt : 0;
}

bool readInaBusVoltage(float &voltageV, bool &ready) {
  // The library accessor strips the bus register's conversion status flags.
  inaWire.beginTransmission(kInaAddress);
  inaWire.write(INA219_REG_BUSVOLTAGE);
  if (inaWire.endTransmission(false) != 0 ||
      inaWire.requestFrom(kInaAddress, static_cast<uint8_t>(2)) != 2) {
    return false;
  }
  const uint16_t high = inaWire.read();
  const uint16_t low = inaWire.read();
  const uint16_t raw = (high << 8) | low;
  ready = (raw & (kInaConversionReady | kInaMathOverflow)) == kInaConversionReady;
  voltageV = (raw >> 3) / 250.0F;
  return true;
}

void sampleIna() {
  readings.ina219Available = false;
  readings.voltageV = NAN;
  readings.currentA = NAN;
  readings.powerW = NAN;
  if (!inaConfigured) {
    if (millis() - lastInaInitAt >= kSensorRetryMs) initializeIna();
    return;  // Wait for the next sample after reconfiguration.
  }

  float voltageV = NAN;
  bool ready = false;
  if (!readInaBusVoltage(voltageV, ready)) {
    inaConfigured = false;
    lastInaInitAt = millis();
    return;
  }
  if (!ready || voltageV > kInaMaxBusVoltageV) return;

  const float currentA = ina219.getCurrent_mA() / 1000.0F;
  if (!ina219.success() || !isfinite(currentA)) {
    inaConfigured = false;
    lastInaInitAt = millis();
    return;
  }
  readings.ina219Available = true;
  readings.voltageV = voltageV;
  readings.currentA = currentA;
  readings.powerW = voltageV * currentA;
}

void restoreEnergy() {
  if (!energyStorage.begin("aceo-energy", false)) {
    Serial.println("Energy storage could not be opened.");
    return;
  }
  if (energyStorage.isKey(kEnergyKey)) {
    const double storedWh = energyStorage.getDouble(kEnergyKey, NAN);
    if (!isfinite(storedWh) || storedWh < 0.0) {
      Serial.println("Stored energy is unreadable.");
      return;
    }
    checkpointWh = storedWh;
    energy.restore(storedWh);
    energyUsable = true;
    readings.energyWh = storedWh;
  }
  energyRestored = true;
  readings.energyStorageAvailable = true;
}

void updateEnergy() {
  const uint32_t now = millis();
  energy.sample(readings.powerW, now);
  energyUsable = energyUsable || isfinite(readings.powerW);
  if (!energyUsable || (wantPersistentEnergy && !energyRestored)) return;

  readings.energyWh = energy.totalWh();
  if (!wantPersistentEnergy || now - lastCheckpointAt < kEnergyCheckpointIntervalMs ||
      readings.energyWh == checkpointWh) {
    return;
  }
  lastCheckpointAt = now;
  readings.energyStorageAvailable =
      energyStorage.putDouble(kEnergyKey, readings.energyWh) == sizeof(double);
  if (readings.energyStorageAvailable) {
    checkpointWh = readings.energyWh;
  } else {
    Serial.println("Energy checkpoint failed; will retry at the next checkpoint.");
  }
}

void printValue(const char *label, double value, const char *unit) {
  if (isfinite(value)) {
    Serial.printf("%s: %.4f %s\n", label, value, unit);
  } else {
    Serial.printf("%s: unavailable\n", label);
  }
}

}  // namespace

void begin(bool persistEnergy) {
  wantPersistentEnergy = persistEnergy;
  pinMode(kPirPin, INPUT_PULLDOWN);
  dht.begin();
  Wire.setTimeOut(kI2cTimeoutMs);
  inaWire.setTimeOut(kI2cTimeoutMs);
  initializeLux();
  initializeIna();
  lastCheckpointAt = millis();
  if (wantPersistentEnergy) restoreEnergy();
}

void sample() {
  const float temperatureC = dht.readTemperature();
  const float humidityPct = dht.readHumidity();
  readings.temperatureC = temperatureC >= -40.0F && temperatureC <= 80.0F ? temperatureC : NAN;
  readings.humidityPct = humidityPct >= 0.0F && humidityPct <= 100.0F ? humidityPct : NAN;
  sampleIna();
  updateEnergy();
  sampleLux();
}

const Readings &latest() { return readings; }

uint64_t uptimeMs() { return esp_timer_get_time() / 1000; }

bool pirReady() { return uptimeMs() >= kPirWarmupMs; }

bool motionDetected() { return pirReady() && digitalRead(kPirPin) == HIGH; }

void printReadings() {
  Serial.printf("PIR (GPIO %u): %s\n", static_cast<unsigned int>(kPirPin),
      !pirReady() ? "warming up" : motionDetected() ? "DETECTED" : "NOT DETECTED");
  printValue("DHT22 temperature", readings.temperatureC, "C");
  printValue("DHT22 humidity", readings.humidityPct, "% RH");
  printValue(readings.bh1750Fresh ? "BH1750 illuminance" : "BH1750 last illuminance",
      readings.lux, "lux");
  printValue("INA219 load voltage", readings.voltageV, "V");
  printValue("INA219 current", readings.currentA, "A");
  printValue("INA219 power", readings.powerW, "W");
  printValue("INA219 cumulative energy", readings.energyWh, "Wh");
}

}  // namespace classroom_sensors
