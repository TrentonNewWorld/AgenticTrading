"""Render the Community Strategy Lab 3-year results into a single HTML report.

Reads the per-strategy JSONs written by community_strategy_lab_3y.py and emits
one self-contained HTML file (inline SVG equity curves, no JS libraries, no
external assets beyond Google Fonts) suitable for publishing as an Artifact.
"""

from __future__ import annotations

import json
import html as html_mod
from pathlib import Path

OUT_DIR = Path(
    r"C:\Users\Trenton\AppData\Local\Temp\claude\C--Users-Trenton-Mission-Control-Alpaca-Trading"
    r"\77c8dc25-06fd-40a9-92e9-cfb030c35da5\scratchpad\strategy_lab_3y"
)
RESULTS_DIR = OUT_DIR / "results"
MULTI_DIR = OUT_DIR.parent / "multi_gauntlet" / "results"
REPORT_PATH = OUT_DIR / "report.html"

INITIAL = 1000.0

PRETTY_NAMES = {
    "buy_hold": "Buy & Hold (whole-share)",
    "equal_weight_djia": "Equal-Weight DJIA",
    "equal_weight_buyhold": "Equal-Weight Buy & Hold",
    "spy_index": "S&P 500 Index (SPY)",
    "djia_index": "Dow Jones Index (DIA)",
    "mean_variance_djia": "Mean-Variance Optimizer",
    "tradingagents_composite": "TradingAgents Composite",
    "capm_alpha_ranking": "CAPM Alpha Ranking",
    "momentum_effect": "12-Month Momentum Effect",
    "volatility_effect": "Low-Volatility Effect",
    "short_term_reversal": "Short-Term Reversal",
    "overnight_anomaly": "Overnight Anomaly",
    "turn_of_month": "Turn-of-Month Effect",
    "bandtastic": "Bandtastic (Bollinger)",
    "supertrend_triple": "Supertrend Triple-Confirm",
    "hlhb": "HLHB Trend-Catcher",
    "trendrider": "TrendRider",
    "pattern_recognition": "Candlestick Pattern Recognition",
    "universal_macd": "Universal MACD",
    "almgren_chriss_twap": "Almgren-Chriss TWAP",
    "balanced_starter": "Balanced Starter",
    "momentum_scout": "Momentum Scout",
    "three_step_analyst": "Three-Step Analyst",
    "ai_hedge_fund": "AI Hedge Fund (deterministic)",
    "blue_chip_steady": "Blue-Chip Steady",
    "even_split_dow": "Even-Split Dow",
    "contrarian_dip_buyer": "Contrarian Dip Buyer",
    "sector_rotator": "Sector Rotator",
    "volatility_guard": "Volatility Guard",
}


def esc(s: str) -> str:
    return html_mod.escape(str(s), quote=True)


def spark_svg(curve, benchmark, width=560, height=150) -> str:
    """Inline SVG equity line with a faint SPY reference underlay."""
    if not curve:
        return "<div class='no-curve'>no curve — stayed in cash</div>"
    pad = 6
    xs = list(range(len(curve)))
    ys = [p["equity"] for p in curve]
    all_vals = ys + ([p["equity"] for p in benchmark] if benchmark else [])
    lo, hi = min(all_vals), max(all_vals)
    if hi - lo < 1e-9:
        hi = lo + 1.0

    def pt(i, v, n):
        x = pad + (width - 2 * pad) * (i / max(n - 1, 1))
        y = height - pad - (height - 2 * pad) * ((v - lo) / (hi - lo))
        return f"{x:.1f},{y:.1f}"

    path = "M" + " L".join(pt(i, v, len(ys)) for i, v in enumerate(ys))
    bench_path = ""
    if benchmark:
        bys = [p["equity"] for p in benchmark]
        bench_path = "M" + " L".join(pt(i, v, len(bys)) for i, v in enumerate(bys))

    # $1,000 starting-capital gridline
    y0 = height - pad - (height - 2 * pad) * ((INITIAL - lo) / (hi - lo))
    base_line = ""
    if pad <= y0 <= height - pad:
        base_line = (
            f"<line x1='{pad}' y1='{y0:.1f}' x2='{width-pad}' y2='{y0:.1f}' "
            f"class='baseline' stroke-dasharray='3 4'/>"
        )

    final = ys[-1]
    end_y = float(pt(len(ys) - 1, final, len(ys)).split(",")[1])
    label_y = min(max(end_y, 14), height - 8)
    up = final >= INITIAL
    return f"""
<svg viewBox="0 0 {width} {height}" class="spark" role="img" aria-label="Equity curve ending at ${final:,.0f}">
  {base_line}
  {f'<path d="{bench_path}" class="bench"/>' if bench_path else ''}
  <path d="{path}" class="line {'up' if up else 'down'}"/>
  <circle cx="{pt(len(ys)-1, final, len(ys)).split(',')[0]}" cy="{end_y:.1f}" r="3.5" class="dot {'up' if up else 'down'}"/>
  <text x="{width-10}" y="{label_y:.1f}" text-anchor="end" class="endlab">${final:,.0f}</text>
</svg>"""


def main() -> None:
    results = []
    errors = []
    for p in sorted(RESULTS_DIR.glob("*.json")):
        results.append(json.loads(p.read_text(encoding="utf-8")))
    for p in sorted(RESULTS_DIR.glob("*.error")):
        errors.append((p.stem, p.read_text(encoding="utf-8")))

    for r in results:
        r["display_name"] = PRETTY_NAMES.get(r["key"], r["name"])

    results.sort(key=lambda r: r["metrics"]["return_pct"], reverse=True)
    window = results[0]["window"] if results else {"start": "?", "end": "?"}

    # SPY buy-and-hold benchmark, computed directly from the cached bars.
    # (The registry's own SPY strategy buys whole shares and cashed out at
    # $1,000, so it can't serve as the reference curve.)
    import pickle
    spy_curve: list = []
    spy_ret = None
    cache_file = OUT_DIR / "bars_cache.pkl"
    if cache_file.exists():
        bars = pickle.loads(cache_file.read_bytes())["bars"]
        if "SPY" in bars:
            closes = bars["SPY"]["close"]
            start_ts = closes.index[closes.index.strftime("%Y-%m-%d") >= window["start"]]
            series = closes.loc[start_ts]
            series = series[series.index.strftime("%Y-%m-%d") <= window["end"]]
            if len(series) > 1:
                eq = (series / series.iloc[0] * INITIAL).round(2)
                pts = [{"t": ts.isoformat(), "equity": float(v)} for ts, v in eq.items()]
                step = max(1, len(pts) // 260)
                spy_curve = pts[::step]
                if spy_curve[-1] is not pts[-1]:
                    spy_curve.append(pts[-1])
                spy_ret = round((float(eq.iloc[-1]) / INITIAL - 1) * 100, 2)

    cashouts = [r for r in results if abs(r["metrics"]["final"] - INITIAL) < 0.01]
    active = [r for r in results if r not in cashouts]
    beat_spy = [r for r in active if spy_ret is not None and r["metrics"]["return_pct"] > spy_ret] if spy_ret is not None else []
    positive = [r for r in active if r["metrics"]["return_pct"] > 0]

    best = active[0] if active else None
    worst = active[-1] if active else None

    # ---------- table rows ----------
    rows = []
    for i, r in enumerate(results, 1):
        m = r["metrics"]
        ret = m["return_pct"]
        cls = "pos" if ret > 0 else ("neg" if ret < 0 else "flat")
        badge = "TV classic" if r["source"] == "tradingview-classic" else "ported"
        spy_mark = " <span class='beats'>&#9650; beats SPY</span>" if (spy_ret is not None and ret > spy_ret and r["key"] != "spy_index") else ""
        note = " <span class='cash-note'>stayed in cash</span>" if r in cashouts else ""
        rows.append(f"""
<tr>
  <td class="rank">{i}</td>
  <td class="name"><a href="#card-{esc(r['key'])}">{esc(r['display_name'])}</a>
      <span class="badge {'tv' if r['source']=='tradingview-classic' else 'reg'}">{badge}</span>{spy_mark}{note}</td>
  <td class="num {cls}">{ret:+.1f}%</td>
  <td class="num">{m.get('cagr_pct', 0):+.1f}%</td>
  <td class="num dd">{m['max_drawdown_pct']:.1f}%</td>
  <td class="num">{m.get('sharpe', 0):.2f}</td>
  <td class="num muted">{m.get('n_trades', 0):,}</td>
</tr>""")

    # ---------- cards ----------
    cards = []
    for r in results:
        m = r["metrics"]
        ret = m["return_pct"]
        cls = "pos" if ret > 0 else ("neg" if ret < 0 else "flat")
        bench = spy_curve if r["key"] != "spy_index" else []
        cards.append(f"""
<article class="card" id="card-{esc(r['key'])}">
  <header>
    <h3>{esc(r['display_name'])}</h3>
    <span class="badge {'tv' if r['source']=='tradingview-classic' else 'reg'}">{'TradingView classic' if r['source']=='tradingview-classic' else 'community port'}</span>
  </header>
  <p class="desc">{esc(r['description'])}</p>
  {spark_svg(r['curve'], bench)}
  <div class="chips">
    <span class="chip {cls}">{ret:+.1f}% total</span>
    <span class="chip">CAGR {m.get('cagr_pct', 0):+.1f}%</span>
    <span class="chip">max DD {m['max_drawdown_pct']:.1f}%</span>
    <span class="chip">Sharpe {m.get('sharpe', 0):.2f}</span>
    <span class="chip muted">{m.get('n_trades', 0):,} trades</span>
  </div>
</article>""")

    error_items = "".join(
        f"<li><code>{esc(k)}</code> — {esc(v[:160])}</li>" for k, v in errors
    ) or "<li>none — every attempted strategy completed</li>"

    # ---------- other dashboards (multi-dashboard gauntlet) ----------
    DOMAIN_META = {
        "options": ("Options", "Alpaca historical option bars (real listed contracts, probed) · SPY underlying · ~2y window"),
        "futures": ("Futures", "Yahoo continuous-contract dailies (ES, MES, NQ, GC, CL, ZN) · ~2y window · 1-contract lots"),
        "forex": ("Forex", "Yahoo dailies, USD-quote majors (EUR, GBP, AUD, NZD) · ~2y window · 500-unit lots"),
        "crypto": ("Crypto", "Alpaca crypto dailies (BTC, ETH, SOL, LTC, LINK, DOGE) · ~2y window · $150 lots"),
    }
    OPTIONS_WARNING = """
      <p class="domain-warning"><strong>Read the options numbers as an engine stress-test, not tradable evidence.</strong>
      The options backtester models zero bid-ask spread, applies no margin requirement or cash cap to
      short-option opens (that's how a $1,000 account posts a −198% drawdown — equity went negative and
      came back), sizes fixed 1-contract lots that dwarf the wallet, and settles cash-only at expiry.
      The cash-secured put's +1,075% and the long call's −99.7% are both artifacts of those gaps at this
      account size. The covered call never traded at all: 100 shares of SPY costs far more than $1,000.</p>"""

    domain_sections = []
    for domain in ("options", "futures", "forex", "crypto"):
        ddir = MULTI_DIR / domain
        if not ddir.exists():
            continue
        dresults = [json.loads(p.read_text(encoding="utf-8")) for p in sorted(ddir.glob("*.json"))]
        if not dresults:
            continue
        dresults.sort(key=lambda r: r["metrics"]["return_pct"], reverse=True)
        title, meta = DOMAIN_META[domain]
        dwindow = dresults[0]["window"]
        dcards = []
        for r in dresults:
            m = r["metrics"]
            ret = m["return_pct"]
            cls = "pos" if ret > 0 else ("neg" if ret < 0 else "flat")
            dcards.append(f"""
<article class="card">
  <header><h3>{esc(r['name'])}</h3>
    <span class="badge reg">{esc(title)}</span></header>
  <p class="desc">{esc(r['description'])}</p>
  {spark_svg(r['curve'], [])}
  <div class="chips">
    <span class="chip {cls}">{ret:+.1f}% total</span>
    <span class="chip">CAGR {m.get('cagr_pct', 0):+.1f}%</span>
    <span class="chip">max DD {m['max_drawdown_pct']:.1f}%</span>
    <span class="chip">Sharpe {m.get('sharpe', 0):.2f}</span>
  </div>
</article>""")
        warning = OPTIONS_WARNING if domain == "options" else ""
        domain_sections.append(f"""
  <h2>{esc(title)} dashboard</h2>
  <p class="meta">{esc(dwindow['start'])} → {esc(dwindow['end'])} · {esc(meta)}</p>
  {warning}
  <div class="grid">{''.join(dcards)}</div>""")

    prediction_section = """
  <h2>Prediction dashboard</h2>
  <div class="notes">
    <p><strong>Not backtestable — by the platform's own design, and for a good reason.</strong>
    Prediction-market prices (Kalshi/Polymarket) gap discretely on news; a historical replay would
    systematically overstate what a strategy could really have captured, and no historical odds feed is
    wired anyway — both market-data adapters expose live snapshots only. Instead, every Prediction
    strategy is paper-traded <em>forward</em>: one real calendar day at a time against live markets,
    for exactly 5 days with real per-platform fees, before its result stands. A gauntlet here means
    activating strategies and waiting five real days — there is no shortcut through history.</p>
  </div>"""
    other_dashboards_html = "".join(domain_sections) + prediction_section

    cashout_items = "".join(
        f"<li>{esc(r['display_name'])}</li>" for r in cashouts
    ) or "<li>none</li>"

    html = f"""<title>Three-Year Strategy Gauntlet</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Bricolage+Grotesque:opsz,wght@12..96,400;12..96,600;12..96,800&family=Public+Sans:wght@400;500;600&family=IBM+Plex+Mono:wght@400;500&display=swap">
<style>
:root {{
  --bg: #f7f8fa; --surface: #ffffff; --ink: #131722; --ink-2: #4a5160; --ink-3: #8a91a0;
  --line-c: #2a78d6; --bench-c: #9aa2b1; --up: #0d7a3f; --down: #c03434;
  --border: #e3e6ec; --chip-bg: #eef1f6; --accent: #2a78d6;
  --pos-bg: #e7f3ec; --neg-bg: #f9e9e9;
}}
@media (prefers-color-scheme: dark) {{
  :root:not([data-theme="light"]) {{
    --bg: #14161c; --surface: #1c1f27; --ink: #f0f2f6; --ink-2: #b9bfcc; --ink-3: #7d8494;
    --line-c: #3987e5; --bench-c: #5b6272; --up: #3fb872; --down: #e66767;
    --border: #2b2f3a; --chip-bg: #262a34; --accent: #3987e5;
    --pos-bg: #1c2f25; --neg-bg: #33211f;
  }}
}}
:root[data-theme="dark"] {{
  --bg: #14161c; --surface: #1c1f27; --ink: #f0f2f6; --ink-2: #b9bfcc; --ink-3: #7d8494;
  --line-c: #3987e5; --bench-c: #5b6272; --up: #3fb872; --down: #e66767;
  --border: #2b2f3a; --chip-bg: #262a34; --accent: #3987e5;
  --pos-bg: #1c2f25; --neg-bg: #33211f;
}}
* {{ box-sizing: border-box; }}
body {{
  margin: 0; background: var(--bg); color: var(--ink);
  font-family: "Public Sans", system-ui, sans-serif; line-height: 1.55;
}}
.wrap {{ max-width: 1240px; margin: 0 auto; padding: 40px 28px 80px; }}
h1, h2, h3 {{ font-family: "Bricolage Grotesque", "Public Sans", sans-serif; text-wrap: balance; }}
h1 {{ font-size: clamp(2rem, 4.5vw, 3.2rem); font-weight: 800; margin: 0 0 6px; letter-spacing: -0.01em; }}
.sub {{ color: var(--ink-2); max-width: 68ch; margin: 0 0 8px; }}
.meta {{ font-family: "IBM Plex Mono", monospace; font-size: 0.8rem; color: var(--ink-3);
  text-transform: uppercase; letter-spacing: 0.08em; margin-bottom: 34px; }}
.stats {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(170px, 1fr)); gap: 14px; margin: 0 0 40px; }}
.stat {{ background: var(--surface); border: 1px solid var(--border); border-radius: 10px; padding: 16px 18px; }}
.stat .v {{ font-family: "Bricolage Grotesque", sans-serif; font-size: 1.7rem; font-weight: 800; }}
.stat .v.pos {{ color: var(--up); }} .stat .v.neg {{ color: var(--down); }}
.stat .k {{ font-size: 0.78rem; color: var(--ink-3); text-transform: uppercase; letter-spacing: 0.07em; margin-top: 2px; }}
.stat .who {{ font-size: 0.82rem; color: var(--ink-2); margin-top: 2px; }}
h2 {{ font-size: 1.45rem; font-weight: 700; margin: 46px 0 14px; }}
.tablewrap {{ overflow-x: auto; background: var(--surface); border: 1px solid var(--border); border-radius: 12px; }}
table {{ border-collapse: collapse; width: 100%; min-width: 760px; }}
th {{ text-align: right; font-size: 0.72rem; text-transform: uppercase; letter-spacing: 0.08em;
  color: var(--ink-3); padding: 12px 14px; border-bottom: 1px solid var(--border); }}
th:nth-child(2) {{ text-align: left; }}
td {{ padding: 9px 14px; border-bottom: 1px solid var(--border); font-size: 0.92rem; }}
tr:last-child td {{ border-bottom: none; }}
td.rank {{ color: var(--ink-3); font-family: "IBM Plex Mono", monospace; width: 2.5rem; text-align: right; }}
td.name {{ min-width: 260px; }}
td.name a {{ color: var(--ink); text-decoration: none; font-weight: 600; }}
td.name a:hover {{ color: var(--accent); text-decoration: underline; }}
td.num {{ text-align: right; font-family: "IBM Plex Mono", monospace; font-variant-numeric: tabular-nums; }}
td.num.pos {{ color: var(--up); font-weight: 600; }}
td.num.neg {{ color: var(--down); font-weight: 600; }}
td.num.dd {{ color: var(--ink-2); }}
td.num.muted, .muted {{ color: var(--ink-3); }}
.badge {{ font-size: 0.66rem; text-transform: uppercase; letter-spacing: 0.06em; padding: 2px 7px;
  border-radius: 99px; margin-left: 7px; vertical-align: 1px; font-weight: 600; }}
.badge.tv {{ background: var(--chip-bg); color: var(--accent); border: 1px solid var(--accent); }}
.badge.reg {{ background: var(--chip-bg); color: var(--ink-2); border: 1px solid var(--border); }}
.beats {{ color: var(--up); font-size: 0.74rem; font-weight: 600; margin-left: 6px; }}
.cash-note {{ color: var(--ink-3); font-size: 0.74rem; font-style: italic; margin-left: 6px; }}
.grid {{ display: grid; grid-template-columns: repeat(auto-fill, minmax(340px, 1fr)); gap: 18px; }}
.card {{ background: var(--surface); border: 1px solid var(--border); border-radius: 12px; padding: 18px 18px 16px; }}
.card header {{ display: flex; align-items: baseline; justify-content: space-between; gap: 8px; flex-wrap: wrap; }}
.card h3 {{ margin: 0; font-size: 1.05rem; font-weight: 700; }}
.card .desc {{ color: var(--ink-2); font-size: 0.84rem; margin: 6px 0 10px; min-height: 2.4em; }}
.spark {{ width: 100%; height: auto; display: block; }}
.spark .line {{ fill: none; stroke: var(--line-c); stroke-width: 2; }}
.spark .line.down {{ stroke: var(--down); }}
.spark .bench {{ fill: none; stroke: var(--bench-c); stroke-width: 1.25; opacity: 0.6; }}
.spark .baseline {{ stroke: var(--ink-3); stroke-width: 1; opacity: 0.5; }}
.spark .dot {{ fill: var(--line-c); }} .spark .dot.down {{ fill: var(--down); }}
.spark .endlab {{ font-family: "IBM Plex Mono", monospace; font-size: 11px; fill: var(--ink-2); }}
.no-curve {{ color: var(--ink-3); font-size: 0.85rem; padding: 40px 0; text-align: center; }}
.chips {{ display: flex; flex-wrap: wrap; gap: 6px; margin-top: 10px; }}
.chip {{ font-family: "IBM Plex Mono", monospace; font-size: 0.72rem; background: var(--chip-bg);
  border-radius: 6px; padding: 3px 8px; color: var(--ink-2); }}
.chip.pos {{ background: var(--pos-bg); color: var(--up); font-weight: 600; }}
.chip.neg {{ background: var(--neg-bg); color: var(--down); font-weight: 600; }}
.notes {{ background: var(--surface); border: 1px solid var(--border); border-radius: 12px;
  padding: 20px 24px; font-size: 0.9rem; color: var(--ink-2); }}
.notes h2 {{ margin-top: 0; font-size: 1.15rem; }}
.domain-warning {{ background: var(--neg-bg); border: 1px solid var(--down); border-radius: 10px;
  padding: 14px 18px; font-size: 0.88rem; color: var(--ink-2); max-width: 78ch; }}
.notes p {{ margin: 0; max-width: 78ch; }}
.notes ul {{ margin: 8px 0; padding-left: 20px; }}
.notes li {{ margin: 4px 0; }}
.legend {{ display: flex; gap: 18px; font-size: 0.78rem; color: var(--ink-3); margin: 8px 2px 16px; }}
.legend span::before {{ content: ""; display: inline-block; width: 16px; height: 3px; border-radius: 2px;
  margin-right: 6px; vertical-align: 3px; }}
.legend .l1::before {{ background: var(--line-c); }}
.legend .l2::before {{ background: var(--bench-c); }}
.legend .l3::before {{ background: var(--ink-3); opacity: 0.5; }}
</style>
<div class="wrap">
  <h1>Three-Year Strategy Gauntlet</h1>
  <p class="sub">Every community-reputed trading strategy this platform can run deterministically,
  across every dashboard. Stocks: the freqtrade / QuantConnect / TradingAgents ports plus thirteen
  TradingView classics, three years of data through one engine. Then Options, Futures, Forex and
  Crypto — each dashboard's full deterministic roster over its maximum available window (~2 years,
  Yahoo's ceiling for futures/forex) — and an honest account of why Prediction cannot be backtested
  at all. No survivors were pre-selected.</p>
  <p class="meta">{esc(window['start'])} → {esc(window['end'])} · 756 trading days · $1,000 start · $10 lots ·
  Alpaca SIP daily bars · DJIA-30 / SPY universes</p>

  <div class="stats">
    <div class="stat"><div class="v">{len(results)}</div><div class="k">strategies tested</div></div>
    <div class="stat"><div class="v pos">{len(positive)}</div><div class="k">finished positive</div></div>
    <div class="stat"><div class="v">{len(beat_spy)}</div><div class="k">beat SPY buy-and-hold{f" ({spy_ret:+.1f}%)" if spy_ret is not None else ""}</div></div>
    <div class="stat"><div class="v pos">{best['metrics']['return_pct']:+.1f}%</div><div class="k">best</div>
      <div class="who">{esc(best['display_name']) if best else ''}</div></div>
    <div class="stat"><div class="v {'neg' if worst and worst['metrics']['return_pct'] < 0 else ''}">{worst['metrics']['return_pct']:+.1f}%</div><div class="k">worst</div>
      <div class="who">{esc(worst['display_name']) if worst else ''}</div></div>
  </div>

  <h2>Standings</h2>
  <div class="tablewrap"><table>
    <thead><tr><th>#</th><th>Strategy</th><th>Return</th><th>CAGR</th><th>Max DD</th><th>Sharpe</th><th>Trades</th></tr></thead>
    <tbody>{''.join(rows)}</tbody>
  </table></div>

  <h2>Every stocks strategy, card by card</h2>
  <div class="legend"><span class="l1">strategy equity</span><span class="l2">SPY buy &amp; hold</span><span class="l3">$1,000 start</span></div>
  <div class="grid">{''.join(cards)}</div>

  {other_dashboards_html}

  <div class="notes" style="margin-top:44px">
    <h2>How to read this honestly</h2>
    <ul>
      <li><strong>"Every strategy the community reports is good" means archetypes, not all 200k TradingView scripts.</strong>
        The registry ports came from freqtrade-strategies, QuantConnect and TradingAgents community sources; the
        thirteen "TV classic" entries are the most republished strategy archetypes on TradingView, implemented
        faithfully from their canonical descriptions.</li>
      <li><strong>No transaction costs, slippage, or taxes are modeled.</strong> High-trade-count strategies
        (see the Trades column) would lose meaningfully more in live trading. Rankings between a 40-trade and a
        4,000-trade strategy should be read with that asymmetry in mind.</li>
      <li><strong>Same engine, same data, same window for all.</strong> Signals computed only on history strictly
        before each day (no look-ahead); daily rebalance to target weights; $10 lot quantization.</li>
      <li><strong>One 3-year window is one sample.</strong> {esc(window['start'])}–{esc(window['end'])} contained
        a strong bull run — trend strategies flatter here; a different window reorders this table.</li>
      <li><strong>Stayed entirely in cash:</strong><ul>{cashout_items}</ul>
        These buy whole shares; $1,000 across 30 Dow names couldn't afford one share of most, a real property of
        the implementation at this capital, not a market verdict.</li>
      <li><strong>Excluded by design:</strong> the LLM-driven agent (spends API money per decision, not
        deterministic) and the sandboxed-upload runner (no strategy code to run).</li>
      <li><strong>Failed to run:</strong><ul>{error_items}</ul></li>
      <li>Past performance does not guarantee future results. This is research output, not investment advice.</li>
    </ul>
  </div>
</div>
"""
    REPORT_PATH.write_text(html, encoding="utf-8")
    print(f"report written: {REPORT_PATH}")
    print(f"strategies: {len(results)}, errors: {len(errors)}, cash-outs: {len(cashouts)}")


if __name__ == "__main__":
    main()
