class Strategy:

    def on_candle(self, hour: int, bars: dict) -> None:
        pass

    def on_tick(self, symbol: str, bid: float, ask: float) -> None:
        pass