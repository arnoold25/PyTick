import math


def _price(x) -> float:
    """None -> NaN, which the C++ broker reads as 'no level set'."""
    return math.nan if x is None else float(x)


class Strategy:
    """
    Base class for user strategies. Subclass it and override `on_candle`
    (and optionally `on_tick`); pass the subclass to `Backtester.run`.

    The engine binds a C++ broker to the instance before the run, so the
    order helpers below forward straight into the broker. Orders placed in
    `on_candle` fill on the next hour's first tick (decide at close, fill
    next — never look-ahead).
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

    # --- order API (do not override) --------------------------------------

    def buy(self, symbol: str, lots: float, sl=None, tp=None) -> None:
        """Open a long: fills at the next tick's ask. `sl`/`tp` are absolute
        prices (optional)."""
        self._broker.submit_buy(symbol, float(lots), _price(sl), _price(tp))

    def sell(self, symbol: str, lots: float, sl=None, tp=None) -> None:
        """Open a short: fills at the next tick's bid."""
        self._broker.submit_sell(symbol, float(lots), _price(sl), _price(tp))

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
        """Realized cash (initial capital + closed-trade P&L)."""
        return self._broker.cash()

    # --- engine plumbing --------------------------------------------------

    def _bind(self, broker) -> None:
        self._broker = broker
