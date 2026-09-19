# Computer-vision worker

Boundary for camera capture, Roboflow person detection and ByteTrack association.
It publishes only aggregate occupancy observations using `docs/mqtt.md`.

Rules:

- keep anonymous track IDs in process memory only;
- do not perform facial recognition or create biometric identifiers;
- do not persist frames/crops by default;
- never publish or request appliance commands; and
- emit health/staleness signals so loss of vision becomes `unknown`, not vacant.

