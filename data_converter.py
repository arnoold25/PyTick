import lzma
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

US_PER_HOUR = 3_600 * 1_000_000  # Mikrosekunden pro Stunde
_BI5_DTYPE = np.dtype([
    ("ms",      ">u4"),  # Millisekunden ab Stundenbeginn der Datei
    ("ask",     ">u4"),  # punkt-skalierter Integer-Preis
    ("bid",     ">u4"),  # punkt-skalierter Integer-Preis
    ("ask_vol", ">f4"),  # Ask-Volumen (Mio. Basiswährung)
    ("bid_vol", ">f4"),  # Bid-Volumen (Mio. Basiswährung)
])
POINT_DIVISOR: dict[str, float] = {
    "EURUSD": 100_000, "GBPUSD": 100_000, "AUDUSD": 100_000,
    "USDCAD": 100_000, "USDCHF": 100_000, "NZDUSD": 100_000,
    "USDJPY": 1_000, "EURJPY": 1_000, "GBPJPY": 1_000,
    "XAUUSD": 1_000,
}
_COLUMNS = ("ts", "bid", "ask", "bid_vol", "ask_vol")

class Bi5Converter:
    """
    Konvertiert ein Verzeichnis LZMA-komprimierter Dukascopy-.bi5-Dateien in
    ein Struct-of-Arrays-Layout: eine .npy pro Spalte plus ein dreispaltiger
    Stunden-Index.

    Die beiden Stufen sind unabhängig nutzbar:
      * parse_file(path)  -> eine .bi5 in native numpy-Arrays dekodieren
      * build()           -> alle Dateien dekodieren, concatenieren, prüfen, schreiben
    """

    def __init__(
        self,
        symbol: str,
        src_dir: str | Path,
        out_dir: str | Path = "data/npy",
        divisor: float | None = None,
    ) -> None:
        self.symbol = symbol
        self.src_dir = Path(src_dir)
        self.out_dir = Path(out_dir) / symbol

        if divisor is not None:
            self.divisor = float(divisor)
        elif symbol in POINT_DIVISOR:
            self.divisor = float(POINT_DIVISOR[symbol])
        else:
            raise ValueError(
                f"Kein Preis-Divisor für {symbol!r} bekannt. divisor=... explizit "
                f"übergeben (ein falscher Divisor korrumpiert still jeden Preis)."
            )

    # --- Einzeldatei-Stufe -------------------------------------------------

    @staticmethod
    def hour_start_us(filename: str) -> int:
        """
        Absoluter Stundenbeginn der Datei, in Mikrosekunden seit Epoch.

        Tickterial-Namensschema: SYMBOL_YYYY-MM-DD_HH(.bi5). Der .bi5-Inhalt
        speichert nur ms-Offsets innerhalb der Stunde, die absolute Stunde muss
        also aus dem Dateinamen kommen. Für ein anderes Schema diese Methode
        überschreiben.
        """
        parts = Path(filename).stem.split("_")
        dt = datetime.strptime(f"{parts[-2]}_{parts[-1]}", "%Y-%m-%d_%H")
        dt = dt.replace(tzinfo=timezone.utc)             # Dukascopy-Zeiten sind UTC
        return int(dt.timestamp()) * 1_000_000           # exakt: volle Stunde

    def parse_file(self, path: str | Path):
        """
        Dekodiert eine .bi5 in (ts, bid, ask, bid_vol, ask_vol) als native
        numpy-Arrays. Gibt None für leere Stunden (Wochenende / Markt zu) und
        für unlesbare Dateien zurück. ts ist absolute int64-Mikrosekunden.
        """
        path = Path(path)
        try:
            with lzma.open(path) as f:
                raw = f.read()
        except (lzma.LZMAError, EOFError):
            print(f"[skip] korrupt: {path.name}")
            return None

        if not raw:                                      # gültig, aber leere Stunde
            return None
        if len(raw) % _BI5_DTYPE.itemsize:               # abgebrochener Download
            print(f"[skip] unvollständig: {path.name}")
            return None

        rec = np.frombuffer(raw, dtype=_BI5_DTYPE)
        base = self.hour_start_us(path.name)

        ts = base + rec["ms"].astype(np.int64) * 1_000   # ms-Offset -> absolute µs
        bid = rec["bid"].astype(np.float64) / self.divisor
        ask = rec["ask"].astype(np.float64) / self.divisor
        bid_vol = rec["bid_vol"].astype(np.float64)
        ask_vol = rec["ask_vol"].astype(np.float64)
        return ts, bid, ask, bid_vol, ask_vol

    # --- Gesamtdatensatz-Stufe ---------------------------------------------

    def build(self) -> int:
        """
        Dekodiert jede .bi5 in src_dir, concateniert chronologisch, prüft und
        schreibt die SoA-Spalten plus den Stunden-Index nach out_dir.
        Gibt die Anzahl der Ticks zurück.
        """
        files = sorted(self.src_dir.iterdir())           # SYMBOL_YYYY-MM-DD_HH sortiert chronologisch
        if not files:
            raise FileNotFoundError(f"Keine Dateien in {self.src_dir}")

        cols: list[list[np.ndarray]] = [[] for _ in _COLUMNS]
        for path in files:
            parsed = self.parse_file(path)
            if parsed is None:
                continue
            for bucket, arr in zip(cols, parsed):
                bucket.append(arr)

        if not cols[0]:
            raise RuntimeError(f"Keine verwertbaren Ticks für {self.symbol}")

        # HINWEIS: Chunks + Ergebnis liegen kurz gleichzeitig im RAM
        # (~6-12 GB bei 150M Ticks x5). Für knapperen Speicher: Längen in einem
        # ersten Pass summieren, dann in np.lib.format.open_memmap-Arrays schreiben.
        ts, bid, ask, bid_vol, ask_vol = (np.concatenate(c) for c in cols)

        # --- Korrektheits-Guards: billiger als ein stiller Bug ---
        assert np.all(np.diff(ts) >= 0), "Timestamps nicht monoton - Dateireihenfolge prüfen"
        n = len(ts)
        assert all(len(a) == n for a in (bid, ask, bid_vol, ask_vol)), "Spaltenlängen ungleich"
        assert (ask >= bid).mean() > 0.99, "ask/bid vermutlich vertauscht"

        self.out_dir.mkdir(parents=True, exist_ok=True)
        np.save(self.out_dir / "ts.npy", ts)
        np.save(self.out_dir / "bid.npy", bid)
        np.save(self.out_dir / "ask.npy", ask)
        np.save(self.out_dir / "bid_vol.npy", bid_vol)
        np.save(self.out_dir / "ask_vol.npy", ask_vol)
        np.save(self.out_dir / "index.npy", self._build_index(ts))

        print(f"[done] {self.symbol}: {n:,} Ticks -> {self.out_dir}")
        return n

    @staticmethod
    def _build_index(ts: np.ndarray) -> np.ndarray:
        """
        Dreispaltiger Stunden-Index [hour_start_us, start_row, end_row) aus dem
        finalen ts-Array. Zeilen sind halboffen [start, end). Stunden ohne Ticks
        haben keine Zeile. Setzt sortiertes ts voraus (durch build() garantiert).
        """
        hours = ts // US_PER_HOUR                        # Integer-Stunden-Bucket pro Tick
        change = np.flatnonzero(np.diff(hours)) + 1      # erste Zeile jeder neuen Stunde
        starts = np.concatenate(([0], change))
        ends = np.concatenate((change, [len(ts)]))
        hour_ts = hours[starts] * US_PER_HOUR            # zurück zu absoluten µs
        return np.column_stack([hour_ts, starts, ends]).astype(np.int64)


symbols = [j.name for j in Path("data", "raw").iterdir()]
for i in symbols:
    Bi5Converter(i, Path("data", "raw", i), divisor=POINT_DIVISOR[i]).build()