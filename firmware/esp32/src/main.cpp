#include <Arduino.h>
#include "classroom_config.h"
#include "classroom_sensors.h"

#ifndef SENSOR_TEST_MODE
#define SENSOR_TEST_MODE 0
#endif

#if SENSOR_TEST_MODE
namespace {
uint32_t lastSampleAt = 0;
}

void setup() {
  Serial.begin(115200);
  classroom_sensors::begin(false);
  Serial.println("Sensor-only test; Wi-Fi, MQTT and energy persistence are disabled.");
  Serial.println("PIR needs up to 30 seconds to stabilize.");
}

void loop() {
  const uint32_t now = millis();
  if (now - lastSampleAt >= classroom_config::kSampleIntervalMs) {
    lastSampleAt = now;
    classroom_sensors::sample();
    classroom_sensors::printReadings();
  }
  delay(10);
}

#else
#include <ArduinoJson.h>
#include <esp_system.h>
#include <WiFi.h>
#include <AsyncMqttClient.h>
#include <Preferences.h>
#include <atomic>
#include <math.h>
#include <time.h>

#include "secrets.h"
#include "local_automation.h"
#include "relay_config.h"

#ifndef FAN_DEVICE_ID
#define FAN_DEVICE_ID "d7958a13-4c75-47e8-8b5e-4f4e00000027"
#endif
#ifndef LIGHT_DEVICE_ID
#define LIGHT_DEVICE_ID "d7958a13-4c75-47e8-8b5e-4f4e00000012"
#endif
#ifndef AC_DEVICE_ID
#define AC_DEVICE_ID "d7958a13-4c75-47e8-8b5e-4f4e00000014"
#endif
#ifndef COMMAND_DISPATCHER_PRODUCER_ID
#define COMMAND_DISPATCHER_PRODUCER_ID "command-dispatcher-local"
#endif
#ifndef MQTT_USERNAME
#define MQTT_USERNAME ""
#endif
#ifndef MQTT_PASSWORD
#define MQTT_PASSWORD ""
#endif

namespace {
using namespace classroom_config;

constexpr uint32_t kReconnectIntervalMs = 5000;
constexpr uint32_t kStateVersionBlock = 256;
constexpr uint32_t kUrgencySeconds = 300;
constexpr size_t kMaxCommandBytes = 1800;
constexpr uint8_t kCommandHistorySize = 12;
constexpr char kFirmwareVersion[] = "1.2.0";
enum RelayIndex { kFan, kLight, kAirConditioner };

struct RelayDevice {
  const char *id;
  const char *name;
  uint8_t pin;
  bool activeLow;
  bool isOn;
  bool manualMode;
  uint64_t urgencyUntilMs;
  bool powerBeforeUrgency;
};

struct QueuedCommand {
  char topic[192];
  char payload[kMaxCommandBytes + 1];
};

struct CommandReceipt {
  char commandId[37];
  char status[12];
  char reasonCode[64];
};

RelayDevice devices[] = {
    {FAN_DEVICE_ID, "FAN", kFanPin, relay_config::kFanActiveLow, false, false, 0, false},
    {LIGHT_DEVICE_ID, "LIGHT", kLightPin, relay_config::kLightActiveLow, false, false, 0, false},
    {AC_DEVICE_ID, "AC", kAcPin, relay_config::kAcActiveLow, false, false, 0, false},
};

AsyncMqttClient mqttClient;
Preferences preferences;
QueueHandle_t commandQueue = nullptr;
String telemetryTopic;
String gatewayStatusTopic;
// AsyncMqttClient retains this buffer while it establishes the connection.
String gatewayWillPayload;
String commandTopics[3];
uint32_t lastSampleAt = 0;
uint32_t lastMqttAttemptAt = 0;
uint32_t stateVersion = 0;
uint32_t reservedStateVersion = 0;
uint32_t receiptWriteIndex = 0;
bool announcedNetworkTime = false;
std::atomic<bool> mqttConnected{false};
std::atomic<bool> mqttConnecting{false};
std::atomic<uint32_t> pendingMqttPublishes{0};
std::atomic<bool> mqttAnnouncementPending{false};
char bootId[37];
CommandReceipt commandReceipts[kCommandHistorySize] = {};

char incomingTopic[192] = {};
char incomingPayload[kMaxCommandBytes + 1] = {};
size_t incomingBytes = 0;
bool incomingTooLarge = false;

String newUuid() {
  uint8_t bytes[16];
  for (size_t i = 0; i < sizeof(bytes); i += sizeof(uint32_t)) {
    const uint32_t randomWord = esp_random();
    for (size_t j = 0; j < sizeof(uint32_t); ++j) {
      bytes[i + j] = static_cast<uint8_t>(randomWord >> (j * 8));
    }
  }
  bytes[6] = static_cast<uint8_t>((bytes[6] & 0x0f) | 0x40);
  bytes[8] = static_cast<uint8_t>((bytes[8] & 0x3f) | 0x80);

  char output[37];
  snprintf(
      output,
      sizeof(output),
      "%02x%02x%02x%02x-%02x%02x-%02x%02x-%02x%02x-%02x%02x%02x%02x%02x%02x",
      bytes[0], bytes[1], bytes[2], bytes[3], bytes[4], bytes[5], bytes[6],
      bytes[7], bytes[8], bytes[9], bytes[10], bytes[11], bytes[12], bytes[13],
      bytes[14], bytes[15]);
  return String(output);
}

bool utcTimestamp(char *output, size_t outputSize) {
  const time_t now = time(nullptr);
  if (now < 1700000000) {
    return false;
  }
  struct tm utcTime {};
  if (gmtime_r(&now, &utcTime) == nullptr) {
    return false;
  }
  return strftime(output, outputSize, "%Y-%m-%dT%H:%M:%SZ", &utcTime) > 0;
}

bool isUuid(const char *value) {
  if (value == nullptr || strlen(value) != 36) {
    return false;
  }
  for (size_t index = 0; index < 36; ++index) {
    if (index == 8 || index == 13 || index == 18 || index == 23) {
      if (value[index] != '-') return false;
    } else if (!isxdigit(static_cast<unsigned char>(value[index]))) {
      return false;
    }
  }
  return true;
}

bool parseUtcTimestamp(const char *value, time_t &result) {
  if (value == nullptr || strlen(value) != 20) return false;
  int year = 0;
  int month = 0;
  int day = 0;
  int hour = 0;
  int minute = 0;
  int second = 0;
  int consumed = 0;
  if (sscanf(
          value,
          "%4d-%2d-%2dT%2d:%2d:%2dZ%n",
          &year,
          &month,
          &day,
          &hour,
          &minute,
          &second,
          &consumed) != 6 ||
      value[consumed] != '\0') {
    return false;
  }
  if (year < 1970 || month < 1 || month > 12 || hour > 23 || minute > 59 ||
      second > 59) {
    return false;
  }
  const bool leapYear = year % 4 == 0 && (year % 100 != 0 || year % 400 == 0);
  const int daysInMonth[] = {
      31, leapYear ? 29 : 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31};
  if (day < 1 || day > daysInMonth[month - 1]) return false;

  const int adjustedYear = year - (month <= 2 ? 1 : 0);
  const int era = (adjustedYear >= 0 ? adjustedYear : adjustedYear - 399) / 400;
  const unsigned yearOfEra = static_cast<unsigned>(adjustedYear - era * 400);
  const int adjustedMonth = month + (month > 2 ? -3 : 9);
  const unsigned dayOfYear =
      static_cast<unsigned>((153 * adjustedMonth + 2) / 5 + day - 1);
  const unsigned dayOfEra =
      yearOfEra * 365 + yearOfEra / 4 - yearOfEra / 100 + dayOfYear;
  const int64_t daysSinceEpoch =
      static_cast<int64_t>(era) * 146097 + dayOfEra - 719468;
  result = static_cast<time_t>(
      daysSinceEpoch * 86400 + hour * 3600 + minute * 60 + second);
  return result > 0;
}

String makeTopic(const char *suffix) {
  return String("aceo/v1/") + SITE_ID + "/" + CLASSROOM_ID + "/" + suffix;
}

void completeMqttPublish() {
  uint32_t pending = pendingMqttPublishes.load();
  while (pending != 0 &&
         !pendingMqttPublishes.compare_exchange_weak(pending, pending - 1)) {}
}

bool publishJson(const String &topic, JsonDocument &document, uint8_t qos, bool retain) {
  if (!mqttConnected) return false;
  String serialized;
  serializeJson(document, serialized);
  if (qos > 0) pendingMqttPublishes.fetch_add(1);
  const uint16_t packetId = mqttClient.publish(
      topic.c_str(), qos, retain, serialized.c_str(), serialized.length());
  if (qos > 0 && packetId == 0) completeMqttPublish();
  return packetId != 0;
}

void addEnvelopeHeader(JsonDocument &document, const char *occurredAt) {
  document["schema_version"] = "1.0";
  document["message_id"] = newUuid();
  document["occurred_at"] = occurredAt;
  document["site_id"] = SITE_ID;
  document["classroom_id"] = CLASSROOM_ID;
  document["producer_id"] = PRODUCER_ID;
}

const char *modeName(const RelayDevice &device) {
  return device.manualMode ? "manual" : "auto";
}

void advanceStateVersion() {
  // Reserve a block in NVS so local PIR/threshold transitions do not write flash.
  if (stateVersion >= reservedStateVersion) {
    reservedStateVersion = stateVersion + kStateVersionBlock;
    if (preferences.putUInt("stateVersion", reservedStateVersion) != sizeof(uint32_t)) {
      Serial.println("Could not reserve relay state versions in NVS.");
    }
  }
  ++stateVersion;
}

void controlOutput(const RelayDevice &device, bool turnOn) {
  digitalWrite(device.pin, relay_config::outputHigh(turnOn, device.activeLow) ? HIGH : LOW);
}

void setDeviceMode(RelayDevice &device, bool manualMode) {
  if (device.manualMode != manualMode) {
    device.manualMode = manualMode;
    advanceStateVersion();
  }
}

void setDeviceOutput(RelayDevice &device, bool turnOn) {
  if (device.isOn != turnOn) {
    device.isOn = turnOn;
    controlOutput(device, turnOn);
    advanceStateVersion();
  }
}

void updateLocalAutomation() {
  if (!kLocalAutomation) return;
  const auto &reading = classroom_sensors::latest();
  const RelayTargets targets = localRelayTargets(classroom_sensors::motionDetected(),
      reading.bh1750Fresh, reading.lux, reading.temperatureC, reading.humidityPct);
  const bool outputs[] = {targets.fan, targets.light, targets.airConditioner};
  const uint64_t now = classroom_sensors::uptimeMs();
  for (size_t index = 0; index < 3; ++index) {
    RelayDevice &device = devices[index];
    if (now < device.urgencyUntilMs) continue;
    if (device.urgencyUntilMs != 0) {
      device.urgencyUntilMs = 0;
      if (device.manualMode) setDeviceOutput(device, device.powerBeforeUrgency);
    }
    if (!device.manualMode) setDeviceOutput(device, outputs[index]);
  }
}

void addReportedState(JsonObject state, const RelayDevice &device) {
  state["on"] = device.isOn;
  state["mode"] = modeName(device);
  if (kLocalAutomation) state["automation_owner"] = "firmware";
}

void publishDeviceState(const RelayDevice &device) {
  char occurredAt[25];
  if (!utcTimestamp(occurredAt, sizeof(occurredAt))) return;

  JsonDocument document;
  addEnvelopeHeader(document, occurredAt);
  JsonObject payload = document["payload"].to<JsonObject>();
  payload["device_id"] = device.id;
  addReportedState(payload["state"].to<JsonObject>(), device);
  payload["fault_codes"].to<JsonArray>();
  payload["state_version"] = stateVersion;
  publishJson(
      makeTopic((String("device/") + device.id + "/state").c_str()),
      document,
      1,
      true);
}

void publishAllDeviceStates() {
  for (const RelayDevice &device : devices) {
    publishDeviceState(device);
  }
}

void makeAvailability(JsonDocument &document, const char *status, const char *occurredAt) {
  addEnvelopeHeader(document, occurredAt);
  JsonObject payload = document["payload"].to<JsonObject>();
  payload["status"] = status;
  payload["boot_id"] = bootId;
  payload["firmware_version"] = kFirmwareVersion;
}

void publishGatewayAvailability() {
  char occurredAt[25];
  announcedNetworkTime = utcTimestamp(occurredAt, sizeof(occurredAt));
  JsonDocument document;
  makeAvailability(document, "online", announcedNetworkTime ? occurredAt : nullptr);
  publishJson(gatewayStatusTopic, document, 1, true);
}

template <typename Number>
void setMeasurement(JsonObject payload, const char *key, Number value) {
  if (isfinite(value)) {
    payload[key] = value;
  } else {
    payload[key] = nullptr;
  }
}

void publishTelemetry() {
  char occurredAt[25];
  JsonDocument document;
  addEnvelopeHeader(document, utcTimestamp(occurredAt, sizeof(occurredAt)) ? occurredAt : nullptr);
  document["schema_version"] = "3.0";
  JsonObject payload = document["payload"].to<JsonObject>();
  const auto &reading = classroom_sensors::latest();
  payload["sensor_id"] = SENSOR_ID;
  payload["boot_id"] = bootId;
  payload["uptime_ms"] = classroom_sensors::uptimeMs();
  payload["sample_interval_ms"] = kSampleIntervalMs;
  setMeasurement(payload, "temperature_c", reading.temperatureC);
  setMeasurement(payload, "relative_humidity_pct", reading.humidityPct);
  setMeasurement(payload, "illuminance_lux", reading.lux);
  const bool pirReady = classroom_sensors::pirReady();
  if (pirReady) {
    payload["motion_detected"] = classroom_sensors::motionDetected();
  } else {
    payload["motion_detected"] = nullptr;
  }
  setMeasurement(payload, "voltage_v", reading.voltageV);
  setMeasurement(payload, "current_a", reading.currentA);
  setMeasurement(payload, "active_power_w", reading.powerW);
  setMeasurement(payload, "energy_wh_total", reading.energyWh);
  setMeasurement(payload, "energy_kwh_total", reading.energyWh / 1000.0);
  payload["energy_storage_available"] = reading.energyStorageAvailable;
  JsonObject relays = payload["relays"].to<JsonObject>();
  relays["light"] = devices[kLight].isOn;
  relays["fan"] = devices[kFan].isOn;
  relays["air_conditioner"] = devices[kAirConditioner].isOn;
  JsonObject sensors = payload["sensors"].to<JsonObject>();
  sensors["dht22_available"] = isfinite(reading.temperatureC) || isfinite(reading.humidityPct);
  sensors["pir_ready"] = pirReady;
  sensors["bh1750_available"] = reading.bh1750Available;
  sensors["bh1750_fresh"] = reading.bh1750Fresh;
  if (isfinite(reading.lux)) {
    sensors["bh1750_age_ms"] = reading.luxAgeMs;
  } else {
    sensors["bh1750_age_ms"] = nullptr;
  }
  sensors["ina219_available"] = reading.ina219Available;
  if (!publishJson(telemetryTopic, document, 1, false)) {
    Serial.println("MQTT telemetry publish was not queued.");
  }
}

int deviceIndexForId(const char *deviceId) {
  for (size_t index = 0; index < 3; ++index) {
    if (strcmp(devices[index].id, deviceId) == 0) return static_cast<int>(index);
  }
  return -1;
}

bool hasReceipt(const char *commandId, CommandReceipt &receipt) {
  for (const CommandReceipt &saved : commandReceipts) {
    if (saved.commandId[0] != '\0' && strcmp(saved.commandId, commandId) == 0) {
      receipt = saved;
      return true;
    }
  }
  return false;
}

void rememberReceipt(const char *commandId, const char *status, const char *reasonCode) {
  const uint8_t index = static_cast<uint8_t>(receiptWriteIndex % kCommandHistorySize);
  CommandReceipt &receipt = commandReceipts[index];
  snprintf(receipt.commandId, sizeof(receipt.commandId), "%s", commandId);
  snprintf(receipt.status, sizeof(receipt.status), "%s", status);
  snprintf(receipt.reasonCode, sizeof(receipt.reasonCode), "%s", reasonCode);
  const String suffix = String(index);
  preferences.putString((String("c") + suffix).c_str(), receipt.commandId);
  preferences.putString((String("s") + suffix).c_str(), receipt.status);
  preferences.putString((String("r") + suffix).c_str(), receipt.reasonCode);
  receiptWriteIndex = (receiptWriteIndex + 1) % kCommandHistorySize;
  preferences.putUInt("receiptIndex", receiptWriteIndex);
}

void loadReceipts() {
  receiptWriteIndex = preferences.getUInt("receiptIndex", 0);
  for (uint8_t index = 0; index < kCommandHistorySize; ++index) {
    const String suffix = String(index);
    preferences.getString((String("c") + suffix).c_str(), "").toCharArray(
        commandReceipts[index].commandId, sizeof(commandReceipts[index].commandId));
    preferences.getString((String("s") + suffix).c_str(), "").toCharArray(
        commandReceipts[index].status, sizeof(commandReceipts[index].status));
    preferences.getString((String("r") + suffix).c_str(), "").toCharArray(
        commandReceipts[index].reasonCode, sizeof(commandReceipts[index].reasonCode));
  }
}

void publishAcknowledgement(
    const char *commandId,
    const char *deviceId,
    const char *status,
    const char *reasonCode,
    const RelayDevice &device) {
  char occurredAt[25];
  if (!utcTimestamp(occurredAt, sizeof(occurredAt))) return;

  JsonDocument document;
  addEnvelopeHeader(document, occurredAt);
  JsonObject payload = document["payload"].to<JsonObject>();
  payload["command_id"] = commandId;
  payload["device_id"] = deviceId;
  payload["status"] = status;
  payload["reason_code"] = reasonCode;
  JsonObject reportedState = payload["reported_state"].to<JsonObject>();
  addReportedState(reportedState, device);
  reportedState["state_version"] = stateVersion;
  if (strcmp(status, "applied") == 0) {
    payload["applied_at"] = occurredAt;
  }
  publishJson(
      makeTopic((String("command/") + deviceId + "/ack").c_str()),
      document,
      1,
      false);
}

void rejectCommand(
    const char *commandId,
    const char *deviceId,
    const char *reasonCode,
    RelayDevice &device) {
  Serial.printf(
      "Rejected MQTT command %s for device %s: %s\n",
      commandId,
      deviceId,
      reasonCode);
  rememberReceipt(commandId, "rejected", reasonCode);
  publishAcknowledgement(commandId, deviceId, "rejected", reasonCode, device);
}

void processCommand(const QueuedCommand &command) {
  JsonDocument document;
  const DeserializationError error = deserializeJson(document, command.payload);
  if (error) {
    Serial.printf("Rejected malformed MQTT command JSON: %s\n", error.c_str());
    return;
  }
  JsonObject root = document.as<JsonObject>();
  JsonObject payload = root["payload"].as<JsonObject>();
  const char *commandId = payload["command_id"] | "";
  const char *deviceId = payload["device_id"] | "";
  if (!isUuid(commandId) || !isUuid(deviceId)) {
    Serial.println("Rejected MQTT command with invalid command/device UUID.");
    return;
  }
  const int index = deviceIndexForId(deviceId);
  if (index < 0) {
    Serial.println("Rejected MQTT command for an unregistered relay device.");
    return;
  }
  RelayDevice &device = devices[index];

  CommandReceipt previous {};
  if (hasReceipt(commandId, previous)) {
    publishAcknowledgement(
        commandId,
        deviceId,
        previous.status,
        previous.reasonCode,
        device);
    return;
  }

  const char *reasonCode = "invalid_command";
  bool valid = true;
  const char *schemaVersion = root["schema_version"] | "";
  const char *messageId = root["message_id"] | "";
  const char *occurredAt = root["occurred_at"] | "";
  const char *siteId = root["site_id"] | "";
  const char *classroomId = root["classroom_id"] | "";
  const char *producerId = root["producer_id"] | "";
  const char *decisionId = payload["decision_id"] | "";
  const char *idempotencyKey = payload["idempotency_key"] | "";
  const char *expiresAt = payload["expires_at"] | "";
  if (strcmp(schemaVersion, "1.0") != 0 ||
      !isUuid(messageId) || !isUuid(decisionId) ||
      strcmp(siteId, SITE_ID) != 0 ||
      strcmp(classroomId, CLASSROOM_ID) != 0 ||
      strcmp(producerId, COMMAND_DISPATCHER_PRODUCER_ID) != 0 ||
      idempotencyKey[0] == '\0') {
    reasonCode = "invalid_command_envelope";
    valid = false;
  }

  time_t expiry = 0;
  time_t occurred = 0;
  const time_t now = time(nullptr);
  if (valid &&
      (now < 1700000000 ||
       !parseUtcTimestamp(expiresAt, expiry) ||
       !parseUtcTimestamp(occurredAt, occurred))) {
    reasonCode = "time_unavailable_or_invalid_expiry";
    valid = false;
  } else if (valid && expiry <= now) {
    reasonCode = "command_expired";
    valid = false;
  } else if (valid && expiry > now + 60) {
    reasonCode = "command_expiry_too_far";
    valid = false;
  } else if (valid && occurred > now + 5) {
    reasonCode = "command_timestamp_in_future";
    valid = false;
  }

  const String expectedTopic =
      String("aceo/v1/") + SITE_ID + "/" + CLASSROOM_ID + "/command/" +
      deviceId + "/set";
  if (valid && expectedTopic != command.topic) {
    reasonCode = "topic_device_mismatch";
    valid = false;
  }

  const char *action = payload["action"] | "";
  JsonObject value = payload["value"].as<JsonObject>();
  bool requestedPower = false;
  bool requestedMode = false;
  bool targetManualMode = false;
  bool releaseUrgency = false;
  uint64_t urgencyUntilMs = 0;
  if (valid && strcmp(action, "set_power") == 0) {
    const char *requestedModeName = value["mode"] | "";
    const char *source = value["source"] | "";
    const bool hasSource = value["source"].is<const char *>();
    const char *urgencyDeadline = value["urgency_until"] | static_cast<const char *>(nullptr);
    const bool hasUrgencyDeadline = urgencyDeadline != nullptr && strcmp(source, "urgency") == 0;
    if (value.size() != 2 + static_cast<size_t>(hasSource) + static_cast<size_t>(hasUrgencyDeadline) ||
        !value["on"].is<bool>() ||
        (strcmp(requestedModeName, "manual") != 0 &&
         strcmp(requestedModeName, "auto") != 0)) {
      reasonCode = "unsupported_power_value";
      valid = false;
    } else {
      requestedPower = value["on"].as<bool>();
      targetManualMode = strcmp(requestedModeName, "manual") == 0;
      const char *effectiveSource =
          hasSource ? source : (targetManualMode ? "manual" : "");
      if (strcmp(effectiveSource, "manual") == 0 && targetManualMode) {
        requestedMode = true;
      } else if (strcmp(effectiveSource, "automation") == 0 ||
                 strcmp(effectiveSource, "vacancy") == 0 ||
                 strcmp(effectiveSource, "pir_fallback") == 0) {
        if (targetManualMode || device.manualMode) {
          reasonCode = "manual_mode_priority";
          valid = false;
        } else if (kLocalAutomation) {
          reasonCode = "firmware_automation_priority";
          valid = false;
        }
      } else if (strcmp(effectiveSource, "urgency") == 0 ||
                 strcmp(effectiveSource, "urgency_timeout") == 0 ||
                 strcmp(effectiveSource, "urgency_restore") == 0 ||
                 strcmp(effectiveSource, "manual_restore") == 0) {
        if (targetManualMode != device.manualMode) {
          reasonCode = "mode_mismatch";
          valid = false;
        } else if (strcmp(effectiveSource, "urgency") == 0) {
          time_t deadline = occurred + kUrgencySeconds;
          if ((hasUrgencyDeadline && !parseUtcTimestamp(urgencyDeadline, deadline)) ||
              deadline <= now || deadline > now + kUrgencySeconds + 5) {
            reasonCode = "invalid_urgency_deadline";
            valid = false;
          } else if (kLocalAutomation) {
            const uint64_t uptime = classroom_sensors::uptimeMs();
            urgencyUntilMs = !hasUrgencyDeadline && device.urgencyUntilMs > uptime
                ? device.urgencyUntilMs
                : uptime + static_cast<uint64_t>(deadline - now) * 1000;
          }
        } else {
          releaseUrgency = true;
        }
      } else {
        reasonCode = "unsupported_command_source";
        valid = false;
      }
    }
  } else if (valid && strcmp(action, "set_mode") == 0) {
    const char *mode = value["mode"] | "";
    if (value.size() != 1) {
      reasonCode = "unsupported_mode";
      valid = false;
    } else if (strcmp(mode, "manual") == 0) {
      requestedMode = true;
      targetManualMode = true;
    } else if (strcmp(mode, "auto") == 0) {
      requestedMode = true;
      targetManualMode = false;
    } else {
      reasonCode = "unsupported_mode";
      valid = false;
    }
  } else if (valid) {
    reasonCode = "unsupported_action";
    valid = false;
  }

  if (!valid) {
    rejectCommand(commandId, deviceId, reasonCode, device);
    return;
  }

  if (requestedMode) {
    setDeviceMode(device, targetManualMode);
  }
  if (strcmp(action, "set_power") == 0) {
    if (urgencyUntilMs != 0 && device.urgencyUntilMs == 0) {
      device.powerBeforeUrgency = device.isOn;
    }
    if (releaseUrgency || urgencyUntilMs != 0) device.urgencyUntilMs = urgencyUntilMs;
    setDeviceOutput(device, requestedPower);
  } else if (requestedMode && !targetManualMode) {
    updateLocalAutomation();
  }
  Serial.printf(
      "Applied MQTT command %s to %s: %s (%s)\n",
      commandId,
      device.name,
      device.isOn ? "ON" : "OFF",
      modeName(device));
  rememberReceipt(commandId, "applied", "applied");
  publishDeviceState(device);
  publishAcknowledgement(commandId, deviceId, "applied", nullptr, device);
}

void acceptIncomingMqttMessage(
    char *topic,
    char *payload,
    AsyncMqttClientMessageProperties,
    size_t length,
    size_t index,
    size_t total) {
  if (total > kMaxCommandBytes || strlen(topic) >= sizeof(incomingTopic)) {
    incomingTooLarge = true;
    incomingBytes = 0;
    return;
  }
  if (index == 0) {
    incomingBytes = 0;
    incomingTooLarge = false;
    snprintf(incomingTopic, sizeof(incomingTopic), "%s", topic);
  }
  if (incomingTooLarge || index != incomingBytes || incomingBytes + length > kMaxCommandBytes) {
    incomingTooLarge = true;
    incomingBytes = 0;
    return;
  }
  memcpy(incomingPayload + incomingBytes, payload, length);
  incomingBytes += length;
  if (incomingBytes != total) return;

  incomingPayload[incomingBytes] = '\0';
  QueuedCommand message {};
  snprintf(message.topic, sizeof(message.topic), "%s", incomingTopic);
  memcpy(message.payload, incomingPayload, incomingBytes + 1);
  if (commandQueue == nullptr || xQueueSend(commandQueue, &message, 0) != pdTRUE) {
    Serial.println("Command queue full; command was not applied.");
  } else {
    Serial.printf("Queued MQTT command from %s\n", incomingTopic);
  }
  incomingBytes = 0;
}

void connectMqtt() {
  if (!WiFi.isConnected() || mqttConnected || mqttConnecting) return;
  JsonDocument will;
  makeAvailability(will, "offline", nullptr);
  gatewayWillPayload = "";
  serializeJson(will, gatewayWillPayload);
  if (gatewayWillPayload.isEmpty()) return;
  mqttConnecting = true;
  lastMqttAttemptAt = millis();
  announcedNetworkTime = false;
  mqttClient.setWill(
      gatewayStatusTopic.c_str(),
      1,
      true,
      gatewayWillPayload.c_str(),
      gatewayWillPayload.length());
  mqttClient.connect();
}

void publishTelemetryAndStates() {
  // AsyncMqttClient serializes QoS 1 publications. Never accumulate old
  // routine snapshots behind an unacknowledged batch. Sensor acquisition
  // continues, and command state/acknowledgement publications bypass this gate.
  if (!mqttConnected || pendingMqttPublishes.load() != 0) {
    return;
  }
  publishTelemetry();
  for (const RelayDevice &device : devices) {
    publishDeviceState(device);
  }
}

void printStatus() {
  Serial.println();
  Serial.println("================================");
  char sampledAt[25];
  Serial.printf(
      "UTC: %s | MQTT: %s | pending publishes: %lu | Wi-Fi RSSI: %d dBm\n",
      utcTimestamp(sampledAt, sizeof(sampledAt)) ? sampledAt : "waiting for network time",
      mqttConnected ? "connected" : mqttConnecting ? "connecting" : "disconnected",
      static_cast<unsigned long>(pendingMqttPublishes.load()),
      static_cast<int>(WiFi.RSSI()));
  classroom_sensors::printReadings();
  Serial.println("-------- RELAY COMMANDS --------");
  for (const RelayDevice &device : devices) {
    Serial.printf(
        "%s: logical %s (%s) | GPIO %u=%s | active-%s\n",
        device.name,
        device.isOn ? "ON" : "OFF",
        modeName(device),
        static_cast<unsigned int>(device.pin),
        digitalRead(device.pin) == HIGH ? "HIGH" : "LOW",
        device.activeLow ? "LOW" : "HIGH");
  }
  Serial.printf("Auto controller: %s.\n", kLocalAutomation ? "ESP32" : "backend");
  Serial.println("================================");
}

}

void setup() {
  Serial.begin(115200);
  for (RelayDevice &device : devices) {
    // Preload the device's OFF level before enabling its output driver.
    controlOutput(device, false);
    pinMode(device.pin, OUTPUT);
    controlOutput(device, false);
    Serial.printf("Relay %s -> GPIO %u\n", device.name, static_cast<unsigned int>(device.pin));
  }
  classroom_sensors::begin(true);

  preferences.begin("aceo-control", false);
  stateVersion = preferences.getUInt("stateVersion", 0);
  reservedStateVersion = stateVersion;
  advanceStateVersion();
  loadReceipts();
  commandQueue = xQueueCreate(4, sizeof(QueuedCommand));
  if (commandQueue == nullptr) {
    Serial.println("Could not allocate MQTT command queue; local monitoring continues.");
  }

  telemetryTopic = makeTopic("telemetry/environment");
  gatewayStatusTopic = makeTopic((String("gateway/") + PRODUCER_ID + "/status").c_str());
  for (size_t index = 0; index < 3; ++index) {
    commandTopics[index] =
        makeTopic((String("command/") + devices[index].id + "/set").c_str());
  }

  // UUID-based command topics are 130 bytes; AsyncMqttClient defaults to 128
  // and silently drops longer incoming topics before calling onMessage.
  mqttClient.setMaxTopicLength(sizeof(incomingTopic) - 1);
  mqttClient.setServer(MQTT_HOST, MQTT_PORT);
  mqttClient.setClientId(PRODUCER_ID);
  mqttClient.setCleanSession(true);
  mqttClient.setKeepAlive(30);
  if (strlen(MQTT_USERNAME) > 0) {
    mqttClient.setCredentials(MQTT_USERNAME, MQTT_PASSWORD);
  }
  mqttClient.onConnect([](bool) {
    // A clean session has discarded publications from the previous connection.
    pendingMqttPublishes.store(0);
    Serial.println("MQTT connected.");
    for (const String &topic : commandTopics) {
      mqttClient.subscribe(topic.c_str(), 1);
    }
    mqttAnnouncementPending.store(true);
    mqttConnected.store(true);
    mqttConnecting.store(false);
  });
  mqttClient.onDisconnect([](AsyncMqttClientDisconnectReason reason) {
    mqttConnected = false;
    mqttConnecting = false;
    mqttAnnouncementPending.store(false);
    Serial.printf("MQTT disconnected (reason %d).\n", static_cast<int>(reason));
  });
  mqttClient.onPublish([](uint16_t) { completeMqttPublish(); });
  mqttClient.onMessage(acceptIncomingMqttMessage);

  snprintf(bootId, sizeof(bootId), "%s", newUuid().c_str());
  WiFi.mode(WIFI_STA);
  // This powered controller needs prompt MQTT acknowledgements. The default
  // modem-sleep mode can defer inbound Wi-Fi traffic until a DTIM beacon.
  if (!WiFi.setSleep(false)) {
    Serial.println("Could not disable Wi-Fi modem sleep; MQTT latency may increase.");
  }
  WiFi.setAutoReconnect(true);
  WiFi.onEvent([](WiFiEvent_t event, WiFiEventInfo_t info) {
    if (event == ARDUINO_EVENT_WIFI_STA_GOT_IP) {
      Serial.print("Wi-Fi connected. ESP32 IP: ");
      Serial.println(WiFi.localIP());
    } else if (event == ARDUINO_EVENT_WIFI_STA_DISCONNECTED) {
      mqttConnected = false;
      mqttConnecting = false;
      Serial.printf(
          "Wi-Fi disconnected (reason %u). Check SSID/password and 2.4 GHz coverage.\n",
          static_cast<unsigned int>(info.wifi_sta_disconnected.reason));
    }
  });
  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
  configTime(0, 0, "pool.ntp.org", "time.nist.gov");
  Serial.println("Connecting to Wi-Fi...");
  lastSampleAt = millis() - kSampleIntervalMs;
  lastMqttAttemptAt = millis() - kReconnectIntervalMs;
}

void loop() {
  if (WiFi.isConnected() && !mqttConnected && !mqttConnecting &&
      millis() - lastMqttAttemptAt >= kReconnectIntervalMs) {
    connectMqtt();
  }

  const uint32_t now = millis();
  const bool sampling = now - lastSampleAt >= kSampleIntervalMs;
  if (sampling) {
    lastSampleAt = now;
    classroom_sensors::sample();
  }
  updateLocalAutomation();

  QueuedCommand command {};
  if (commandQueue != nullptr && xQueueReceive(commandQueue, &command, 0) == pdTRUE) {
    processCommand(command);
  }

  if (mqttConnected && (mqttAnnouncementPending.exchange(false) ||
          (!announcedNetworkTime && time(nullptr) >= 1700000000))) {
    publishGatewayAvailability();
    publishAllDeviceStates();
  }
  if (sampling) {
    printStatus();
    publishTelemetryAndStates();
  }
  delay(10);
}
#endif
