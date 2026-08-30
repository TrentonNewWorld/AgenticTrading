"""Manual 10: an opening-range momentum bot.

Every trading day: scan the first N minutes of market open for stocks priced
$1-$99 with the strongest gains, buy the top 10 in the Alpaca *paper* account,
promote any that are still up after a short window to the Alpaca *live*
(real-money) account, hold until shortly before close, then sell everything.

See ``docs/`` cross-references in each submodule; the whole feature is opt-in
and off by default -- see ``scheduler.py`` and ``engine.py`` for the specific
environment-variable gates before assuming anything here can spend real money
on its own.
"""
