#pragma once

#include <stdint.h>

namespace solar_mqtt_config {
inline constexpr bool kEnabled = false;
inline constexpr char kWiFiSsid[] = "Xiaomi 15T Pro";
inline constexpr char kWiFiPassword[] = "123456789";

inline constexpr char kBrokerHost[] = "";
inline constexpr uint16_t kBrokerPort = 1883;
inline constexpr char kBrokerUsername[] = "aceo-esp32-solar-tracker";
inline constexpr char kBrokerPassword[] = "123456";
inline constexpr bool kUseTls = true;
inline constexpr char kBrokerCaPem[] = "";
inline constexpr char kNtpServer[] = "pool.ntp.org";

inline constexpr char kSiteId[] = "c7ef6d75-7c12-461a-bb49-73f917c3357d";
inline constexpr char kClassroomId[] = "26e97d46-7a34-4439-922a-83684bda557d";
inline constexpr char kProducerId[] = "solar-tracker-esp32";
inline constexpr uint32_t kTelemetryIntervalMs = 1000;
}  // namespace solar_mqtt_config
