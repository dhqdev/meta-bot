import { CandlestickSeries, ColorType, createChart, LineSeries, TickMarkType, type IChartApi, type ISeriesApi, type Time, type UTCTimestamp } from "lightweight-charts";
import { useEffect, useRef } from "react";

// O gráfico trabalha em UTC; aqui os horários são mostrados em Brasília, como no resto do app.
const TZ = "America/Sao_Paulo";
const fmt = (opts: Intl.DateTimeFormatOptions) => new Intl.DateTimeFormat("pt-BR", { timeZone: TZ, ...opts });
const F = {
  year: fmt({ year: "numeric" }),
  month: fmt({ month: "short" }),
  day: fmt({ day: "2-digit", month: "2-digit" }),
  time: fmt({ hour: "2-digit", minute: "2-digit" }),
  full: fmt({ day: "2-digit", month: "2-digit", year: "2-digit", hour: "2-digit", minute: "2-digit" }),
};
const toDate = (t: Time) => new Date((typeof t === "number" ? t : 0) * 1000);

const base = {
  layout: { background: { type: ColorType.Solid, color: "transparent" }, textColor: "#8a97b1", fontFamily: "Inter" },
  grid: { vertLines: { color: "#1f2940" }, horzLines: { color: "#1f2940" } },
  rightPriceScale: { borderColor: "#26324a" },
  timeScale: {
    borderColor: "#26324a",
    timeVisible: true,
    tickMarkFormatter: (t: Time, type: TickMarkType) => {
      const d = toDate(t);
      if (type === TickMarkType.Year) return F.year.format(d);
      if (type === TickMarkType.Month) return F.month.format(d).replace(".", "");
      if (type === TickMarkType.DayOfMonth) return F.day.format(d);
      return F.time.format(d);
    },
  },
  crosshair: { mode: 0 },
  localization: { locale: "pt-BR", timeFormatter: (t: Time) => F.full.format(toDate(t)) },
};

export function CandleChart({ candles, height = 320 }: { candles: Array<{ t: number; o: number; h: number; l: number; c: number }>; height?: number }) {
  const ref = useRef<HTMLDivElement>(null);
  const chart = useRef<IChartApi | null>(null);
  useEffect(() => {
    if (!ref.current) return;
    const c = createChart(ref.current, { ...base, height, autoSize: true });
    chart.current = c;
    const series = c.addSeries(CandlestickSeries, { upColor: "#2ecc71", downColor: "#e74c3c", wickUpColor: "#2ecc71", wickDownColor: "#e74c3c", borderVisible: false });
    series.setData(candles.map((k) => ({ time: k.t as UTCTimestamp, open: k.o, high: k.h, low: k.l, close: k.c })));
    c.timeScale().fitContent();
    return () => {
      c.remove();
      chart.current = null;
    };
  }, [candles, height]);
  return <div ref={ref} style={{ height }} className="w-full" />;
}

export function LineChart({ points, height = 220, color }: { points: Array<{ t: number; v: number }>; height?: number; color?: string }) {
  const ref = useRef<HTMLDivElement>(null);
  const chart = useRef<{ api: IChartApi; series: ISeriesApi<"Line">; key: string } | null>(null);
  // o gráfico é criado uma vez; novos pontos só atualizam a linha (o zoom do dono não se perde a cada atualização)
  useEffect(() => {
    if (!ref.current) return;
    const api = createChart(ref.current, { ...base, height, autoSize: true });
    const series = api.addSeries(LineSeries, { lineWidth: 2, priceLineVisible: false });
    chart.current = { api, series, key: "" };
    return () => {
      api.remove();
      chart.current = null;
    };
  }, [height]);
  useEffect(() => {
    const c = chart.current;
    if (!c) return;
    const seen = new Set<number>();
    const data = points
      .filter((p) => (seen.has(p.t) ? false : (seen.add(p.t), true)))
      .sort((a, b) => a.t - b.t)
      .map((p) => ({ time: p.t as UTCTimestamp, value: p.v }));
    const up = data.length > 1 ? data[data.length - 1].value >= data[0].value : true;
    c.series.applyOptions({ color: color ?? (up ? "#2ecc71" : "#e74c3c") });
    c.series.setData(data);
    // enquadra de novo só quando é outra série (outro começo); novos pontos no fim mantêm o zoom
    const key = data.length ? String(data[0].time) : "";
    if (key !== c.key) {
      c.api.timeScale().fitContent();
      c.key = key;
    }
  }, [points, color, height]);
  return <div ref={ref} style={{ height }} className="w-full" />;
}
