import { useEffect, useRef, useState } from "react";

const MAG7 = ["AAPL", "MSFT", "GOOGL", "AMZN", "NVDA", "TSLA", "META"] as const;
const SCROLL_PX_PER_SEC = 55;
const REFRESH_MS = 30_000;

type Quote = {
  symbol: string;
  price: number | null;
  changePercent: number | null;
};

function apiBase(): string {
  const host = window.location.hostname;
  if (host === "localhost" || host === "127.0.0.1") {
    return window.location.origin;
  }
  return "https://newworldtrading.onrender.com";
}

function sortQuotes(quotes: Quote[]): Quote[] {
  const order = new Map(MAG7.map((s, i) => [s, i]));
  return [...quotes].sort((a, b) => (order.get(a.symbol as (typeof MAG7)[number]) ?? 99) - (order.get(b.symbol as (typeof MAG7)[number]) ?? 99));
}

function sparkPath(changePercent: number | null): string {
  if (changePercent == null) return "M0,8 L5,6 L10,7 L15,4 L20,5 L25,3 L30,5";
  return changePercent >= 0
    ? "M0,10 L5,8 L10,9 L15,6 L20,7 L25,4 L30,3"
    : "M0,3 L5,5 L10,4 L15,7 L20,6 L25,9 L30,10";
}

function QuoteItem({ quote }: { quote: Quote }) {
  const change = quote.changePercent;
  const changeClass =
    change == null ? "" : change >= 0 ? "positive" : "negative";
  const changeDisplay =
    change == null
      ? "--"
      : `${change >= 0 ? "+" : ""}${change.toFixed(2)}%`;
  const price =
    quote.price != null
      ? quote.price.toLocaleString("en-US", {
          minimumFractionDigits: 2,
          maximumFractionDigits: 2,
        })
      : "--";

  return (
    <div className="landing-ticker-item" data-symbol={quote.symbol}>
      <span className="symbol">{quote.symbol}</span>
      <span className="price">{price}</span>
      <span className={`change ${changeClass}`} title="Change vs previous close">
        {changeDisplay}
      </span>
      <svg className={`landing-ticker-chart ${changeClass}`} viewBox="0 0 30 12" aria-hidden="true">
        <path d={sparkPath(change)} stroke="currentColor" fill="none" strokeWidth="1" />
      </svg>
    </div>
  );
}

export function MarketTicker() {
  const [quotes, setQuotes] = useState<Quote[]>([]);
  const [status, setStatus] = useState("Loading market data...");
  const marqueeRef = useRef<HTMLDivElement>(null);
  const trackRef = useRef<HTMLDivElement>(null);
  const offsetRef = useRef(0);
  const setWidthRef = useRef(0);
  const lastTimeRef = useRef(0);
  const rafRef = useRef<number | null>(null);
  const pausedRef = useRef(false);

  useEffect(() => {
    let cancelled = false;

    async function load() {
      const controller = new AbortController();
      const timeoutId = setTimeout(() => controller.abort(), 45_000);
      try {
        const symbols = MAG7.join(",");
        const res = await fetch(`${apiBase()}/ticker?symbols=${symbols}`, {
          signal: controller.signal,
        });
        const data = await res.json().catch(() => ({}));
        if (cancelled) return;
        if (data.quotes?.length) {
          setQuotes(sortQuotes(data.quotes));
          setStatus("");
        } else {
          setStatus(
            data.error ||
              (res.ok
                ? "Market data temporarily unavailable"
                : `Market data unavailable (HTTP ${res.status})`),
          );
        }
      } catch (err) {
        if (cancelled) return;
        const aborted = err instanceof DOMException && err.name === "AbortError";
        setStatus(
          aborted
            ? "Market data is taking longer than expected — retrying…"
            : "Could not load market data",
        );
      } finally {
        clearTimeout(timeoutId);
      }
    }

    load();
    const poll = setInterval(load, REFRESH_MS);
    return () => {
      cancelled = true;
      clearInterval(poll);
    };
  }, []);

  useEffect(() => {
    const track = trackRef.current;
    const marquee = marqueeRef.current;
    if (!track || !marquee || quotes.length === 0) return;

    offsetRef.current = 0;
    setWidthRef.current = 0;
    lastTimeRef.current = 0;
    track.style.transform = "translate3d(0,0,0)";

    const stop = () => {
      if (rafRef.current != null) {
        cancelAnimationFrame(rafRef.current);
        rafRef.current = null;
      }
    };

    const frame = (now: number) => {
      if (!setWidthRef.current) {
        const firstSet = track.querySelector<HTMLElement>(".landing-ticker-set");
        setWidthRef.current = firstSet?.offsetWidth || 0;
        if (!setWidthRef.current) {
          rafRef.current = requestAnimationFrame(frame);
          return;
        }
      }
      if (!lastTimeRef.current) lastTimeRef.current = now;
      if (!pausedRef.current) {
        const dt = Math.min(0.05, (now - lastTimeRef.current) / 1000);
        offsetRef.current -= SCROLL_PX_PER_SEC * dt;
        if (offsetRef.current <= -setWidthRef.current) {
          offsetRef.current += setWidthRef.current;
        }
        track.style.transform = `translate3d(${offsetRef.current}px, 0, 0)`;
      }
      lastTimeRef.current = now;
      rafRef.current = requestAnimationFrame(frame);
    };

    const onEnter = () => {
      pausedRef.current = true;
    };
    const onLeave = () => {
      pausedRef.current = false;
      lastTimeRef.current = 0;
    };
    marquee.addEventListener("mouseenter", onEnter);
    marquee.addEventListener("mouseleave", onLeave);
    rafRef.current = requestAnimationFrame(frame);

    return () => {
      stop();
      marquee.removeEventListener("mouseenter", onEnter);
      marquee.removeEventListener("mouseleave", onLeave);
    };
  }, [quotes]);

  const sorted = quotes.length
    ? quotes
    : MAG7.map((symbol) => ({ symbol, price: null, changePercent: null }));
  const ready = quotes.length > 0;
  // Tile enough copies so one set is wider than the viewport (seamless loop).
  const repeats = Math.max(2, Math.ceil(1400 / Math.max(1, sorted.length * 140)));
  const tiled = Array.from({ length: repeats }, () => sorted).flat();

  return (
    <div className="landing-ticker" aria-label="Live market ticker">
      <div className="landing-ticker-bar">
        <div className="landing-ticker-marquee" ref={marqueeRef}>
          <div className="landing-ticker-track" ref={trackRef} data-ready={ready ? "1" : "0"}>
            {ready ? (
              <>
                <div className="landing-ticker-set">
                  {tiled.map((q, i) => (
                    <QuoteItem key={`${q.symbol}-${i}`} quote={q} />
                  ))}
                </div>
                <div className="landing-ticker-set" aria-hidden="true">
                  {tiled.map((q, i) => (
                    <QuoteItem key={`dup-${q.symbol}-${i}`} quote={q} />
                  ))}
                </div>
              </>
            ) : (
              <div className="landing-ticker-placeholder">{status}</div>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
