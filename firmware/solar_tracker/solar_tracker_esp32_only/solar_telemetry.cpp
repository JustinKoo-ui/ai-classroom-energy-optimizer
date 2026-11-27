#include "solar_telemetry.h"

#include <Arduino.h>
#include <WiFi.h>
#include <atomic>
#include <esp_random.h>
#include <esp_timer.h>
#include <freertos/FreeRTOS.h>
#include <freertos/queue.h>
#include <freertos/task.h>
#include <mqtt_client.h>
#include <stdio.h>
#include <string.h>
#include <time.h>

#if __has_include("solar_mqtt_secrets.h")
#include "solar_mqtt_secrets.h"
#else
#include "solar_mqtt_secrets.example.h"
#endif

namespace {
using namespace solar_mqtt_config;

constexpr uint32_t kFirstRetryMs = 3000;
constexpr uint32_t kMaxRetryMs = 60000;
constexpr uint32_t kMqttConnectTimeoutMs = 12000;
constexpr uint32_t kPublishAckTimeoutMs = 12000;
constexpr size_t kMessageCapacity = 1700;

static_assert(kTelemetryIntervalMs >= 250 && kTelemetryIntervalMs <= 60000,
              "Solar telemetry interval must be 250 to 60000 milliseconds");

StaticQueue_t snapshotQueueControl;
alignas(SolarTelemetrySnapshot) uint8_t snapshotStorage[sizeof(SolarTelemetrySnapshot)];
QueueHandle_t snapshotQueue = nullptr;
uint32_t lastCaptureMs = 0;
char telemetryTopic[192];
char statusTopic[192];
char bootId[37];
char clientId[24];

enum class ConnectionState : uint32_t { Connecting, Connected, Failed };

struct JsonNumber {
  char text[32];
};

JsonNumber numberOrNull(double number) {
  JsonNumber result{};
  if (isfinite(number)) {
    snprintf(result.text, sizeof(result.text), "%.12g", number);
  } else {
    strcpy(result.text, "null");
  }
  return result;
}

const char *stopReasonJson(SolarStopReason reason) {
  switch (reason) {
    case SolarStopReason::WaitingForGps: return "\"waiting_for_gps\"";
    case SolarStopReason::WaitingForClock: return "\"waiting_for_clock\"";
    case SolarStopReason::SpaError: return "\"spa_error\"";
    case SolarStopReason::FeedbackUnavailable: return "\"feedback_unavailable\"";
    case SolarStopReason::ZenithLimit: return "\"zenith_limit\"";
    case SolarStopReason::LocalHold: return "\"local_hold\"";
    case SolarStopReason::None: return "null";
  }
  return "null";
}

void createUuid(char (&destination)[37]) {
  uint8_t bytes[16];
  esp_fill_random(bytes, sizeof(bytes));
  bytes[6] = (bytes[6] & 0x0f) | 0x40;
  bytes[8] = (bytes[8] & 0x3f) | 0x80;
  snprintf(destination, sizeof(destination),
           "%02x%02x%02x%02x-%02x%02x-%02x%02x-%02x%02x-%02x%02x%02x%02x%02x%02x",
           bytes[0], bytes[1], bytes[2], bytes[3], bytes[4], bytes[5], bytes[6], bytes[7],
           bytes[8], bytes[9], bytes[10], bytes[11], bytes[12], bytes[13], bytes[14], bytes[15]);
}

void formatUtcTimestamp(char (&destination)[28], time_t utc) {
  strcpy(destination, "null");
  tm brokenDown{};
  if (utc > 0 && gmtime_r(&utc, &brokenDown) != nullptr) {
    strftime(destination, sizeof(destination), "\"%Y-%m-%dT%H:%M:%SZ\"", &brokenDown);
  }
}

void formatNetworkTimestamp(char (&destination)[28], int64_t sampleUs) {
  strcpy(destination, "null");
  if (sampleUs < 0) return;

  // The tracking clock can be set by GPS or the Serial test command. Use only
  // system time, which is synchronized independently by SNTP, on the wire.
  timespec current{};
  clock_gettime(CLOCK_REALTIME, &current);
  if (current.tv_sec < kEarliestSolarNetworkTime) return;
  const int64_t ageUs = esp_timer_get_time() - sampleUs;
  const int64_t epochUs = static_cast<int64_t>(current.tv_sec) * 1000000 +
                          current.tv_nsec / 1000 - ageUs;
  formatUtcTimestamp(destination, epochUs / 1000000);
}

bool formatEnvelope(char (&output)[kMessageCapacity], const char *payload, int64_t sampleUs) {
  char timestamp[28];
  char messageId[37];
  formatNetworkTimestamp(timestamp, sampleUs);
  createUuid(messageId);
  const int count = snprintf(
      output, sizeof(output),
      "{\"schema_version\":\"1.0\",\"message_id\":\"%s\",\"occurred_at\":%s,"
      "\"site_id\":\"%s\",\"classroom_id\":\"%s\",\"producer_id\":\"%s\",\"payload\":%s}",
      messageId, timestamp, kSiteId, kClassroomId, kProducerId, payload);
  return count > 0 && static_cast<size_t>(count) < sizeof(output);
}

bool formatAvailability(char (&output)[kMessageCapacity], bool online) {
  char payload[96];
  snprintf(payload, sizeof(payload), "{\"boot_id\":\"%s\",\"status\":\"%s\"}",
           bootId, online ? "online" : "offline");
  return formatEnvelope(output, payload, online ? esp_timer_get_time() : -1);
}

bool formatTelemetry(char (&output)[kMessageCapacity], const SolarTelemetrySnapshot &sample) {
  char payload[1400];
  char lastFixAge[24] = "null";
  if (sample.lastFixAgeMs >= 0) {
    snprintf(lastFixAge, sizeof(lastFixAge), "%lld", static_cast<long long>(sample.lastFixAgeMs));
  }
  const SolarTrackingSample &tracking = sample.tracking;
  const SolarPowerSample &power = sample.solarPower;
  char yawSequence[280] = "null";
  const SolarYawReport &yaw = tracking.yawSequence;
  if (yaw.started) {
    const char *result = solarYawResultName(yaw.result);
    const int length = snprintf(
        yawSequence, sizeof(yawSequence),
        "{\"kind\":\"%s\",\"phase\":\"%s\",\"leg_progress_deg\":%s,\"leg_target_deg\":%s,"
        "\"net_rotation_deg\":%s,\"result\":%s%s%s}",
        yaw.shortTest ? "test" : "run", solarYawPhaseName(yaw.phase),
        numberOrNull(yaw.legProgressDeg).text, numberOrNull(yaw.legTargetDeg).text,
        numberOrNull(yaw.netRotationDeg).text, result ? "\"" : "", result ? result : "null",
        result ? "\"" : "");
    if (length < 0 || static_cast<size_t>(length) >= sizeof(yawSequence)) return false;
  }
  char fixedYaw[320] = "null";
  const SolarFixedYawSample &fixed = tracking.fixedYaw;
  if (fixed.enabled) {
    char solarTime[28];
    formatUtcTimestamp(solarTime, fixed.solarUtc);
    const int length = snprintf(
        fixedYaw, sizeof(fixedYaw),
        "{\"clock_set\":%s,\"referenced\":%s,\"calibrating\":%s,\"armed\":%s,"
        "\"reachable\":%s,\"status\":\"%s\",\"demo_speed\":%u,\"solar_time\":%s}",
        fixed.clockSet ? "true" : "false", fixed.referenced ? "true" : "false",
        fixed.calibrating ? "true" : "false", fixed.armed ? "true" : "false",
        fixed.reachable ? "true" : "false", solarFixedYawStatusName(fixed.status),
        static_cast<unsigned int>(fixed.demoSpeed), solarTime);
    if (length < 0 || static_cast<size_t>(length) >= sizeof(fixedYaw)) return false;
  }
  const int count = snprintf(
      payload, sizeof(payload),
      "{\"boot_id\":\"%s\",\"gps\":{\"enabled\":%s,\"fix\":%s,\"time_valid\":%s,\"latitude\":%s,"
      "\"longitude\":%s,\"last_fix_age_ms\":%s},"
      "\"tracking\":{\"mode\":\"%s\",\"target_azimuth\":%s,\"actual_azimuth\":%s,"
      "\"target_zenith\":%s,\"actual_zenith\":%s,\"azimuth_pwm\":%lu,"
      "\"zenith_pwm\":%lu,\"stop_reason\":%s,\"yaw_sequence\":%s,\"fixed_yaw\":%s},"
      "\"solar_power\":{\"voltage\":%s,\"current\":%s,\"power\":%s,\"energy\":%s},"
      "\"sensors\":{\"ina219_available\":%s,\"azimuth_mpu_available\":%s,"
      "\"zenith_mpu_available\":%s}}",
      bootId, sample.gpsEnabled ? "true" : "false", sample.gpsFix ? "true" : "false",
      sample.gpsTimeValid ? "true" : "false",
      numberOrNull(sample.latitude).text, numberOrNull(sample.longitude).text, lastFixAge,
      sample.manualMode ? "manual" : "auto",
      numberOrNull(tracking.targetAzimuth).text, numberOrNull(tracking.actualAzimuth).text,
      numberOrNull(tracking.targetZenith).text, numberOrNull(tracking.actualZenith).text,
      static_cast<unsigned long>(sample.azimuthPwm), static_cast<unsigned long>(sample.zenithPwm),
      stopReasonJson(tracking.stopReason), yawSequence, fixedYaw, numberOrNull(power.voltage).text,
      numberOrNull(power.current).text, numberOrNull(power.power).text, numberOrNull(power.energy).text,
      sample.ina219Available ? "true" : "false", sample.azimuthMpuAvailable ? "true" : "false",
      sample.zenithMpuAvailable ? "true" : "false");
  return count > 0 && static_cast<size_t>(count) < sizeof(payload) &&
      formatEnvelope(output, payload, sample.capturedAtUs);
}

void handleMqttEvent(void *argument, esp_event_base_t, int32_t eventId, void *) {
  auto &state = *static_cast<std::atomic<ConnectionState> *>(argument);
  if (eventId == MQTT_EVENT_CONNECTED) {
    state.store(ConnectionState::Connected);
  } else if (eventId == MQTT_EVENT_DISCONNECTED || eventId == MQTT_EVENT_ERROR) {
    state.store(ConnectionState::Failed);
  }
}

bool queuePublish(esp_mqtt_client_handle_t client, const char *topic, const char *message,
                  bool retained) {
  const int result = esp_mqtt_client_enqueue(client, topic, message, 0, 1, retained, true);
  if (result >= 0) return true;
  Serial.println("Solar MQTT could not enqueue a message.");
  return false;
}

void publishWhileConnected(esp_mqtt_client_handle_t client, std::atomic<ConnectionState> &state) {
  char message[kMessageCapacity];
  if (!formatAvailability(message, true) || !queuePublish(client, statusTopic, message, true)) {
    return;
  }

  uint32_t lastQueuedMs = millis();
  while (state.load() == ConnectionState::Connected && WiFi.isConnected()) {
    if (esp_mqtt_client_get_outbox_size(client) == 0) {
      SolarTelemetrySnapshot snapshot{};
      if (xQueueReceive(snapshotQueue, &snapshot, 0) == pdTRUE) {
        const int64_t maximumAgeUs = (kTelemetryIntervalMs + 250) * 1000LL;
        if (esp_timer_get_time() - snapshot.capturedAtUs <= maximumAgeUs) {
          if (formatTelemetry(message, snapshot)) {
            if (!queuePublish(client, telemetryTopic, message, false)) return;
            lastQueuedMs = millis();
          } else {
            Serial.println("Solar MQTT snapshot exceeded the JSON buffer.");
          }
        }
      }
    } else if (millis() - lastQueuedMs >= kPublishAckTimeoutMs) {
      Serial.println("Solar MQTT publish acknowledgement timed out.");
      return;
    }
    vTaskDelay(pdMS_TO_TICKS(100));
  }
}

bool runMqttSession() {
  char will[kMessageCapacity];
  if (!formatAvailability(will, false)) return false;

  esp_mqtt_client_config_t config{};
  config.broker.address.hostname = kBrokerHost;
  config.broker.address.port = kBrokerPort;
  config.broker.address.transport = kUseTls ? MQTT_TRANSPORT_OVER_SSL : MQTT_TRANSPORT_OVER_TCP;
  if (kUseTls) config.broker.verification.certificate = kBrokerCaPem;
  config.credentials.client_id = clientId;
  config.credentials.username = kBrokerUsername;
  config.credentials.authentication.password = kBrokerPassword;
  config.session.last_will.topic = statusTopic;
  config.session.last_will.msg = will;
  config.session.last_will.qos = 1;
  config.session.last_will.retain = true;
  config.session.keepalive = 15;
  config.session.protocol_ver = MQTT_PROTOCOL_V_3_1_1;
  config.network.disable_auto_reconnect = true;
  config.network.timeout_ms = 6000;
  config.task.priority = tskIDLE_PRIORITY + 1;
  config.buffer.size = kMessageCapacity + sizeof(telemetryTopic);
  config.outbox.limit = config.buffer.size;

  esp_mqtt_client_handle_t client = esp_mqtt_client_init(&config);
  if (client == nullptr) {
    Serial.println("Solar MQTT client initialization failed.");
    return false;
  }

  std::atomic<ConnectionState> state{ConnectionState::Connecting};
  const bool started = esp_mqtt_client_register_event(client, MQTT_EVENT_ANY, handleMqttEvent,
                                                     &state) == ESP_OK &&
                       esp_mqtt_client_start(client) == ESP_OK;
  bool connected = false;
  if (started) {
    const uint32_t startedMs = millis();
    while (state.load() == ConnectionState::Connecting && WiFi.isConnected() &&
           millis() - startedMs < kMqttConnectTimeoutMs) {
      vTaskDelay(pdMS_TO_TICKS(100));
    }
    connected = state.load() == ConnectionState::Connected;
    if (connected) {
      Serial.println("Solar MQTT connected.");
      publishWhileConnected(client, state);
    }
    esp_mqtt_client_stop(client);
  }
  // Each retry gets a clean session and a new outbox. Never replay old samples
  // or an old retained status after reconnecting.
  esp_mqtt_client_destroy(client);
  Serial.println(connected ? "Solar MQTT disconnected." : "Solar MQTT connection failed.");
  return connected;
}

uint32_t increaseRetryDelay(uint32_t retryMs) {
  return retryMs >= kMaxRetryMs / 2 ? kMaxRetryMs : retryMs * 2;
}

void networkTask(void *) {
  createUuid(bootId);
  snprintf(clientId, sizeof(clientId), "aceo-solar-%012llx",
           static_cast<unsigned long long>(ESP.getEfuseMac() & 0xFFFFFFFFFFFFULL));
  WiFi.mode(WIFI_STA);
  WiFi.setAutoReconnect(false);
  WiFi.begin(kWiFiSsid, kWiFiPassword);
  uint32_t lastWifiAttemptMs = millis();
  uint32_t lastMqttSessionMs = 0;
  uint32_t wifiRetryMs = kFirstRetryMs;
  uint32_t mqttRetryMs = 0;
  bool wifiPreviouslyConnected = false;
  bool clockSyncStarted = false;

  for (;;) {
    if (!WiFi.isConnected()) {
      if (wifiPreviouslyConnected) {
        wifiPreviouslyConnected = false;
        Serial.println("Solar Wi-Fi disconnected.");
      }
      if (millis() - lastWifiAttemptMs >= wifiRetryMs) {
        WiFi.reconnect();
        lastWifiAttemptMs = millis();
        wifiRetryMs = increaseRetryDelay(wifiRetryMs);
      }
      vTaskDelay(pdMS_TO_TICKS(250));
      continue;
    }
    if (!wifiPreviouslyConnected) {
      wifiPreviouslyConnected = true;
      wifiRetryMs = kFirstRetryMs;
      mqttRetryMs = 0;
      Serial.println("Solar Wi-Fi connected.");
    }
    if (!clockSyncStarted) {
      if (kNtpServer[0] != '\0') configTime(0, 0, kNtpServer);
      clockSyncStarted = true;
      if (kUseTls && time(nullptr) < kEarliestSolarNetworkTime) {
        Serial.println("Solar MQTT TLS waiting for network time.");
      }
    }
    if ((kUseTls && time(nullptr) < kEarliestSolarNetworkTime) ||
        millis() - lastMqttSessionMs < mqttRetryMs) {
      vTaskDelay(pdMS_TO_TICKS(250));
      continue;
    }

    const bool connected = runMqttSession();
    lastMqttSessionMs = millis();
    mqttRetryMs = connected || mqttRetryMs == 0 ? kFirstRetryMs : increaseRetryDelay(mqttRetryMs);
  }
}

bool validTopicComponent(const char *value) {
  return value[0] != '\0' &&
      strspn(value, "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_") == strlen(value);
}

bool configureTopics() {
  if (!validTopicComponent(kSiteId) || !validTopicComponent(kClassroomId) ||
      !validTopicComponent(kProducerId)) return false;
  const int telemetryLength = snprintf(telemetryTopic, sizeof(telemetryTopic),
                                      "aceo/v1/%s/%s/solar/%s/telemetry",
                                      kSiteId, kClassroomId, kProducerId);
  const int statusLength = snprintf(statusTopic, sizeof(statusTopic), "aceo/v1/%s/%s/solar/%s/status",
                                   kSiteId, kClassroomId, kProducerId);
  return telemetryLength > 0 && static_cast<size_t>(telemetryLength) < sizeof(telemetryTopic) &&
      statusLength > 0 && static_cast<size_t>(statusLength) < sizeof(statusTopic);
}
}  // namespace

void startSolarTelemetry() {
  if (!kEnabled) {
    Serial.println("Solar MQTT disabled. Configure solar_mqtt_secrets.h to enable it.");
    return;
  }
  if (!kWiFiSsid[0] || !kBrokerHost[0] || !kBrokerPort || !kBrokerUsername[0] ||
      !kBrokerPassword[0] || (kUseTls && (!kBrokerCaPem[0] || !kNtpServer[0])) ||
      !configureTopics()) {
    Serial.println("Solar MQTT disabled because its configuration is incomplete.");
    return;
  }
  snapshotQueue = xQueueCreateStatic(1, sizeof(SolarTelemetrySnapshot), snapshotStorage,
                                    &snapshotQueueControl);
  lastCaptureMs = millis() - kTelemetryIntervalMs;
  if (xTaskCreate(networkTask, "solar-network", 10240, nullptr, tskIDLE_PRIORITY + 1,
                  nullptr) != pdPASS) {
    snapshotQueue = nullptr;
    Serial.println("Solar MQTT worker could not start.");
  }
}

bool solarTelemetryDue(uint32_t nowMs) {
  if (snapshotQueue == nullptr || nowMs - lastCaptureMs < kTelemetryIntervalMs) return false;
  lastCaptureMs = nowMs;
  return true;
}

void queueSolarTelemetry(const SolarTelemetrySnapshot &snapshot) {
  xQueueOverwrite(snapshotQueue, &snapshot);
}
