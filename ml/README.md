# Prediction model

Boundary for feature building, training, evaluation and inference of the event:
"all current occupants leave within 60 seconds." Model output is a calibrated
probability with model/feature versions and an expiry time, never a command.

Use time-based train/validation/test splits, record calibration metrics, prevent
future leakage and keep reproducible artifact hashes. `artifacts/` is ignored
except for its placeholder; deploy model files through artifact storage.

