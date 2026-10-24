#pragma once

// Copy this file to secrets.h and set local values. Never commit secrets.h.
#define WIFI_SSID "replace-with-2.4ghz-wifi-name"
#define WIFI_PASSWORD "replace-with-wifi-password"

// Use the computer's LAN IPv4 address, not localhost or 127.0.0.1.
#define MQTT_HOST "192.168.1.10"
#define MQTT_PORT 1883
#define MQTT_USERNAME "aceo-esp32-room-a204"
#define MQTT_PASSWORD "replace-with-local-device-password"

#define SITE_ID "c7ef6d75-7c12-461a-bb49-73f917c3357d"
#define CLASSROOM_ID "26e97d46-7a34-4439-922a-83684bda557d"
#define SENSOR_ID "esp32-room-a204"
#define PRODUCER_ID "esp32-room-a204"
#define COMMAND_DISPATCHER_PRODUCER_ID "command-dispatcher-local"

#define FAN_DEVICE_ID "d7958a13-4c75-47e8-8b5e-4f4e00000027"
#define LIGHT_DEVICE_ID "d7958a13-4c75-47e8-8b5e-4f4e00000012"
#define AC_DEVICE_ID "d7958a13-4c75-47e8-8b5e-4f4e00000014"

// Verify each GPIO against the exact ESP32 board and sensor breakout.
#define DHT22_GPIO 4
#define ACS712_ANALOG_GPIO 34
#define LDR_ANALOG_GPIO 35
#define PIR_GPIO 21
