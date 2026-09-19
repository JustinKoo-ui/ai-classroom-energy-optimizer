# ESP32 firmware boundary

Firmware reads classroom sensors, publishes telemetry and controls its assigned
appliance through versioned MQTT messages. Each controller must:

- use a unique TLS/broker identity and last-will availability message;
- validate topic identity, schema, expiry, action and capability;
- persist recently applied command IDs for QoS 1 deduplication;
- enforce local electrical/switching interlocks even for approved commands;
- acknowledge `accepted`, then `applied`, or return `rejected`/`failed`; and
- fail to a documented safe state when communications or sensing fails.

High-power mains wiring and relay selection require qualified supervision.

