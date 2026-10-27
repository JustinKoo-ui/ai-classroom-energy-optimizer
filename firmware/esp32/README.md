# ESP32 classroom prototype

The ESP32 DevKit V1 firmware preserves the supplied DHT22/PIR/LDR sensor
acquisition and MQTT telemetry path. Output polarity is configured per device
in `include/relay_config.h`: fan, AC, and the yellow lighting output are
active HIGH (logical OFF drives LOW; logical ON drives HIGH). It
samples sensors every two seconds, publishes fresh snapshots when the previous
MQTT batch is acknowledged, and subscribes to
policy-approved ON/OFF and Auto/Manual commands for the fan, light, and AC
outputs. The backend deterministic policy is the only automation decision
maker; the ESP32 validates and applies commands, reports outputs, and does not
let a LOW PIR sample override occupancy, urgency, or manual state. PIR is
motion detection, never an occupant count.

For the localhost low-voltage prototype, the backend applies these configured
rules: fresh occupancy >= 1 immediately requests fan ON. Automatic lighting
requires fresh PIR motion first, then a fresh valid `LDR ADC > 1000`, independent
of the camera count. No motion always requests light OFF, even at ADC 4095.
During motion, ADC > 1000 requests ON and ADC <= 1000 requests OFF. AC requires
continuous occupancy >= 3 for at least 5 seconds and either fresh
valid temperature >=26°C or humidity >=65%; loss of qualification requests AC
OFF. Camera observations older than 15 seconds become unknown. With no
confirmed occupant (valid zero or unknown), a fresh PIR rising motion event
starts a non-blocking 20-second camera-scanning window. Any detected
person cancels the scan timeout. Lighting follows the same PIR-then-LDR gates
during scanning, handoff and normal operation; no timer forces it ON.
The fan and AC remain OFF without confirmed occupants.
Sustained PIR HIGH does not restart a scan; LOW re-arms
the next event. Per-device Manual mode persists until explicitly switched back
to Auto. These decisions live in the backend, not this firmware.

Urgency is submitted through the localhost REST API and the same policy,
outbox, MQTT dispatcher, and ESP32 command path. It commands all three outputs
on for a fixed five minutes. At expiry, a fresh zero count commands all
outputs off; a fresh nonzero count returns control to the previous per-device
mode. Unknown occupancy is not treated as confirmed absence.

The local hardware API is disabled by default, loopback-only, and intended for
an isolated, low-voltage LED/relay test. Do not connect mains-powered
appliances to this prototype. Fan speed, light brightness, AC temperature,
power, and energy are not supported by this digital-output firmware.

## Test sensors without Wi-Fi or MQTT

The sensor-test build prints local readings to the serial monitor only. It
does not connect to Wi-Fi, publish MQTT, or require `include\secrets.h`.

1. Keep ACS712 OUT disconnected from GPIO 34 unless you have confirmed the
   voltage at the ESP32 pin stays within 0–3.3 V over the full expected range,
   or added suitable signal conditioning. Power off before changing wiring.
2. From this directory, upload the sensor-test firmware (replace `COM11` if
   Windows shows a different port):

   ```powershell
   uv tool run --from platformio pio run -e esp32dev_sensor_test -t upload --upload-port COM11
   uv tool run --from platformio pio device monitor --port COM11 --baud 115200
   ```
3. It prints DHT22 temperature/humidity, PIR state, LDR ADC counts, and ACS712
   ADC/millivolts every two seconds. DHT22 errors usually mean checking data
   wiring and the pull-up. PIR says warming up for 30 seconds. Cover/uncover
   the LDR to see its ADC counts change; these are not lux. Move in front of
   the PIR after its warmup to check its output.
4. When sensor testing is complete, upload the normal MQTT telemetry build
   instead with `pio run -t upload --upload-port COM11`. That build requires a
   configured `include\secrets.h`.

## Local setup

1. Create three local Mosquitto credentials before starting the broker. From
   the repository root in PowerShell:

   ```powershell
   $mqttConfig = (Resolve-Path .\infra\mosquitto).Path
   docker run --rm -it --mount "type=bind,source=$mqttConfig,target=/mosquitto/config" eclipse-mosquitto:2 mosquitto_passwd -c /mosquitto/config/password_file aceo-esp32-room-a204
   docker run --rm -it --mount "type=bind,source=$mqttConfig,target=/mosquitto/config" eclipse-mosquitto:2 mosquitto_passwd /mosquitto/config/password_file aceo-backend-ingestion
   docker run --rm -it --mount "type=bind,source=$mqttConfig,target=/mosquitto/config" eclipse-mosquitto:2 mosquitto_passwd /mosquitto/config/password_file aceo-backend-dispatcher
   ```

   Enter a different strong password for each prompt. The generated password
   file is ignored by Git. Put the matching ingestion/dispatcher credentials in
   `backend\.env`, and the ESP32 credential in `include\secrets.h`.
   The backend values use these usernames:

   ```text
   MQTT_USERNAME=aceo-backend-ingestion
   MQTT_PASSWORD=<the password entered for aceo-backend-ingestion>
   MQTT_DISPATCHER_USERNAME=aceo-backend-dispatcher
   MQTT_DISPATCHER_PASSWORD=<the password entered for aceo-backend-dispatcher>
   LOCAL_HARDWARE_CONTROL_ENABLED=true
   ```

   Keep the passwords local; do not put them in frontend `NEXT_PUBLIC_*`
   variables or commit them.
2. Start the local broker and backend from the repository root:

   ```powershell
   docker compose up -d
   Get-Content database\migrations\002_environment_raw_adc.sql |
     docker exec -i aceo-postgres psql -U aceo -d aceo
   Get-Content database\migrations\003_occupancy_automation_state.sql |
     docker exec -i aceo-postgres psql -U aceo -d aceo
   Set-Location backend
   if (-not (Test-Path .env)) { Copy-Item ..\.env.example .env }
   uv sync --locked --extra dev
   # Set LOCAL_HARDWARE_CONTROL_ENABLED=true and MQTT credentials in backend\.env.
   uv run python scripts/seed_local_classroom.py
   uv run python -m uvicorn app.main:app --reload --port 8000
   ```

   For an existing broker container, restart it after changing its config with
   `docker compose restart mqtt`. The database migration is safe to run against
   fresh and existing local databases.
3. Find the computer's private LAN IPv4 address with `ipconfig`. Copy
   `include\secrets.example.h` to `include\secrets.h` and set Wi-Fi credentials,
   the LAN address as `MQTT_HOST`, and the local ESP32 broker password. Do not
   use `localhost` for `MQTT_HOST`; on the ESP32, that refers to the board.
   Keep the site/classroom IDs and device IDs aligned with the seeded local
   fixture and the checked-in local broker ACL.
4. Connect sensors and verify board GPIO levels before powering the ESP32:

   | Signal | Default GPIO |
   |---|---:|
   | DHT22 data | 4 |
   | PIR digital output | 21 |
   | ACS712 analog output | 34 |
   | LM393 analog output (AO) | 35 |
   | Yellow light relay/LED | 12 |
   | Blue AC relay/LED | 14 |
   | Red fan relay/LED | 27 |

   Join sensor ground to ESP32 ground. Power the DHT22 from 3.3 V and connect
   its data pin to GPIO 4; a bare DHT22 needs a data pull-up resistor, while a
   breakout may already include one. Power the LDR module at 3.3 V and connect
   AO (not DO) to GPIO 35. Connect PIR OUT to GPIO 21 only after confirming
   that its output never exceeds the ESP32's 3.3 V input limit. Check the
   ACS712 OUT voltage across its full range before connecting it to GPIO 34;
   many ACS712 modules are powered at 5 V and can output above 3.3 V. Add a
   properly designed divider/conditioning circuit if needed. Allow the PIR to
   stabilize for 30 seconds. Verify each output's polarity on LEDs or an isolated
   low-voltage test load. The yellow output uses active HIGH
   (logical ON = GPIO 12 HIGH, OFF = LOW), as do fan and AC.
5. From this directory, build and upload with PlatformIO:

   ```powershell
   uv tool run --from platformio pio run
   uv tool run --from platformio pio run -t upload --upload-port COM4
   uv tool run --from platformio pio device monitor --port COM4 --baud 115200
   ```

   Replace `COM4` with the serial port shown by Windows Device Manager.
6. In `frontend\.env.local`, enable only the localhost UI and point it at the
   local backend:

   ```text
   NEXT_PUBLIC_API_BASE_URL=http://localhost:8000
   NEXT_PUBLIC_CLASSROOM_ID=26e97d46-7a34-4439-922a-83684bda557d
   NEXT_PUBLIC_ENABLE_HARDWARE_CONTROLS=true
   ```

   Start `pnpm dev` in `frontend` and open `http://localhost:3000`. The
   Cloudflare-hosted page remains demo-only and cannot send hardware commands.
   In Auto mode, the tested sensor rules own the outputs; switch a relay to
   Manual before requesting ON/OFF from the dashboard.
7. Keep the serial monitor open and check the backend response:

   ```powershell
   Invoke-WebRequest http://localhost:8000/api/v1/classrooms/26e97d46-7a34-4439-922a-83684bda557d/telemetry/latest
   ```

   On startup, serial output should show `Wi-Fi connected` with the ESP32's
   assigned IP, followed by `MQTT connected.` and queued telemetry. If it only
   shows `Connecting to Wi-Fi...`, check the configured SSID/password and use a
   2.4 GHz network. If Wi-Fi connects but MQTT does not, confirm `MQTT_HOST` is
   the computer's current LAN IPv4 address, the broker is running on port
   1883, all three broker users were created, and the firmware/backend
   passwords match the local password file.

## Yellow light behaves opposite to the dashboard

The backend Auto rule is `fresh PIR motion AND fresh valid LDR ADC > 1000`.
Without motion, lighting is requested OFF at every ADC value. With motion,
ADC 1000 or below requests OFF and ADC 1001 or above requests ON. Do not reverse
this comparison to compensate for an electrically inverted output.

The yellow LED output is configured active HIGH in `include/relay_config.h`:
logical OFF drives GPIO 12 LOW and logical ON drives it HIGH. The same
per-device mapping is used for boot OFF and every approved command. Fan and
AC use active HIGH.
Restart the backend to load a changed LDR threshold. If changing the output
mapping, rebuild and upload the normal `esp32dev` firmware; restarting the
backend alone does not change the ESP32's output mapping.

After upload, use Manual mode to compare logical ON/OFF with the physical
yellow light, then return Lighting to Auto and test this table:

| Fresh PIR motion | LDR ADC | Yellow light | GPIO 12 |
|---|---|---|---|
| No | Any value, including > 1000 | OFF | LOW |
| Yes | < 1000 | OFF | LOW |
| Yes | = 1000 | OFF | LOW |
| Yes | > 1000 | ON | HIGH |

Stale or invalid motion/LDR data also requests OFF in Auto. Manual mode and
the five-minute urgency override retain their existing priority.
Serial diagnostics show logical state, GPIO level, and configured polarity;
reported MQTT state confirms the commanded state, not physical light feedback.

The local ACL gives the ESP32 only its telemetry/state/ack publishes and its
three exact command subscriptions. The backend dispatcher alone can publish
commands; the ingestion user cannot. Commands are QoS 1 and never retained.
This plaintext broker is for a trusted local development network only. Do not
expose port 1883 to the public internet or deploy this local control API.

## Commands time out while sensor readings still arrive

The full UUID-based command topics are 130 bytes long. AsyncMqttClient's default
incoming-topic limit is 128 bytes; it silently discards longer topics before
the firmware's command callback runs. Telemetry can still publish normally,
so working sensor readings do not prove that command reception works.

The firmware sets the incoming-topic limit to 191 bytes, matching its 192-byte
topic buffer including the terminating null. Rebuild and upload the normal
`esp32dev` firmware after applying this change. Existing timed-out commands
are not replayed; submit a new request from the dashboard.

Switch Lighting to Manual and wait for the controller's reported mode to
change before using the power switch. The serial monitor should show
`Queued MQTT command` followed by `Applied MQTT command` for each request.
An HTTP 200 from the command-status API only confirms that the status was
retrieved; an `applied` command status confirms the ESP32 acknowledgement.

## Readings repeatedly become stale

First distinguish a disconnected ESP32 from a slow publication queue. Check
`http://localhost:8000/health/ready` and the serial monitor: backend MQTT can
be connected while the board is still disconnected. Compare `MQTT_HOST` in
the ignored `include/secrets.h` with the computer's current Wi-Fi IPv4 address
from `ipconfig`. DHCP or switching hotspots can change that address. Update
the host, rebuild, and upload `esp32dev`; changing the header alone does not
update firmware already on the board. Reserve the computer's address in the
router's DHCP settings where available to avoid repeating this problem.

If flashing reports `Wrong boot mode`, close any serial monitor or competing
uploader, hold BOOT, tap EN/RESET, then release BOOT to enter download mode
before retrying. Only one process can use the board's serial port at a time.
After upload, verify that `telemetry/latest` returns a new `observed_at` and
`freshness: "fresh"`; MQTT connectivity alone does not prove new sensor data.

Sensor acquisition runs every two seconds. The MQTT library sends QoS 1
publications serially, waiting for broker acknowledgements. Enqueuing a new
environment reading and three relay states every two seconds without waiting
can build a growing backlog when delivery is slower than acquisition. Messages
then arrive with old timestamps even though Wi-Fi and MQTT remain connected.

Routine snapshots are now submitted only after earlier publications have been
acknowledged. Skipped snapshots are replaced by the next sensor sample rather
than queued for later delivery. Command acknowledgements and immediate device
state reports remain independent of this periodic gate. A clean MQTT session
discards old queued publications after reconnecting.

The serial monitor prints the board's UTC time, MQTT connection state, pending
publish count and Wi-Fi RSSI. If readings remain stale with this firmware,
compare board UTC with the computer's UTC and check for connection loss or poor
Wi-Fi. A rising publication backlog is a delivery problem, not a reason to
extend safety freshness limits. Environment readings currently expire after
six seconds for this two-second sampling interval; device states expire after
fifteen seconds. Rebuild and upload `esp32dev` to use the queue fix.

The normal firmware disables Wi-Fi modem sleep to reduce receive latency for
this powered controller. This uses more radio power and does not eliminate
packet loss or interference. If MQTT stays connected but pending publications
take many seconds to drain, test the board's network path from the computer:

```powershell
ping.exe -n 20 -w 1000 192.168.50.190
```

Replace the address with the board's current IP from the serial monitor. If
there are lost replies or large latency spikes, move the board closer to the
router and repeat the test. Freshness can remain steady only while new samples
arrive within the freshness window; missing samples must still become stale.

## Sensor limitations and electrical safety

- Replace an LDR module damaged by incorrect VCC/GND wiring before validating
  the 1000 ADC threshold; software cannot repair the damaged hardware.
- LM393 is a comparator board, not a lux meter. Firmware sends averaged ADC
  counts, and the dashboard labels them as uncalibrated counts. A lux meter is
  needed before converting those readings to lux.
- The ACS712 input on GPIO 34 is displayed as raw ADC counts only. A
  current-only measurement does not establish active power or energy, and its
  output must be proven safe for the ESP32 ADC.
  Do not connect it to mains wiring as a beginner project; use an isolated,
  low-voltage test load or qualified electrical supervision and a properly
  rated isolated measurement setup.
- The dashboard polls environment and device state through REST; the browser
  never connects to MQTT. Only temperature, humidity and raw LDR ADC counts
  are live readings. Occupancy, power, energy, savings, and advanced device
  settings remain demo/simulated data.
- Firmware compilation is not hardware-in-the-loop validation. Test only on a
  supervised isolated low-voltage load; do not use mains-powered appliances.
