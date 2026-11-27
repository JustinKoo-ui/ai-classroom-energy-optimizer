import { EnergyCharts } from "@/src/components/EnergyCharts";
import type { ClassroomTelemetryView } from "@/src/lib/classroom-telemetry";
import { SOLAR_ENERGY_PRECISION, solarCollectedWh, solarTelemetryIsFresh } from "@/src/lib/prototype-energy";
import type { ClassroomMeterPoint, PrototypeConnection, PrototypeMonitorSnapshot, SolarTelemetry } from "@/src/types/prototype-monitor";

type Measurement = number | null | undefined;

function measurement(value: Measurement, unit: string, precision: number) {
  return value == null || !Number.isFinite(value) ? "Unavailable" : `${value.toFixed(precision)} ${unit}`;
}

function sensorStatus(available: boolean | undefined) {
  return available === undefined ? "Unknown" : available ? "Available" : "Unavailable";
}

type MeterProps = {
  id: string;
  title: string;
  description: string;
  availability: PrototypeConnection["availability"];
  fresh: boolean;
  receivedAt: string | null | undefined;
  voltage: Measurement;
  current: Measurement;
  power: Measurement;
  totalWh: Measurement;
  energyPrecision: number;
  inaAvailable: boolean | undefined;
  detail: [string, string];
};

function EnergyMeter({
  id, title, description, availability, fresh, receivedAt, voltage, current, power, totalWh, energyPrecision,
  inaAvailable, detail,
}: MeterProps) {
  const status = availability === "offline" ? "ESP32 offline"
    : fresh ? inaAvailable ? "Live readings" : "Meter unavailable"
    : receivedAt ? "Stale readings" : "Waiting for telemetry";
  const tone = availability === "offline" || (fresh && !inaAvailable) ? "error"
    : fresh ? "ok" : receivedAt ? "stale" : "unknown";
  const facts = [
    ["Voltage", measurement(voltage, "V", 3)],
    ["Current", measurement(current, "A", 4)],
    ["Instantaneous power", measurement(power, "W", 3)],
    ["INA219", sensorStatus(inaAvailable)],
    detail,
  ];

  return (
    <article className="solar-panel energy-meter" aria-labelledby={id}>
      <h3 id={id}>{title}</h3>
      <p className="energy-meter__scope">{description}</p>
      <div className="classroom-monitor__sample energy-meter__status" role="status">
        <span className={`status-dot status-dot--${tone}`}>{status}</span>
        {receivedAt && <small>Last received <time dateTime={receivedAt}>{new Date(receivedAt).toLocaleString()}</time></small>}
      </div>
      <dl className="energy-meter__total">
        <dt>Accumulated total</dt>
        <dd>
          <strong>{measurement(totalWh, "Wh", energyPrecision)}</strong>
          {totalWh != null && Number.isFinite(totalWh) && (
            <span>{measurement(totalWh / 1_000, "kWh", energyPrecision + 3)}</span>
          )}
        </dd>
      </dl>
      <dl className="solar-facts">
        {facts.map(([label, value]) => <div key={label}><dt>{label}</dt><dd>{value}</dd></div>)}
      </dl>
    </article>
  );
}

export function EnergyAnalytics({ classroom, classroomMeterHistory, snapshot, history, error }: {
  classroom: ClassroomTelemetryView;
  classroomMeterHistory: ClassroomMeterPoint[];
  snapshot: PrototypeMonitorSnapshot | null;
  history: SolarTelemetry[];
  error: string | null;
}) {
  const classroomPayload = classroom.reading?.payload;
  const solar = snapshot?.solar;
  const solarReading = solar?.telemetry;
  const solarPower = solarReading?.payload.solar_power;
  const solarAvailable = solarReading?.payload.sensors.ina219_available;
  const solarAvailability = !error && snapshot?.broker_connected ? solar?.availability ?? "unknown" : "unknown";
  const solarFresh = solarTelemetryIsFresh(snapshot, error);
  const classroomLive = classroom.fresh && classroomPayload?.sensors.ina219_available;
  const solarLive = solarFresh && solarAvailable;
  const hasHistory = classroomMeterHistory.length > 0 || history.length > 0;
  const chartStatus = classroomLive && solarLive ? "Both meters live"
    : classroomLive ? "Classroom meter live"
    : solarLive ? "Solar meter live"
    : hasHistory ? "Recorded history" : "Waiting for meter data";
  const chartTone = classroomLive || solarLive ? "ok" : hasHistory ? "stale" : "unknown";

  return (
    <section id="analytics" className="analytics-section" aria-labelledby="analytics-title">
      <div className="section-heading">
        <h2 id="analytics-title">Energy analytics</h2>
        <span className={`status-dot status-dot--${chartTone}`} role="status">{chartStatus}</span>
      </div>
      {error && <p className="inline-alert" role="status">{error}</p>}
      <EnergyCharts classroom={classroomMeterHistory} solar={history} sampleIntervalMs={classroomPayload?.sample_interval_ms} />
      <div className="energy-meter-grid">
       <EnergyMeter
          id="classroom-energy-title"
          title="Classroom energy consumption"
          description="Measured by the INA219 on the classroom DC circuit."
          availability={classroom.availability}
          fresh={classroom.fresh}
          receivedAt={classroom.reading?.received_at}
          voltage={classroomPayload?.voltage_v}
          current={classroomPayload?.current_a}
          power={classroomPayload?.active_power_w}
          totalWh={classroomPayload?.sensors.ina219_available ? classroomPayload.energy_wh_total : null}
          energyPrecision={3}
          inaAvailable={classroomPayload?.sensors.ina219_available}
          detail={["ESP32 energy storage", sensorStatus(classroomPayload?.energy_storage_available)]}
        />
        <EnergyMeter
          id="solar-energy-title"
          title="Solar energy collected"
          description="Measured by the tracker’s INA219 at the solar panel."
          availability={solarAvailability}
          fresh={solarFresh}
          receivedAt={solarReading?.received_at}
          voltage={solarAvailable ? solarPower?.voltage : null}
          current={solarAvailable ? solarPower?.current : null}
          power={solarAvailable ? solarPower?.power : null}
          totalWh={solarCollectedWh(solarReading)}
          energyPrecision={SOLAR_ENERGY_PRECISION}
          inaAvailable={solarAvailable}
          detail={["Counter starts over", "On tracker restart or reset"]}
        />
      </div>
    </section>
  );
}
