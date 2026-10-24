"""Stable logical device identities for the localhost ESP32 relay prototype."""

from uuid import UUID

LOCAL_SITE_ID = UUID("c7ef6d75-7c12-461a-bb49-73f917c3357d")
LOCAL_CLASSROOM_ID = UUID("26e97d46-7a34-4439-922a-83684bda557d")

FAN_DEVICE_ID = UUID("d7958a13-4c75-47e8-8b5e-4f4e00000027")
LIGHT_DEVICE_ID = UUID("d7958a13-4c75-47e8-8b5e-4f4e00000012")
AC_DEVICE_ID = UUID("d7958a13-4c75-47e8-8b5e-4f4e00000014")
LOCAL_RELAY_DEVICE_IDS = frozenset((FAN_DEVICE_ID, LIGHT_DEVICE_ID, AC_DEVICE_ID))

LOCAL_RELAY_DEVICES = {
    "fan": {
        "id": FAN_DEVICE_ID,
        "name": "Ceiling fan",
        "type": "fan",
        "gpio": 27,
        "hardware_uid": "aceo-local-relay-fan",
        "mqtt_client_id": "aceo-room-a204-fan",
    },
    "lighting": {
        "id": LIGHT_DEVICE_ID,
        "name": "Lighting",
        "type": "lighting",
        "gpio": 12,
        "hardware_uid": "aceo-local-relay-lighting",
        "mqtt_client_id": "aceo-room-a204-lighting",
    },
    "air_conditioner": {
        "id": AC_DEVICE_ID,
        "name": "Air conditioner",
        "type": "air_conditioner",
        "gpio": 14,
        "hardware_uid": "aceo-local-relay-air-conditioner",
        "mqtt_client_id": "aceo-room-a204-air-conditioner",
    },
}

LOCAL_OPERATOR_ID = UUID("fce4dbe8-c175-4ec2-9ca5-eaf5f0682dd5")
