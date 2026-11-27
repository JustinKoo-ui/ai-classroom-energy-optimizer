# ESP32 classroom prototype

The PlatformIO `esp32dev` firmware publishes a complete sensor, relay and DC
energy snapshot every two seconds through the existing MQTT gateway. The
`esp32dev_sensor_test` build reads the same sensors locally over serial without
requiring Wi-Fi, MQTT credentials or connected relays.

## Wiring

| Function | ESP32 connection | Details |
|---|---|---|
| DHT22 | GPIO 4 | Temperature and humidity |
| PIR | GPIO 23 | Digital motion output with input pull-down |
| BH1750 | GPIO 21 SDA, 22 SCL | First I²C bus, address `0x23` |
| INA219 | GPIO 32 SDA, 33 SCL | Second I²C bus, address `0x40` |
| Relay 1, light | GPIO 12 | Active HIGH |
| Relay 2, fan | GPIO 14 | Active HIGH |
| Relay 3, air conditioner | GPIO 27 | Active HIGH |

Use common grounds on the logic side, 3.3 V-compatible signals and pull-ups on
both I²C buses. A bare DHT22 needs a data pull-up. Verify the PIR output voltage
and relay input compatibility for your breakout boards. A LOW PIR signal can
mean no motion or a disconnected sensor. GPIO 12 is an ESP32
boot strapping pin. Verify that the relay circuit allows the correct reset
level and stays off at power-on. The existing MQTT relay UUIDs identify logical
devices and stay the same after the fan and AC pins move.

The INA219 measures the isolated low-voltage **DC circuit** through its shunt.
The bus reading is VIN− relative to GND. Verify the board's shunt rating and
DC voltage/current limits before testing, and check that forward load current
reads positive. The INA219 chip supports bus voltages up to 26 V DC. Keep the
INA219 and prototype relay test wiring away from 230 V AC mains. Measuring
actual AC appliance energy requires a purpose-built isolated AC energy meter
rated for the installation. [TI INA219 datasheet](https://www.ti.com/product/INA219)
and [ESP32 hardware design guidelines](https://docs.espressif.com/projects/esp-hardware-design-guidelines/en/latest/esp32/schematic-checklist.html).

## Start Mosquitto and FastAPI

Reuse the existing broker accounts, topic ACLs, password file and backend
configuration. For a first-time setup from the repository root, create the
password file and set a different password for each account. Use `-c` for the
first command only when the file does not exist.

```powershell
$mqttConfig = (Resolve-Path .\infra\mosquitto).Path
docker run --rm -it --mount "type=bind,source=$mqttConfig,target=/mosquitto/config" eclipse-mosquitto:2 mosquitto_passwd -c /mosquitto/config/password_file aceo-esp32-room-a204
docker run --rm -it --mount "type=bind,source=$mqttConfig,target=/mosquitto/config" eclipse-mosquitto:2 mosquitto_passwd /mosquitto/config/password_file aceo-backend-ingestion
docker run --rm -it --mount "type=bind,source=$mqttConfig,target=/mosquitto/config" eclipse-mosquitto:2 mosquitto_passwd /mosquitto/config/password_file aceo-backend-dispatcher
```

Place the matching ingestion and dispatcher usernames and passwords in the
ignored `backend\.env` as `MQTT_USERNAME`, `MQTT_PASSWORD`,
`MQTT_DISPATCHER_USERNAME` and `MQTT_DISPATCHER_PASSWORD`. Configure
`MQTT_HOST=localhost` and `MQTT_PORT=1883` for a backend running on the broker
host. Leave `LOCAL_HARDWARE_CONTROL_ENABLED=false` for monitoring only. Set it
to `true` to test approved controls on an isolated low-voltage load.

From the repository root, start the services and apply missing migrations to
the existing database. Compose installs the full schema on a fresh volume.
The migration loop can also be used on a fresh installation.

```powershell
docker compose up -d --wait
Get-ChildItem .\database\migrations\*.sql | Sort-Object Name | ForEach-Object {
  Get-Content $_.FullName -Raw | docker exec -i aceo-postgres psql -v ON_ERROR_STOP=1 -U aceo -d aceo
  if ($LASTEXITCODE -ne 0) { throw "Migration failed: $($_.Name)" }
}
Set-Location backend
if (-not (Test-Path .env)) { Copy-Item ..\.env.example .env }
uv sync --locked --extra dev
uv run python scripts/seed_local_classroom.py
uv run python -m uvicorn app.main:app --reload --port 8000
```

If you created or changed broker passwords while the container was running,
run `docker compose restart mqtt` from the repository root. Keep the local
plaintext listener on a trusted private network.

## Build, upload and connect the ESP32

1. From the repository root, enter `firmware\esp32`. Copy
   `include\secrets.example.h` to `include\secrets.h` if it does not exist.
2. Set the board's 2.4 GHz Wi-Fi settings, the ESP32 broker username/password
   and `MQTT_HOST` to the broker computer's private LAN IPv4 address from
   `ipconfig`. Use port `1883` for the stock local Compose listener. Keep the
   site, classroom, producer, sensor and device IDs aligned with the local
   database and broker ACL. The browser needs only the backend address.
3. Replace `COM11` below with the board's serial port. Close other serial
   monitors before uploading.

   ```powershell
   uv tool run --from platformio pio run -e esp32dev
   uv tool run --from platformio pio run -e esp32dev -t upload --upload-port COM11
   uv tool run --from platformio pio device monitor --port COM11 --baud 115200
   ```

   To test just the sensors, substitute `esp32dev_sensor_test` for
   `esp32dev` in the upload command. Return to `esp32dev` for MQTT operation.

## Start the dashboard

From the repository root, set the following values in `frontend\.env.local`:

```dotenv
NEXT_PUBLIC_API_BASE_URL=http://localhost:8000
NEXT_PUBLIC_CLASSROOM_ID=26e97d46-7a34-4439-922a-83684bda557d
NEXT_PUBLIC_ENABLE_HARDWARE_CONTROLS=true
```

Use `false` for the last setting when running read-only monitoring.

```powershell
Set-Location frontend
pnpm install --frozen-lockfile
pnpm dev
```

Open [the local dashboard](http://localhost:3000). Check Overview for
environment and connectivity, Devices for commanded relay states, and
Analytics for measured DC power and accumulated Wh/kWh history. The browser
receives backend snapshots and live updates with a polling fallback.

## Verify telemetry and accumulated energy

```powershell
Invoke-RestMethod http://localhost:8000/health/ready
$classroom = '26e97d46-7a34-4439-922a-83684bda557d'
$snapshot = Invoke-RestMethod "http://localhost:8000/api/v1/classrooms/$classroom/prototype-monitor"
$snapshot.classroom_telemetry | ConvertTo-Json -Depth 5
$snapshot.energy_history
```

The ESP32 owns the cumulative Wh counter. It trapezoidally integrates
consecutive valid consumption samples using unsigned `millis()` differences,
skips invalid intervals and gaps over six seconds, and derives kWh as
Wh divided by 1,000. It restores the counter from Preferences
(`aceo-energy` / `totalWh`) and checkpoints changed values every 60 seconds.
Abrupt power loss can discard consumption since the last successful
checkpoint. Set the Auto thresholds in `include/classroom_config.h` and
rebuild the firmware after changing them. Setting `kLocalAutomation` to
`false` selects the backend's existing camera-based Auto policy, which
can use fresh BH1750 lux for lighting.

- Confirm serial shows Wi-Fi and MQTT connected, and the dashboard update time
  advances with new snapshots. Check DHT22, BH1750 and PIR changes independently.
  Give the PIR 30 seconds to stabilize. Recheck with each sensor unavailable
  in turn. Other measurements should continue. A cached lux reading carries
  an age and freshness flag.
- On independent low-voltage test loads, verify each GPIO/channel and polarity.
  In Auto, all three turn off when motion clears. With motion, the fan is on,
  the light is on below 300 lux, and AC is on above 26 °C or above 65% RH.
  Verify Manual and Urgency through the approved dashboard controls separately.
  The dashboard reports relay commands. Confirm each physical load separately.
- Use a stable DC load and compare the change in Wh with average watts times
  elapsed hours. For example, 12 W for five minutes gives about 1 Wh or
  0.001 kWh. Check actual voltage and current using a suitable DC reference
  meter. A gap in INA219 readings should hold the total. Disconnect MQTT
  while leaving the board powered and verify the latest total catches up on
  reconnect. Verify the total survives a normal reboot after an NVS checkpoint.
- Disconnect power or Wi-Fi from the board and observe the stale/offline
  dashboard transition. Reconnect and verify a fresh update. A broker
  outage stops delivery while the board continues sampling and integrating.

## Troubleshooting

- For a bootloader error, close the serial monitor. Hold BOOT, tap EN/RESET,
  release BOOT, and retry. If reset fails only with the light relay connected,
  check the GPIO 12 strap and relay input on startup.
- If telemetry stalls, check `/health/ready`, serial logs, the broker host
  address, credentials and `docker compose logs --tail 60 mqtt`. An MQTT
  `session taken over` message usually indicates a second client with the same
  client ID. Stop the duplicate. Firmware waits for QoS 1 acknowledgements
  before enqueueing a new routine batch and resumes with a fresh sample.
- If telemetry works but a control times out, verify the normal
  `esp32dev` build, the same relay UUID in the device, ACL and command, and a
  synchronized device clock. Select Manual before direct power control. The
  firmware allows 191-byte incoming topics for the existing UUID commands.
