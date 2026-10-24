#include <Arduino.h>
#include <ArduinoJson.h>
#include <DHT.h>
#include <esp_system.h>
#include <WiFi.h>
#include <AsyncMqttClient.h>
#include <Preferences.h>
#include <math.h>
#include <time.h>

#ifndef SENSOR_TEST_MODE
#define SENSOR_TEST_MODE 0
#endif

#if SENSOR_TEST_MODE
namespace {
constexpr uint8_t kDht22Pin = 4;
constexpr uint8_t kPirPin = 21;
constexpr uint8_t kAcs712Pin = 34;
constexpr uint8_t kLdrPin = 35;
constexpr uint32_t kSensorTestIntervalMs = 2000;
constexpr uint32_t kPirWarmupMs = 30000;

DHT dht(kDht22Pin, DHT22);
uint32_t startedAt = 0;
uint32_t lastSampleAt = 0;
uint32_t sampleNumber = 0;
}

void setup() {
  Serial.begin(115200);
  startedAt = millis();
  dht.begin();
  pinMode(kPirPin, INPUT);
  analogReadResolution(12);
  analogSetPinAttenuation(kAcs712Pin, ADC_11db);
  analogSetPinAttenuation(kLdrPin, ADC_11db);
  Serial.println("Sensor-only test; Wi-Fi and MQTT are disabled.");
  Serial.println("Keep ACS712 OUT disconnected unless its voltage is safe for 3.3 V ADC.");
  Serial.println("PIR needs up to 30 seconds to stabilize.");
}

void loop() {
  if (millis() - lastSampleAt < kSensorTestIntervalMs) {
    delay(10);
    return;
  }
  lastSampleAt = millis();
  ++sampleNumber;

  const float temperatureC = dht.readTemperature();
  const float humidityPct = dht.readHumidity();
  const uint16_t acsAdc = static_cast<uint16_t>(analogRead(kAcs712Pin));
  const uint32_t acsMilliVolts = analogReadMilliVolts(kAcs712Pin);
  const uint16_t ldrAdc = static_cast<uint16_t>(analogRead(kLdrPin));

  Serial.printf("Sample %lu\n", static_cast<unsigned long>(sampleNumber));
  if (isfinite(temperatureC) && isfinite(humidityPct)) {
    Serial.printf("DHT22: %.1f C, %.1f %% RH\n", temperatureC, humidityPct);
  } else {
    Serial.println("DHT22: READ ERROR (check DATA wiring and pull-up)");
  }

  if (millis() - startedAt < kPirWarmupMs) {
    Serial.println("PIR: warming up (signal not evaluated yet)");
  } else {
    Serial.printf("PIR: %s\n", digitalRead(kPirPin) == HIGH ? "MOTION" : "no motion");
  }
  Serial.printf("LDR AO (GPIO 35): %u / 4095 ADC counts (not lux)\n", ldrAdc);
  Serial.printf(
      "ACS712 OUT (GPIO 34): %u ADC counts, %lu mV at ESP32 pin\n",
      acsAdc,
      static_cast<unsigned long>(acsMilliVolts));
  Serial.println();
}

#else
#include "secrets.h"

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
constexpr uint32_t kReadIntervalMs = 2000;
constexpr uint32_t kPirWarmupMs = 30000;
constexpr uint32_t kReconnectIntervalMs = 5000;
constexpr uint8_t kLdrSamples = 16;
constexpr uint16_t kLdrThreshold = 4000;
constexpr float kTemperatureThresholdC = 26.0F;
constexpr float kHumidityThresholdPct = 65.0F;
constexpr bool kRelayActiveLow = false;
constexpr size_t kMaxCommandBytes = 1800;
constexpr uint8_t kCommandHistorySize = 12;

struct RelayDevice {
  const char *id;
  const char *name;
  uint8_t pin;
  bool isOn;
  bool manualMode;
  bool manualTarget;
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
    {FAN_DEVICE_ID, "FAN (Red)", 27, false, false, false},
    {LIGHT_DEVICE_ID, "LIGHT (Yellow)", 12, false, false, false},
    {AC_DEVICE_ID, "AC (Blue)", 14, false, false, false},
};

DHT dht(DHT22_GPIO, DHT22);
AsyncMqttClient mqttClient;
Preferences preferences;
QueueHandle_t commandQueue = nullptr;
String telemetryTopic;
String gatewayStatusTopic;
// AsyncMqttClient retains this buffer while it establishes the connection.
String gatewayWillPayload;
String commandTopics[3];
float temperatureC = NAN;
float humidityPct = NAN;
uint16_t ldrAdc = 0;
uint16_t currentAdc = 0;
bool motionDetected = false;
uint32_t lastSampleAt = 0;
uint32_t lastMqttAttemptAt = 0;
uint32_t bootedAt = 0;
uint32_t stateVersion = 0;
uint32_t receiptWriteIndex = 0;
volatile bool mqttConnected = false;
volatile bool mqttConnecting = false;
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

bool publishJson(const String &topic, JsonDocument &document, uint8_t qos, bool retain) {
  if (!mqttConnected) return false;
  String serialized;
  serializeJson(document, serialized);
  const uint16_t packetId = mqttClient.publish(
      topic.c_str(), qos, retain, serialized.c_str(), serialized.length());
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

void persistStateVersion() {
  ++stateVersion;
  preferences.putUInt("stateVersion", stateVersion);
}

void controlOutput(uint8_t pin, bool turnOn) {
  if (kRelayActiveLow) {
    digitalWrite(pin, turnOn ? LOW : HIGH);
  } else {
    digitalWrite(pin, turnOn ? HIGH : LOW);
  }
}

void setDeviceMode(RelayDevice &device, bool manualMode) {
  if (device.manualMode != manualMode) {
    device.manualMode = manualMode;
    persistStateVersion();
  }
}

void setDeviceOutput(RelayDevice &device, bool turnOn) {
  if (device.isOn != turnOn) {
    device.isOn = turnOn;
    persistStateVersion();
  }
  controlOutput(device.pin, turnOn);
}

void publishDeviceState(const RelayDevice &device) {
  char occurredAt[25];
  if (!utcTimestamp(occurredAt, sizeof(occurredAt))) return;

  JsonDocument document;
  addEnvelopeHeader(document, occurredAt);
  JsonObject payload = document["payload"].to<JsonObject>();
  payload["device_id"] = device.id;
  JsonObject state = payload["state"].to<JsonObject>();
  state["on"] = device.isOn;
  state["mode"] = modeName(device);
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

String availabilityEnvelope(const char *status) {
  char occurredAt[25];
  if (!utcTimestamp(occurredAt, sizeof(occurredAt))) return String();
  JsonDocument document;
  addEnvelopeHeader(document, occurredAt);
  JsonObject payload = document["payload"].to<JsonObject>();
  payload["status"] = status;
  payload["boot_id"] = bootId;
  payload["firmware_version"] = "1.1.0";
  String serialized;
  serializeJson(document, serialized);
  return serialized;
}

void publishGatewayAvailability(const char *status) {
  char occurredAt[25];
  if (!utcTimestamp(occurredAt, sizeof(occurredAt))) return;
  JsonDocument document;
  addEnvelopeHeader(document, occurredAt);
  JsonObject payload = document["payload"].to<JsonObject>();
  payload["status"] = status;
  payload["boot_id"] = bootId;
  payload["firmware_version"] = "1.1.0";
  publishJson(gatewayStatusTopic, document, 1, true);
}

uint16_t readLdrAdc() {
  uint32_t sum = 0;
  for (uint8_t sample = 0; sample < kLdrSamples; ++sample) {
    sum += static_cast<uint16_t>(analogRead(LDR_ANALOG_GPIO));
    delay(3);
  }
  return static_cast<uint16_t>(sum / kLdrSamples);
}

void applyAutomation() {
  motionDetected = digitalRead(PIR_GPIO) == HIGH;
  if (!motionDetected) {
    for (RelayDevice &device : devices) {
      device.manualTarget = false;
      setDeviceOutput(device, false);
    }
    return;
  }

  const bool dhtValid =
      isfinite(temperatureC) && isfinite(humidityPct) &&
      temperatureC >= -20.0F && temperatureC <= 80.0F &&
      humidityPct >= 0.0F && humidityPct <= 100.0F;
  const bool automaticTargets[] = {
      true,
      ldrAdc >= kLdrThreshold,
      dhtValid && temperatureC > kTemperatureThresholdC &&
          humidityPct > kHumidityThresholdPct,
  };

  for (size_t index = 0; index < 3; ++index) {
    RelayDevice &device = devices[index];
    const bool target = device.manualMode ? device.manualTarget : automaticTargets[index];
    setDeviceOutput(device, target);
  }
}

void publishTelemetry() {
  if (!mqttConnected) return;
  if (!isfinite(temperatureC) || !isfinite(humidityPct) ||
      temperatureC < -20.0F || temperatureC > 80.0F ||
      humidityPct < 0.0F || humidityPct > 100.0F) {
    Serial.println("DHT22 reading failed validation; telemetry sample skipped.");
    return;
  }

  char occurredAt[25];
  if (!utcTimestamp(occurredAt, sizeof(occurredAt))) {
    Serial.println("Waiting for network time; telemetry sample skipped.");
    return;
  }

  JsonDocument document;
  document["schema_version"] = "2.0";
  document["message_id"] = newUuid();
  document["occurred_at"] = occurredAt;
  document["site_id"] = SITE_ID;
  document["classroom_id"] = CLASSROOM_ID;
  document["producer_id"] = PRODUCER_ID;
  JsonObject payload = document["payload"].to<JsonObject>();
  payload["sensor_id"] = SENSOR_ID;
  payload["temperature_c"] = temperatureC;
  payload["relative_humidity_pct"] = humidityPct;
  payload["illuminance_adc"] = ldrAdc;
  if (millis() - bootedAt >= kPirWarmupMs) {
    payload["motion_detected"] = motionDetected;
  } else {
    payload["motion_detected"] = nullptr;
  }
  payload["sample_interval_ms"] = kReadIntervalMs;
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
  reportedState["on"] = device.isOn;
  reportedState["mode"] = modeName(device);
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

  applyAutomation();
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
  if (valid && strcmp(action, "set_power") == 0) {
    if (value.size() != 2 || !value["on"].is<bool>() ||
        strcmp(value["mode"] | "", "manual") != 0) {
      reasonCode = "unsupported_power_value";
      valid = false;
    } else {
      requestedPower = value["on"].as<bool>();
      requestedMode = true;
      targetManualMode = true;
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

  if (valid && requestedPower && digitalRead(PIR_GPIO) != HIGH) {
    reasonCode = "pir_master_no_motion";
    valid = false;
  }

  if (!valid) {
    applyAutomation();
    rejectCommand(commandId, deviceId, reasonCode, device);
    return;
  }

  if (requestedMode) {
    setDeviceMode(device, targetManualMode);
    if (targetManualMode && !requestedPower) {
      device.manualTarget = device.isOn;
    }
  }
  if (strcmp(action, "set_power") == 0) {
    device.manualTarget = requestedPower;
  }
  applyAutomation();
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
  }
  incomingBytes = 0;
}

void connectMqtt() {
  if (!WiFi.isConnected() || mqttConnected || mqttConnecting) return;
  gatewayWillPayload = availabilityEnvelope("offline");
  if (gatewayWillPayload.isEmpty()) return;
  mqttConnecting = true;
  lastMqttAttemptAt = millis();
  mqttClient.setWill(
      gatewayStatusTopic.c_str(),
      1,
      true,
      gatewayWillPayload.c_str(),
      gatewayWillPayload.length());
  mqttClient.connect();
}

void publishTelemetryAndStates() {
  if (!mqttConnected) return;
  publishTelemetry();
  for (const RelayDevice &device : devices) {
    publishDeviceState(device);
  }
}

void readSensors() {
  temperatureC = dht.readTemperature();
  humidityPct = dht.readHumidity();
  ldrAdc = readLdrAdc();
  currentAdc = static_cast<uint16_t>(analogRead(ACS712_ANALOG_GPIO));
  motionDetected = digitalRead(PIR_GPIO) == HIGH;
  applyAutomation();

  Serial.println();
  Serial.println("================================");
  Serial.printf("PIR Motion: %s\n", motionDetected ? "DETECTED" : "NOT DETECTED");
  if (!isfinite(temperatureC) || !isfinite(humidityPct)) {
    Serial.println("DHT22: READ ERROR");
  } else {
    Serial.printf("Temperature: %.1f C\nHumidity: %.1f %%\n", temperatureC, humidityPct);
  }
  Serial.printf("LDR ADC (0-4095): %u\nCurrent ADC (raw): %u\n", ldrAdc, currentAdc);
  Serial.println("-------- DEVICE STATUS --------");
  for (const RelayDevice &device : devices) {
    Serial.printf(
        "%s: %s (%s)\n",
        device.name,
        device.isOn ? "ON" : "OFF",
        modeName(device));
  }
  if (!motionDetected) {
    Serial.println("NO MOTION - PIR MASTER OVERRIDE; ALL DEVICES OFF");
  } else {
    Serial.println("MOTION DETECTED - AUTOMATION/MANUAL CONTROL ACTIVE");
  }
  Serial.println("================================");
}

}

void setup() {
  Serial.begin(115200);
  bootedAt = millis();
  dht.begin();
  pinMode(PIR_GPIO, INPUT);
  pinMode(ACS712_ANALOG_GPIO, INPUT);
  pinMode(LDR_ANALOG_GPIO, INPUT);
  analogReadResolution(12);
  analogSetPinAttenuation(ACS712_ANALOG_GPIO, ADC_11db);
  analogSetPinAttenuation(LDR_ANALOG_GPIO, ADC_11db);

  for (RelayDevice &device : devices) {
    digitalWrite(device.pin, kRelayActiveLow ? HIGH : LOW);
    pinMode(device.pin, OUTPUT);
    controlOutput(device.pin, false);
  }

  preferences.begin("aceo-control", false);
  stateVersion = preferences.getUInt("stateVersion", 0);
  loadReceipts();
  commandQueue = xQueueCreate(4, sizeof(QueuedCommand));
  if (commandQueue == nullptr) {
    Serial.println("FATAL: Could not allocate MQTT command queue.");
    while (true) delay(1000);
  }

  telemetryTopic = makeTopic("telemetry/environment");
  gatewayStatusTopic = makeTopic((String("gateway/") + PRODUCER_ID + "/status").c_str());
  for (size_t index = 0; index < 3; ++index) {
    commandTopics[index] =
        makeTopic((String("command/") + devices[index].id + "/set").c_str());
  }

  mqttClient.setServer(MQTT_HOST, MQTT_PORT);
  mqttClient.setClientId(PRODUCER_ID);
  mqttClient.setKeepAlive(30);
  if (strlen(MQTT_USERNAME) > 0) {
    mqttClient.setCredentials(MQTT_USERNAME, MQTT_PASSWORD);
  }
  mqttClient.onConnect([](bool) {
    mqttConnected = true;
    mqttConnecting = false;
    Serial.println("MQTT connected.");
    for (const String &topic : commandTopics) {
      mqttClient.subscribe(topic.c_str(), 1);
    }
    publishGatewayAvailability("online");
    publishAllDeviceStates();
  });
  mqttClient.onDisconnect([](AsyncMqttClientDisconnectReason reason) {
    mqttConnected = false;
    mqttConnecting = false;
    Serial.printf("MQTT disconnected (reason %d).\n", static_cast<int>(reason));
  });
  mqttClient.onMessage(acceptIncomingMqttMessage);

  snprintf(bootId, sizeof(bootId), "%s", newUuid().c_str());
  WiFi.mode(WIFI_STA);
  WiFi.setAutoReconnect(true);
  WiFi.onEvent([](WiFiEvent_t event, WiFiEventInfo_t info) {
    if (event == ARDUINO_EVENT_WIFI_STA_GOT_IP) {
      Serial.print("Wi-Fi connected. ESP32 IP: ");
      Serial.println(WiFi.localIP());
      connectMqtt();
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
  lastSampleAt = millis() - kReadIntervalMs;
  lastMqttAttemptAt = millis() - kReconnectIntervalMs;
}

void loop() {
  if (WiFi.isConnected() && !mqttConnected && !mqttConnecting &&
      millis() - lastMqttAttemptAt >= kReconnectIntervalMs) {
    connectMqtt();
  }

  QueuedCommand command {};
  if (xQueueReceive(commandQueue, &command, 0) == pdTRUE) {
    processCommand(command);
  }

  applyAutomation();
  if (millis() - lastSampleAt >= kReadIntervalMs) {
    lastSampleAt = millis();
    readSensors();
    publishTelemetryAndStates();
  }
  delay(10);
}
#endif
