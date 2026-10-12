"use client";

import { useEffect, useRef } from "react";
import { CandlestickSeries, createChart, LineStyle, type IChartApi, type IPriceLine, type ISeriesApi, type UTCTimestamp } from "lightweight-charts";

export type Candle = { time: number; open: number; high: number; low: number; close: number };
export type Level = { price: number; title: string; color: string };

/** Candlestick chart (times are UTC seconds; axis shows UTC). Levels are drawn as dashed price lines. */
export function CandleChart({ candles, levels = [] }: { candles: Candle[]; levels?: Level[] }) {
  const box = useRef<HTMLDivElement>(null);
  const chart = useRef<IChartApi | null>(null);
  const series = useRef<ISeriesApi<"Candlestick"> | null>(null);
  const lines = useRef<IPriceLine[]>([]);

  useEffect(() => {
    if (!box.current) return;
    const css = getComputedStyle(document.documentElement);
    const c = createChart(box.current, {
      autoSize: true,
      layout: { background: { color: css.getPropertyValue("--panel").trim() || "#151b24" }, textColor: css.getPropertyValue("--muted").trim() || "#8f9bb0" },
      grid: { vertLines: { color: "rgba(127,127,127,0.12)" }, horzLines: { color: "rgba(127,127,127,0.12)" } },
      timeScale: { timeVisible: true, secondsVisible: false },
    });
    chart.current = c;
    series.current = c.addSeries(CandlestickSeries, { upColor: "#34c38f", downColor: "#f46a6a", wickUpColor: "#34c38f", wickDownColor: "#f46a6a", borderVisible: false });
    return () => {
      c.remove();
      chart.current = null;
      series.current = null;
      lines.current = [];
    };
  }, []);

  useEffect(() => {
    const s = series.current;
    if (!s) return;
    s.setData(candles.map((k) => ({ ...k, time: k.time as UTCTimestamp })));
    lines.current.forEach((l) => s.removePriceLine(l));
    lines.current = levels.map((l) => s.createPriceLine({ price: l.price, color: l.color, lineWidth: 1, lineStyle: LineStyle.Dashed, title: l.title, axisLabelVisible: true }));
  }, [candles, levels]);

  return <div ref={box} className="h-80 w-full" dir="ltr" />;
}
