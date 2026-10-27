# Objective Traceability

| # | Objective | Primary components | Persisted evidence / outward contract |
|---:|---|---|---|
| 1 | Detect occupancy | CV worker, MQTT ingestion, state estimator | `occupancy_observations`, occupancy REST |
| 2 | Count occupants | Roboflow detector, ByteTrack deduplication | Aggregate count and confidence only |
| 3 | Track without facial recognition | Process-local ByteTrack | No frames, faces or track IDs in database or occupancy API; optional authenticated live preview has no retention |
| 4 | Predict departure within 60s | Feature builder, prediction model | `prediction_runs`, prediction REST |
| 5 | Maintain about 26 °C | Comfort rules, AC controller | Versioned policy decision and command audit |
| 6 | Adjust fans | Temperature/humidity band rules | Device state, policy decision and command events |
| 7 | Adjust lighting | Occupancy/lux rules with hysteresis | Lux readings, decision and lighting command |
| 8 | Shut off after vacancy | Vacancy state machine, grace timers | Continuous observations and matched rule |
| 9 | Measure consumption | Energy meter, ingestion, aggregation | `energy_readings`, energy REST |
| 10 | Estimate savings | Versioned counterfactual baseline | `energy_savings_estimates` with quality/method |
| 11 | Manual device control | REST control request, authorization | Request → decision → command → acknowledgement |
| 12 | Schedules | Scheduler, RRULE evaluation | Schedule/run and linked control request |
| 13 | Manual override | Override service and policy precedence | Owner, reason, duration and status audit |
| 14 | Historical analytics | PostgreSQL time series, aggregation API | Occupancy/environment/energy history endpoints |
| 15 | Display AI predictions | Prediction REST, dashboard polling | Probability, horizon, model version and expiry |

Every control objective routes through the deterministic policy. Objectives 1–4
produce observations or advice and have no physical command authority.
