import math


def _price(x) -> float:
    """None -> NaN, which the C++ broker reads as 'no level set'."""
    return math.nan if x is None else float(x)


def _entry(limit, stop) -> tuple[int, float]:
    """Map (limit, stop) kwargs to the broker's (entry_type, trigger).

    entry_type: 0 = market, 1 = limit, 2 = stop. limit/stop are mutually
    exclusive; neither means a market order filled at the next tick.
    """
    if limit is not None and stop is not None:
        raise ValueError("pass at most one of limit= or stop=")
    if limit is not None:
        return 1, float(limit)
    if stop is not None:
        return 2, float(stop)
    return 0, math.nan


class Context:
    """The order API handed to callback strategies: ``on_candle(ctx, bars)``.

    Carries the bound C++ broker, so the helpers forward straight into it, plus
    ``hour`` (the current hour, updated by the engine before each call). Orders
    placed here fill on the next tick(s) of their symbol — decide at the close,
    fill next, never look-ahead. The same surface backs the legacy ``Strategy``.
    """

    def __init__(self, broker) -> None:
        self._broker = broker
        self.hour: int = 0

    # --- order API --------------------------------------------------------

    def buy(self, symbol: str, lots: float, sl=None, tp=None,
            limit=None, stop=None) -> None:
        """Open a long. Market (default) fills at the next tick's ask; pass
        ``limit=`` (fills when the ask falls to it) or ``stop=`` (when it rises
        to it) for a pending entry. ``sl``/``tp`` are absolute prices."""
        et, trig = _entry(limit, stop)
        self._broker.submit_buy(symbol, float(lots), _price(sl), _price(tp), et, trig)

    def sell(self, symbol: str, lots: float, sl=None, tp=None,
             limit=None, stop=None) -> None:
        """Open a short. Market fills at the next tick's bid; ``limit=`` fills
        when the bid rises to it, ``stop=`` when it falls to it."""
        et, trig = _entry(limit, stop)
        self._broker.submit_sell(symbol, float(lots), _price(sl), _price(tp), et, trig)

    def close(self, symbol: str) -> None:
        """Close every open position on `symbol` at the next tick (market)."""
        self._broker.submit_close(symbol)

    def position(self, symbol: str) -> list:
        """Open positions on `symbol` as a list of dicts (empty if flat)."""
        return self._broker.positions(symbol)

    @property
    def equity(self) -> float:
        """Account equity at the last processed hour's close (cash + unrealized)."""
        return self._broker.equity()

    @property
    def cash(self) -> float:
        """Realized cash (initial capital + closed-trade P&L, net of costs)."""
        return self._broker.cash()


class Strategy:
    """Legacy base class (still supported). Subclass it, override `on_candle`
    (and optionally `on_tick`), and pass the subclass to `Backtester.run`.

    Prefer the callback form for new code — ``bt.run(on_candle=fn)`` with
    ``fn(ctx, bars)`` — which needs no subclassing. Either way the order helpers
    below forward into the same C++ broker via a bound `Context`.
    """

    # --- lifecycle hooks (override these) ---------------------------------

    def on_candle(self, hour: int, bars: dict) -> None:
        """Called once per hour, after the C++ tick iterator has processed that
        hour (i.e. at the candle close). `bars` maps each symbol active this
        hour to its bid OHLC dict (`open/high/low/close`). Default: no-op."""

    def on_tick(self, symbol: str, bid: float, ask: float) -> None:
        """Opt-in, expensive per-tick hook. Enabled only when a subclass
        overrides it (detected at run start). When active the broker scans
        every tick and calls this for each one, so a full-dataset run costs
        tens of seconds even with an empty body — use it for short windows and
        feature exploration, not production sweeps. Default: no-op."""

    # --- order API (delegates to the bound Context; do not override) ------

    def buy(self, symbol: str, lots: float, sl=None, tp=None,
            limit=None, stop=None) -> None:
        self._ctx.buy(symbol, lots, sl=sl, tp=tp, limit=limit, stop=stop)

    def sell(self, symbol: str, lots: float, sl=None, tp=None,
             limit=None, stop=None) -> None:
        self._ctx.sell(symbol, lots, sl=sl, tp=tp, limit=limit, stop=stop)

    def close(self, symbol: str) -> None:
        self._ctx.close(symbol)

    def position(self, symbol: str) -> list:
        return self._ctx.position(symbol)

    @property
    def equity(self) -> float:
        return self._ctx.equity

    @property
    def cash(self) -> float:
        return self._ctx.cash

    # --- engine plumbing --------------------------------------------------

    def _bind(self, broker) -> None:
        self._ctx = Context(broker)
