"""Testing page: upload a strategy -> scan it -> backtest it -> user decides.

A strategy submitted here is the same portable contract Manual's uploaded
strategies use (see ``domain.manual10.sandbox``): a plain top-level Python
function ``decide(price_history)`` returning ``{symbol: weight}``, executed
in the same AST-checked, subprocess-isolated sandbox. This module owns the
queue around that contract -- persisted to SQLite so it survives the user
navigating away and back, processed one item at a time by a background
worker thread (``worker.py``) so the page never blocks on a scan/backtest.
"""
