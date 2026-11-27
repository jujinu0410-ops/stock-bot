"""Indicator series shared by the existing E30 scan and its advisories."""


def vwap_series(frame, periods):
    volume = frame.Volume
    typical_price = (frame.High + frame.Low + frame.Close) / 3
    return (typical_price * volume).rolling(periods).sum() / volume.rolling(periods).sum()


def obv_series(close, volume):
    delta = close.diff()
    obv = volume.where(delta > 0, (-volume).where(delta < 0, 0)).cumsum()
    return obv, obv.rolling(9).mean()


def macd_series(close):
    macd = close.ewm(span=12, adjust=False).mean() - close.ewm(span=26, adjust=False).mean()
    return macd, macd.ewm(span=9, adjust=False).mean()
