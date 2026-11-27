import { useMemo } from "react";
import { TelemetryHistoryChart, type HistoryChartDefinition } from "@/src/components/TelemetryHistoryChart";
import { energyChartHistory, SOLAR_ENERGY_PRECISION } from "@/src/lib/prototype-energy";
import type { ClassroomMeterPoint, SolarTelemetry } from "@/src/types/prototype-monitor";

type EnergyChartPoint = ReturnType<typeof energyChartHistory>[number];

export function EnergyCharts({ classroom, solar, sampleIntervalMs }: {
  classroom: ClassroomMeterPoint[];
  solar: SolarTelemetry[];
  sampleIntervalMs: number | undefined;
}) {
  const history = useMemo(() => energyChartHistory(classroom, solar), [classroom, solar]);
  const definitions = useMemo<HistoryChartDefinition<EnergyChartPoint>[]>(() => {
    const classroomSeries = {
      label: "Classroom",
      color: "#1a6b5f",
      gapMs: Math.max(6_000, (sampleIntervalMs ?? 2_000) * 2.5),
    };
    const solarSeries = { label: "Solar", color: "#906019", dashed: true };

    return [
      {
        title: "Power over time",
        description: "Classroom demand and solar generation at each meter reading.",
        unit: "W",
        precision: 6,
        series: [
          { ...classroomSeries, get: (sample) => sample.meter === "classroom" ? sample.powerW : undefined },
          { ...solarSeries, get: (sample) => sample.meter === "solar" ? sample.powerW : undefined },
        ],
      },
      {
        title: "Energy over time",
        description: "Classroom consumption and solar energy collected. Each meter has its own starting point and reset history.",
        unit: "Wh",
        precision: SOLAR_ENERGY_PRECISION,
        breakAt: "energy-reset",
        series: [
          { ...classroomSeries, get: (sample) => sample.meter === "classroom" ? sample.energyWh : undefined },
          { ...solarSeries, get: (sample) => sample.meter === "solar" ? sample.energyWh : undefined },
        ],
      },
    ];
  }, [sampleIntervalMs]);

  return (
    <div className="energy-chart-grid">
      {definitions.map((definition) => (
        <TelemetryHistoryChart key={definition.title} definition={definition} history={history} />
      ))}
    </div>
  );
}

