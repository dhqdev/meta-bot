import { CandlestickSeries, ColorType, createChart, LineSeries, type IChartApi, type UTCTimestamp } from "lightweight-charts";
import { useEffect, useRef } from "react";

const base = {
  layout: { background: { type: ColorType.Solid, color: "transparent" }, textColor: "#8a97b1", fontFamily: "Inter" },
  grid: { vertLines: { color: "#1f2940" }, horzLines: { color: "#1f2940" } },
  rightPriceScale: { borderColor: "#26324a" },
  timeScale: { borderColor: "#26324a", timeVisible: true },
  crosshair: { mode: 0 },
  localization: { locale: "pt-BR" },
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
  useEffect(() => {
    if (!ref.current) return;
    const c = createChart(ref.current, { ...base, height, autoSize: true });
    const up = points.length > 1 ? points[points.length - 1].v >= points[0].v : true;
    const series = c.addSeries(LineSeries, { color: color ?? (up ? "#2ecc71" : "#e74c3c"), lineWidth: 2, priceLineVisible: false });
    const seen = new Set<number>();
    const data = points
      .filter((p) => (seen.has(p.t) ? false : (seen.add(p.t), true)))
      .sort((a, b) => a.t - b.t)
      .map((p) => ({ time: p.t as UTCTimestamp, value: p.v }));
    series.setData(data);
    c.timeScale().fitContent();
    return () => c.remove();
  }, [points, height, color]);
  return <div ref={ref} style={{ height }} className="w-full" />;
}
