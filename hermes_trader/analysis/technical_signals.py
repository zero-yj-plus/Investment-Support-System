"""可复算的本地技术信号算法。

所有滚动突破先 ``shift(1)``，预热期保持 NaN；事件与持续状态使用不同编码。
输入必须是单只股票的前复权 OHLCV，周/月线先合成 K 线再重算指标。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd

ALGORITHM_VERSION = "local-signal-v1"


@dataclass(frozen=True)
class SignalDefinition:
    signal_code: str
    signal_name: str
    category: str
    signal_kind: str = "event"
    direction: str = "bullish"
    defaults: tuple[tuple[str, Any], ...] = ()
    aliases: tuple[str, ...] = ()
    requires: str | None = None

    def payload(self, *, level2_available: bool) -> dict:
        available = self.requires != "level2" or level2_available
        return {
            "signal_code": self.signal_code,
            "signal_name": self.signal_name,
            "category": self.category,
            "signal_kind": self.signal_kind,
            "direction": self.direction,
            "default_params": dict(self.defaults),
            "aliases": list(self.aliases),
            "available": available,
            "unavailable_reason": None if available else "当前实例没有可核验的 Level-2/逐笔资金数据源",
        }


def _d(code: str, name: str, category: str, kind: str = "event", direction: str = "bullish", **kwargs) -> SignalDefinition:
    defaults = tuple((kwargs.pop("defaults", {}) or {}).items())
    aliases = tuple(kwargs.pop("aliases", ()) or ())
    return SignalDefinition(code, name, category, kind, direction, defaults, aliases, kwargs.pop("requires", None))


_DEFINITIONS = [
    _d("MACD_GOLDEN_CROSS", "MACD金叉", "趋势", defaults={"fast": 12, "slow": 26, "signal": 9}, aliases=("DIF", "DEA", "金叉")),
    _d("MACD_DEAD_CROSS", "MACD死叉", "趋势", direction="bearish", defaults={"fast": 12, "slow": 26, "signal": 9}),
    _d("MACD_ZERO_UP", "MACD上穿零轴", "趋势", defaults={"fast": 12, "slow": 26, "signal": 9}), _d("MACD_ZERO_DOWN", "MACD下穿零轴", "趋势", direction="bearish", defaults={"fast": 12, "slow": 26, "signal": 9}),
    _d("MACD_BULLISH_STATE", "DIF位于DEA上方", "趋势", "state", defaults={"fast": 12, "slow": 26, "signal": 9}),
    _d("KDJ_GOLDEN_CROSS", "KDJ金叉", "动量", defaults={"period": 9}), _d("KDJ_DEAD_CROSS", "KDJ死叉", "动量", direction="bearish", defaults={"period": 9}),
    _d("KDJ_BULLISH_STATE", "K值位于D值上方", "动量", "state", defaults={"period": 9}),
    _d("STOCHRSI_GOLDEN_CROSS", "StochRSI金叉", "动量", defaults={"rsi_period": 14, "stoch_period": 14, "smooth_k": 3, "smooth_d": 3}), _d("STOCHRSI_DEAD_CROSS", "StochRSI死叉", "动量", direction="bearish", defaults={"rsi_period": 14, "stoch_period": 14, "smooth_k": 3, "smooth_d": 3}),
    _d("TRIX_GOLDEN_CROSS", "TRIX金叉", "趋势", defaults={"trix_period": 12, "signal_period": 20}), _d("TRIX_DEAD_CROSS", "TRIX死叉", "趋势", direction="bearish", defaults={"trix_period": 12, "signal_period": 20}),
    _d("DMA_GOLDEN_CROSS", "DMA金叉", "趋势", defaults={"short_period": 10, "long_period": 50, "signal_period": 10}), _d("DMA_DEAD_CROSS", "DMA死叉", "趋势", direction="bearish", defaults={"short_period": 10, "long_period": 50, "signal_period": 10}),
    _d("PPO_GOLDEN_CROSS", "PPO金叉", "趋势", defaults={"fast": 12, "slow": 26, "signal": 9}), _d("PPO_DEAD_CROSS", "PPO死叉", "趋势", direction="bearish", defaults={"fast": 12, "slow": 26, "signal": 9}),
    _d("WT_GOLDEN_CROSS", "WaveTrend金叉", "动量", defaults={"channel_period": 10, "average_period": 21, "smooth_period": 4}), _d("WT_DEAD_CROSS", "WaveTrend死叉", "动量", direction="bearish", defaults={"channel_period": 10, "average_period": 21, "smooth_period": 4}),
    _d("DPO_GOLDEN_CROSS", "DPO金叉", "动量", defaults={"period": 11, "signal_period": 6}), _d("DPO_DEAD_CROSS", "DPO死叉", "动量", direction="bearish", defaults={"period": 11, "signal_period": 6}),
    _d("RVI_GOLDEN_CROSS", "RVI金叉", "动量", defaults={"period": 10, "signal_period": 4}), _d("RVI_DEAD_CROSS", "RVI死叉", "动量", direction="bearish", defaults={"period": 10, "signal_period": 4}),
    _d("EMV_GOLDEN_CROSS", "EMV金叉", "量价", defaults={"period": 14, "signal_period": 9}), _d("EMV_DEAD_CROSS", "EMV死叉", "量价", direction="bearish", defaults={"period": 14, "signal_period": 9}),
    _d("ROC_GOLDEN_CROSS", "ROC金叉", "动量", defaults={"period": 12, "signal_period": 6}), _d("ROC_DEAD_CROSS", "ROC死叉", "动量", direction="bearish", defaults={"period": 12, "signal_period": 6}),
    _d("FORCE_GOLDEN_CROSS", "Force Index金叉", "量价", defaults={"fast": 2, "slow": 13}), _d("FORCE_DEAD_CROSS", "Force Index死叉", "量价", direction="bearish", defaults={"fast": 2, "slow": 13}),
    _d("DMI_GOLDEN_CROSS", "DMI金叉", "趋势", defaults={"period": 14, "adx_threshold": 20}), _d("DMI_DEAD_CROSS", "DMI死叉", "趋势", direction="bearish", defaults={"period": 14, "adx_threshold": 20}),
    *[_d(f"BREAKUP_MA_{n}", f"向上突破MA{n}", "突破", defaults={"period": n}) for n in (5, 10, 20, 30, 60)],
    *[_d(f"BREAKDOWN_MA_{n}", f"向下跌破MA{n}", "突破", direction="bearish", defaults={"period": n}) for n in (5, 10, 20, 30, 60)],
    _d("CLOSE_ABOVE_MA", "收盘价位于均线上方", "趋势", "state", defaults={"period": 60}),
    _d("MA5_CROSS_UP_MA10", "MA5上穿MA10", "趋势"), _d("MA10_CROSS_UP_MA20", "MA10上穿MA20", "趋势"), _d("MA20_CROSS_UP_MA60", "MA20上穿MA60", "趋势"),
    _d("LONG_MA_ARRAY_START", "均线多头排列启动", "趋势"), _d("SHORT_MA_ARRAY_START", "均线空头排列启动", "趋势", direction="bearish"),
    _d("LONG_MA_ARRAY", "均线多头排列", "趋势", "state"), _d("SHORT_MA_ARRAY", "均线空头排列", "趋势", "state", "bearish"),
    _d("PRICE_BREAKOUT_N", "N日价格突破", "突破", defaults={"period": 20}), _d("PRICE_BREAKDOWN_N", "N日价格跌破", "突破", direction="bearish", defaults={"period": 20}),
    _d("HISTORICAL_NEW_HIGH", "创历史新高", "突破", defaults={"min_history": 250}), _d("HISTORICAL_NEW_LOW", "创历史新低", "突破", direction="bearish", defaults={"min_history": 250}),
    _d("BOLL_UPPER_BREAK", "突破BOLL上轨", "突破", defaults={"period": 20, "stddev": 2.0}), _d("BOLL_LOWER_BREAK", "跌破BOLL下轨", "突破", direction="bearish", defaults={"period": 20, "stddev": 2.0}), _d("BOLL_MIDDLE_RECLAIM", "重新站上BOLL中轨", "突破", defaults={"period": 20, "stddev": 2.0}),
    _d("SAR_BULLISH_FLIP", "SAR转多", "趋势", defaults={"step": 0.02, "maximum": 0.2}), _d("SAR_BEARISH_FLIP", "SAR转空", "趋势", direction="bearish", defaults={"step": 0.02, "maximum": 0.2}),
    _d("SUPERTREND_BULLISH_FLIP", "Supertrend转多", "趋势", defaults={"period": 14, "multiplier": 3.0}), _d("SUPERTREND_BEARISH_FLIP", "Supertrend转空", "趋势", direction="bearish", defaults={"period": 14, "multiplier": 3.0}),
    _d("ADX_TREND_STRENGTHEN", "趋势增强", "趋势", defaults={"threshold": 20}), _d("ADX_STRONG", "ADX趋势较强", "趋势", "state", defaults={"threshold": 20}),
    _d("RSI_OVERSOLD_RECOVERY", "RSI超卖修复", "动量", defaults={"period": 14, "threshold": 30}), _d("RSI_OVERBOUGHT_REVERSAL", "RSI超买回落", "动量", direction="bearish", defaults={"period": 14, "threshold": 70}),
    _d("RSI_OVERBOUGHT", "RSI超买", "风险", "state", "bearish", defaults={"period": 14, "threshold": 70}),
    _d("MFI_OVERSOLD_RECOVERY", "MFI超卖修复", "动量", defaults={"period": 14, "threshold": 20}), _d("MFI_OVERBOUGHT_REVERSAL", "MFI超买回落", "动量", direction="bearish", defaults={"period": 14, "threshold": 80}),
    _d("CCI_MINUS100_UP", "CCI上穿-100", "动量", defaults={"period": 14}), _d("CCI_PLUS100_UP", "CCI上穿+100", "动量", defaults={"period": 14}), _d("CCI_PLUS100_DOWN", "CCI跌破+100", "动量", direction="bearish", defaults={"period": 14}),
    _d("WR_OVERSOLD_RECOVERY", "WR超卖修复", "动量", defaults={"period": 14}), _d("WR_OVERBOUGHT_REVERSAL", "WR超买回落", "动量", direction="bearish", defaults={"period": 14}),
    _d("BIAS_NEGATIVE_RECOVERY", "负乖离修复", "动量", defaults={"period": 6, "threshold": -6}), _d("BIAS_POSITIVE_REVERSAL", "正乖离回落", "动量", direction="bearish", defaults={"period": 6, "threshold": 6}),
    _d("VOLUME_SPIKE", "成交量放大", "量价", "state", defaults={"period": 20, "multiplier": 1.5}), _d("VOLUME_BREAKOUT", "放量突破", "量价", defaults={"period": 20, "volume_period": 20, "multiplier": 1.5}),
    _d("PRICE_VOLUME_ATTACK", "放量上攻", "量价", defaults={"change_pct": 3, "multiplier": 1.5}), _d("RISING_WITH_VOLUME", "连涨放量", "量价", defaults={"days": 3}),
    _d("FALLING_WITHOUT_VOLUME", "下跌无量", "量价", direction="bearish", defaults={"days": 3, "ratio": 0.7}), _d("OBV_BREAKOUT_20", "OBV二十日突破", "量价", defaults={"period": 20}),
    _d("VOLUME_AT_LEAST_AVERAGE", "成交量不低于均量", "量价", "state", defaults={"period": 20}),
    *[_d(f"UP_{n}_DAYS", f"连涨{n}日", "形态", defaults={"days": n}) for n in (4, 8, 9)], _d("DOWN_7_DAYS", "连续下跌7日", "风险", direction="bearish", defaults={"days": 7}),
    _d("RETURN_N_ABOVE", "N日收益率高于阈值", "动量", "state", defaults={"period": 20, "threshold": 0}),
    _d("WIN_MARKET_N", "近N日跑赢大盘", "动量", "state", defaults={"period": 20}, requires="benchmark"),
    _d("CONSECUTIVE_SURGE", "连续大涨", "风险", "state", "bearish", defaults={"period": 5, "threshold": 20}),
    _d("ONE_LARGE_BULLISH", "一根大阳线", "K线形态", defaults={"body_pct": 4}), _d("TWO_LARGE_BULLISH", "两根大阳线", "K线形态", defaults={"body_pct": 4}),
    _d("NARROW_CONSOLIDATION", "窄幅整理", "K线形态", "state", defaults={"period": 10, "range_pct": 8}),
    _d("BULLISH_ENGULFING", "看涨吞没", "K线形态"), _d("BEARISH_ENGULFING", "看跌吞没", "K线形态", direction="bearish"),
    _d("INVERTED_HAMMER", "倒转锤头", "K线形态"), _d("SHOOTING_STAR", "射击之星", "K线形态", direction="bearish"),
    _d("MORNING_STAR", "早晨之星", "K线形态"), _d("EVENING_STAR", "黄昏之星", "K线形态", direction="bearish"),
    _d("PIERCING_PATTERN", "曙光初现", "K线形态"), _d("HARAMI_BULLISH", "看涨身怀六甲", "K线形态"), _d("HARAMI_BEARISH", "看跌身怀六甲", "K线形态", direction="bearish"), _d("DARK_CLOUD_COVER", "乌云盖顶", "K线形态", direction="bearish"),
    _d("HIGH_VOLATILITY", "高波动", "风险", "state", "bearish", defaults={"period": 20, "threshold": 4}),
    _d("DDX_POSITIVE", "DDX飘红", "资金", "state", requires="level2"), _d("MAIN_FORCE_NET_INFLOW", "主力资金净流入", "资金", "state", requires="level2"),
]
DEFINITIONS = {item.signal_code: item for item in _DEFINITIONS}


def signal_catalog(*, level2_available: bool = False) -> list[dict]:
    return [item.payload(level2_available=level2_available) for item in _DEFINITIONS]


def _false(index) -> pd.Series:
    return pd.Series(False, index=index, dtype=bool)


def cross_up(a: pd.Series, b: pd.Series | float) -> pd.Series:
    b = b if isinstance(b, pd.Series) else pd.Series(float(b), index=a.index)
    valid = a.notna() & b.notna() & a.shift(1).notna() & b.shift(1).notna()
    return (valid & (a.shift(1) <= b.shift(1)) & (a > b)).fillna(False)


def cross_down(a: pd.Series, b: pd.Series | float) -> pd.Series:
    b = b if isinstance(b, pd.Series) else pd.Series(float(b), index=a.index)
    valid = a.notna() & b.notna() & a.shift(1).notna() & b.shift(1).notna()
    return (valid & (a.shift(1) >= b.shift(1)) & (a < b)).fillna(False)


def normalized_frame(bars: list[dict]) -> pd.DataFrame:
    rows = []
    for raw in bars or []:
        date = str(raw.get("date") or raw.get("d") or "")[:10]
        row = {
            "date": pd.to_datetime(date, errors="coerce"),
            "open": raw.get("open", raw.get("o")), "high": raw.get("high", raw.get("h")),
            "low": raw.get("low", raw.get("l")), "close": raw.get("close", raw.get("c")),
            "volume": raw.get("v_shares", raw.get("volume", raw.get("v"))),
            "amount": raw.get("amount"),
        }
        rows.append(row)
    df = pd.DataFrame(rows, columns=["date", "open", "high", "low", "close", "volume", "amount"])
    if df.empty:
        return df
    for col in ("open", "high", "low", "close", "volume", "amount"):
        df[col] = pd.to_numeric(df[col], errors="coerce")
    return df.dropna(subset=["date"]).sort_values("date").drop_duplicates("date", keep="last").reset_index(drop=True)


def resample_frame(df: pd.DataFrame, timeframe: str, *, include_partial: bool = False) -> pd.DataFrame:
    if timeframe == "D" or df.empty:
        return df.copy()
    rule = "W-FRI" if timeframe == "W" else "ME"
    indexed = df.set_index("date")
    out = indexed.resample(rule).agg({"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum", "amount": "sum"}).dropna(subset=["open", "close"])
    if not include_partial and not out.empty:
        last_input = indexed.index.max()
        if timeframe == "W" and last_input.weekday() < 4:
            out = out.iloc[:-1]
        elif timeframe == "M" and last_input.to_period("M").end_time.normalize() > last_input.normalize():
            out = out.iloc[:-1]
    return out.reset_index()


def _ema(s, span): return s.ewm(span=max(1, int(span)), adjust=False, min_periods=max(1, int(span))).mean()
def _rsi(c, n=14):
    delta = c.diff(); gain = delta.clip(lower=0); loss = -delta.clip(upper=0)
    rs = gain.ewm(alpha=1 / n, adjust=False, min_periods=n).mean() / loss.ewm(alpha=1 / n, adjust=False, min_periods=n).mean().replace(0, np.nan)
    return 100 - 100 / (1 + rs)
def _tr(df):
    return pd.concat([(df.high-df.low), (df.high-df.close.shift()).abs(), (df.low-df.close.shift()).abs()], axis=1).max(axis=1)
def _adx(df, n=14):
    up=df.high.diff(); down=-df.low.diff(); tr=_tr(df)
    atr=tr.ewm(alpha=1/n, adjust=False, min_periods=n).mean()
    p=100*pd.Series(np.where((up>down)&(up>0),up,0),index=df.index).ewm(alpha=1/n,adjust=False,min_periods=n).mean()/atr
    m=100*pd.Series(np.where((down>up)&(down>0),down,0),index=df.index).ewm(alpha=1/n,adjust=False,min_periods=n).mean()/atr
    adx=(100*(p-m).abs()/(p+m).replace(0,np.nan)).ewm(alpha=1/n,adjust=False,min_periods=n).mean()
    return p,m,adx,atr
def _obv(c,v): return (np.sign(c.diff()).fillna(0)*v.fillna(0)).cumsum()
def _supertrend(df,n=14,mult=3.0):
    _,_,_,atr=_adx(df,n); mid=(df.high+df.low)/2; upper=mid+mult*atr; lower=mid-mult*atr
    st=pd.Series(np.nan,index=df.index); trend=pd.Series(False,index=df.index)
    for i in range(1,len(df)):
        if pd.isna(atr.iloc[i]): continue
        if not pd.isna(st.iloc[i-1]):
            upper.iloc[i]=upper.iloc[i] if upper.iloc[i]<upper.iloc[i-1] or df.close.iloc[i-1]>upper.iloc[i-1] else upper.iloc[i-1]
            lower.iloc[i]=lower.iloc[i] if lower.iloc[i]>lower.iloc[i-1] or df.close.iloc[i-1]<lower.iloc[i-1] else lower.iloc[i-1]
        prev=bool(trend.iloc[i-1]); trend.iloc[i]=df.close.iloc[i]>upper.iloc[i] if not prev else not (df.close.iloc[i]<lower.iloc[i])
        st.iloc[i]=lower.iloc[i] if trend.iloc[i] else upper.iloc[i]
    return st,trend
def _sar(df, step=.02, maximum=.2):
    sar=pd.Series(np.nan,index=df.index); bull=True; af=step
    if len(df)<2:return sar
    ep=df.high.iloc[0]; sar.iloc[0]=df.low.iloc[0]
    for i in range(1,len(df)):
        value=sar.iloc[i-1]+af*(ep-sar.iloc[i-1])
        if bull:
            value=min(value,df.low.iloc[i-1],df.low.iloc[max(0,i-2)])
            if df.low.iloc[i]<value: bull=False; value=ep; ep=df.low.iloc[i]; af=step
            elif df.high.iloc[i]>ep: ep=df.high.iloc[i]; af=min(maximum,af+step)
        else:
            value=max(value,df.high.iloc[i-1],df.high.iloc[max(0,i-2)])
            if df.high.iloc[i]>value: bull=True; value=ep; ep=df.high.iloc[i]; af=step
            elif df.low.iloc[i]<ep: ep=df.low.iloc[i]; af=min(maximum,af+step)
        sar.iloc[i]=value
    return sar


def _indicators(df: pd.DataFrame, p: dict) -> dict[str, pd.Series]:
    c,o,h,l,v=df.close,df.open,df.high,df.low,df.volume
    fast=int(p.get("fast",12)); slow=int(p.get("slow",26)); sig=int(p.get("signal",9))
    rsi_n=int(p.get("rsi_period",p.get("period",14))); dmi_n=int(p.get("period",14)); kdj_n=int(p.get("period",9))
    dif=_ema(c,fast)-_ema(c,slow); dea=_ema(dif,sig); rsi=_rsi(c,rsi_n); pdi,mdi,adx,atr=_adx(df,dmi_n)
    low9=l.rolling(kdj_n,min_periods=kdj_n).min(); high9=h.rolling(kdj_n,min_periods=kdj_n).max(); rsv=100*(c-low9)/(high9-low9).replace(0,np.nan); k=rsv.ewm(alpha=1/3,adjust=False,min_periods=3).mean(); d=k.ewm(alpha=1/3,adjust=False,min_periods=3).mean()
    typical=(h+l+c)/3; mfi_n=int(p.get("period",14)); pos=typical*v.where(typical.diff()>0,0); neg=typical*v.where(typical.diff()<0,0); mfi=100-100/(1+pos.rolling(mfi_n,min_periods=mfi_n).sum()/neg.rolling(mfi_n,min_periods=mfi_n).sum().replace(0,np.nan))
    cci_n=int(p.get("period",14)); cci=(typical-typical.rolling(cci_n,min_periods=cci_n).mean())/(.015*typical.rolling(cci_n,min_periods=cci_n).apply(lambda values:np.mean(np.abs(values-values.mean())),raw=True).replace(0,np.nan))
    wr_n=int(p.get("period",14)); wr=-100*(h.rolling(wr_n,min_periods=wr_n).max()-c)/(h.rolling(wr_n,min_periods=wr_n).max()-l.rolling(wr_n,min_periods=wr_n).min()).replace(0,np.nan)
    stoch_n=int(p.get("stoch_period",14)); smooth_k=int(p.get("smooth_k",3)); smooth_d=int(p.get("smooth_d",3)); stoch=(rsi-rsi.rolling(stoch_n,min_periods=stoch_n).min())/(rsi.rolling(stoch_n,min_periods=stoch_n).max()-rsi.rolling(stoch_n,min_periods=stoch_n).min()).replace(0,np.nan)*100; sk=stoch.rolling(smooth_k,min_periods=smooth_k).mean(); sd=sk.rolling(smooth_d,min_periods=smooth_d).mean()
    trix_n=int(p.get("trix_period",12)); trix_sig=int(p.get("signal_period",20)); trix=_ema(_ema(_ema(c,trix_n),trix_n),trix_n).pct_change()*100; trixs=trix.rolling(trix_sig,min_periods=trix_sig).mean()
    dma_short=int(p.get("short_period",10)); dma_long=int(p.get("long_period",50)); dma_sig=int(p.get("signal_period",10)); dma=c.rolling(dma_short,min_periods=dma_short).mean()-c.rolling(dma_long,min_periods=dma_long).mean(); dmas=dma.rolling(dma_sig,min_periods=dma_sig).mean()
    ppo=100*(_ema(c,fast)-_ema(c,slow))/_ema(c,slow).replace(0,np.nan); ppos=_ema(ppo,sig)
    wt_channel=int(p.get("channel_period",10)); wt_average=int(p.get("average_period",21)); wt_smooth=int(p.get("smooth_period",4)); esa=_ema(typical,wt_channel); dev=_ema((typical-esa).abs(),wt_channel); ci=(typical-esa)/(.015*dev.replace(0,np.nan)); wt1=_ema(ci,wt_average); wt2=wt1.rolling(wt_smooth,min_periods=wt_smooth).mean()
    dpo_n=int(p.get("period",11)); dpo_sig=int(p.get("signal_period",6)); dpo=c.shift(dpo_n//2+1)-c.rolling(dpo_n,min_periods=dpo_n).mean(); dpos=dpo.rolling(dpo_sig,min_periods=dpo_sig).mean()
    rvi_n=int(p.get("period",10)); rvi_sig=int(p.get("signal_period",4)); rvi=(c-o).rolling(rvi_n,min_periods=rvi_n).mean()/(h-l).rolling(rvi_n,min_periods=rvi_n).mean().replace(0,np.nan); rvis=rvi.rolling(rvi_sig,min_periods=rvi_sig).mean()
    emv_n=int(p.get("period",14)); emv_sig=int(p.get("signal_period",9)); emv_raw=((h+l)/2-(h.shift()+l.shift())/2)*(h-l)/v.replace(0,np.nan); emv=emv_raw.rolling(emv_n,min_periods=emv_n).mean(); emva=emv.rolling(emv_sig,min_periods=emv_sig).mean()
    roc_n=int(p.get("period",12)); roc_sig=int(p.get("signal_period",6)); roc=c.pct_change(roc_n)*100; rocs=roc.rolling(roc_sig,min_periods=roc_sig).mean(); force=c.diff()*v; f2=_ema(force,fast); f13=_ema(force,slow)
    return locals()


def evaluate_condition(df: pd.DataFrame, condition: dict, *, precomputed: dict[str, pd.Series] | None = None) -> dict:
    code=str(condition.get("signal_code") or ""); definition=DEFINITIONS.get(code); params=dict(definition.defaults) if definition else {}; params.update(condition.get("params") or {})
    if df.empty or not definition or definition.requires:
        return {"mask":_false(df.index),"metrics":{},"params":params,"definition":definition}
    x=precomputed if precomputed is not None else _indicators(df,params); c,o,h,l,v=x["c"],x["o"],x["h"],x["l"],x["v"]; mask=_false(df.index); metrics={}
    pairs={"MACD_GOLDEN_CROSS":(x["dif"],x["dea"],1),"MACD_DEAD_CROSS":(x["dif"],x["dea"],-1),"KDJ_GOLDEN_CROSS":(x["k"],x["d"],1),"KDJ_DEAD_CROSS":(x["k"],x["d"],-1),"STOCHRSI_GOLDEN_CROSS":(x["sk"],x["sd"],1),"STOCHRSI_DEAD_CROSS":(x["sk"],x["sd"],-1),"TRIX_GOLDEN_CROSS":(x["trix"],x["trixs"],1),"TRIX_DEAD_CROSS":(x["trix"],x["trixs"],-1),"DMA_GOLDEN_CROSS":(x["dma"],x["dmas"],1),"DMA_DEAD_CROSS":(x["dma"],x["dmas"],-1),"PPO_GOLDEN_CROSS":(x["ppo"],x["ppos"],1),"PPO_DEAD_CROSS":(x["ppo"],x["ppos"],-1),"WT_GOLDEN_CROSS":(x["wt1"],x["wt2"],1),"WT_DEAD_CROSS":(x["wt1"],x["wt2"],-1),"DPO_GOLDEN_CROSS":(x["dpo"],x["dpos"],1),"DPO_DEAD_CROSS":(x["dpo"],x["dpos"],-1),"RVI_GOLDEN_CROSS":(x["rvi"],x["rvis"],1),"RVI_DEAD_CROSS":(x["rvi"],x["rvis"],-1),"EMV_GOLDEN_CROSS":(x["emv"],x["emva"],1),"EMV_DEAD_CROSS":(x["emv"],x["emva"],-1),"ROC_GOLDEN_CROSS":(x["roc"],x["rocs"],1),"ROC_DEAD_CROSS":(x["roc"],x["rocs"],-1),"FORCE_GOLDEN_CROSS":(x["f2"],x["f13"],1),"FORCE_DEAD_CROSS":(x["f2"],x["f13"],-1),"DMI_GOLDEN_CROSS":(x["pdi"],x["mdi"],1),"DMI_DEAD_CROSS":(x["pdi"],x["mdi"],-1)}
    if code in pairs:
        a,b,direction=pairs[code]; mask=cross_up(a,b) if direction>0 else cross_down(a,b)
        if code.startswith("DMI_"): mask &= x["adx"]>=float(params.get("adx_threshold",20))
        metrics={"primary":a,"comparison":b}
    elif code=="MACD_ZERO_UP": mask=cross_up(x["dif"],0)
    elif code=="MACD_ZERO_DOWN": mask=cross_down(x["dif"],0)
    elif code=="MACD_BULLISH_STATE": mask=(x["dif"]>x["dea"])&x["dif"].notna(); metrics={"DIF":x["dif"],"DEA":x["dea"]}
    elif code=="KDJ_BULLISH_STATE": mask=(x["k"]>x["d"])&x["k"].notna(); metrics={"K":x["k"],"D":x["d"]}
    elif code.startswith("BREAKUP_MA_") or code.startswith("BREAKDOWN_MA_"):
        n=int(code.rsplit("_",1)[-1]); ma=c.rolling(n,min_periods=n).mean(); mask=cross_up(c,ma) if "BREAKUP" in code else cross_down(c,ma); metrics={"close":c,"ma":ma}
    elif code=="CLOSE_ABOVE_MA": n=int(params.get("period",60)); ma=c.rolling(n,min_periods=n).mean(); mask=(c>ma)&ma.notna(); metrics={"close":c,"ma":ma}
    elif code in {"MA5_CROSS_UP_MA10","MA10_CROSS_UP_MA20","MA20_CROSS_UP_MA60"}:
        a,b={"MA5_CROSS_UP_MA10":(5,10),"MA10_CROSS_UP_MA20":(10,20),"MA20_CROSS_UP_MA60":(20,60)}[code]; mask=cross_up(c.rolling(a,min_periods=a).mean(),c.rolling(b,min_periods=b).mean())
    elif code in {"LONG_MA_ARRAY","SHORT_MA_ARRAY","LONG_MA_ARRAY_START","SHORT_MA_ARRAY_START"}:
        mas=[c.rolling(n,min_periods=n).mean() for n in (5,10,20,30,60)]; state=(mas[0]>mas[1])&(mas[1]>mas[2])&(mas[2]>mas[3])&(mas[3]>mas[4]); state=state if code.startswith("LONG") else ((mas[0]<mas[1])&(mas[1]<mas[2])&(mas[2]<mas[3])&(mas[3]<mas[4])); mask=(state&~state.shift(1,fill_value=False)) if code.endswith("START") else state
    elif code in {"PRICE_BREAKOUT_N","PRICE_BREAKDOWN_N"}:
        n=int(params.get("period",20)); threshold=(h.shift(1).rolling(n,min_periods=n).max() if "BREAKOUT" in code else l.shift(1).rolling(n,min_periods=n).min()); mask=(c>threshold) if "BREAKOUT" in code else (c<threshold); mask=mask&threshold.notna(); metrics={"close":c,"threshold":threshold}
    elif code in {"HISTORICAL_NEW_HIGH","HISTORICAL_NEW_LOW"}:
        n=int(params.get("min_history",250)); threshold=(h.shift().expanding(n).max() if code.endswith("HIGH") else l.shift().expanding(n).min()); mask=(h>threshold) if code.endswith("HIGH") else (l<threshold); mask&=threshold.notna()
    else:
        mask,metrics=_evaluate_extended(code,df,x,params)
    return {"mask":mask.fillna(False).astype(bool),"metrics":metrics,"params":params,"definition":definition}


def _evaluate_extended(code,df,x,p):
    c,o,h,l,v=x["c"],x["o"],x["h"],x["l"],x["v"]; false=_false(df.index); metrics={}
    boll_n=int(p.get("period",20)); boll_mult=float(p.get("stddev",2)); boll=c.rolling(boll_n,min_periods=boll_n).mean(); std=c.rolling(boll_n,min_periods=boll_n).std(); upper=boll+boll_mult*std; lower=boll-boll_mult*std
    if code=="BOLL_UPPER_BREAK": return cross_up(c,upper),{"close":c,"boll_upper":upper}
    if code=="BOLL_LOWER_BREAK": return cross_down(c,lower),{"close":c,"boll_lower":lower}
    if code=="BOLL_MIDDLE_RECLAIM": return cross_up(c,boll),{"close":c,"boll_middle":boll}
    if code.startswith("SAR_"): sar=_sar(df,float(p.get("step",.02)),float(p.get("maximum",.2))); return (cross_up(c,sar) if "BULLISH" in code else cross_down(c,sar)),{"close":c,"sar":sar}
    if code.startswith("SUPERTREND_"): st,_=_supertrend(df,int(p.get("period",14)),float(p.get("multiplier",3))); return (cross_up(c,st) if "BULLISH" in code else cross_down(c,st)),{"close":c,"supertrend":st}
    if code=="ADX_TREND_STRENGTHEN": t=float(p.get("threshold",20)); return (x["adx"]>=t)&(x["adx"]>x["adx"].shift()),{"adx":x["adx"]}
    if code=="ADX_STRONG": return x["adx"]>=float(p.get("threshold",20)),{"adx":x["adx"]}
    level_codes={"RSI_OVERSOLD_RECOVERY":(x["rsi"],float(p.get("threshold",30)),1),"RSI_OVERBOUGHT_REVERSAL":(x["rsi"],float(p.get("threshold",70)),-1),"MFI_OVERSOLD_RECOVERY":(x["mfi"],float(p.get("threshold",20)),1),"MFI_OVERBOUGHT_REVERSAL":(x["mfi"],float(p.get("threshold",80)),-1),"CCI_MINUS100_UP":(x["cci"],-100,1),"CCI_PLUS100_UP":(x["cci"],100,1),"CCI_PLUS100_DOWN":(x["cci"],100,-1),"WR_OVERSOLD_RECOVERY":(x["wr"],-80,1),"WR_OVERBOUGHT_REVERSAL":(x["wr"],-20,-1)}
    if code in level_codes:
        s,level,direction=level_codes[code]; return (cross_up(s,level) if direction>0 else cross_down(s,level)),{"value":s,"threshold":pd.Series(level,index=df.index)}
    if code=="RSI_OVERBOUGHT": t=float(p.get("threshold",70)); return x["rsi"]>=t,{"rsi":x["rsi"]}
    if code.startswith("BIAS_"): n=int(p.get("period",6)); bias=(c/c.rolling(n,min_periods=n).mean()-1)*100; t=float(p.get("threshold",-6 if "NEGATIVE" in code else 6)); return (cross_up(bias,t) if "NEGATIVE" in code else cross_down(bias,t)),{"bias":bias}
    vm=v.rolling(int(p.get("volume_period",p.get("period",20))),min_periods=int(p.get("volume_period",p.get("period",20)))).mean(); mult=float(p.get("multiplier",1.5))
    if code=="VOLUME_SPIKE": return v>=vm*mult,{"volume":v,"volume_ma":vm,"ratio":v/vm}
    if code=="VOLUME_AT_LEAST_AVERAGE": return v>=vm,{"volume":v,"volume_ma":vm}
    if code=="VOLUME_BREAKOUT": n=int(p.get("period",20)); th=h.shift().rolling(n,min_periods=n).max(); return (c>th)&(v>=vm*mult),{"close":c,"threshold":th,"volume_ratio":v/vm}
    if code=="PRICE_VOLUME_ATTACK": pct=(c/o-1)*100; return (c>o)&(pct>=float(p.get("change_pct",3)))&(v>=vm*mult),{"body_pct":pct,"volume_ratio":v/vm}
    if code in {"RISING_WITH_VOLUME","FALLING_WITHOUT_VOLUME"}:
        n=int(p.get("days",3)); up=(c.diff()>0).rolling(n,min_periods=n).sum()==n; down=(c.diff()<0).rolling(n,min_periods=n).sum()==n; rising=(v.diff()>0).rolling(n,min_periods=n).sum()==n
        return (up&rising if code.startswith("RISING") else down&(v<=vm*float(p.get("ratio",.7)))),{"volume_ratio":v/vm}
    if code=="OBV_BREAKOUT_20": n=int(p.get("period",20)); obv=_obv(c,v); return obv>obv.shift().rolling(n,min_periods=n).max(),{"obv":obv}
    if code.startswith("UP_") or code=="DOWN_7_DAYS":
        n=int(p.get("days",code.split("_")[1])); state=((c.diff()>0) if code.startswith("UP") else (c.diff()<0)).rolling(n,min_periods=n).sum()==n; return state&~state.shift(1,fill_value=False),{}
    body=(c-o)/o.replace(0,np.nan)*100; bull=(c>o)&(body>=float(p.get("body_pct",4)))
    if code=="ONE_LARGE_BULLISH": return bull,{"body_pct":body}
    if code=="TWO_LARGE_BULLISH": return bull&bull.shift(1,fill_value=False),{"body_pct":body}
    if code=="NARROW_CONSOLIDATION": n=int(p.get("period",10)); rp=(h.rolling(n,min_periods=n).max()-l.rolling(n,min_periods=n).min())/c.rolling(n,min_periods=n).mean()*100; return rp<=float(p.get("range_pct",8)),{"range_pct":rp}
    if code in {"RETURN_N_ABOVE","CONSECUTIVE_SURGE"}:
        n=int(p.get("period",20 if code=="RETURN_N_ABOVE" else 5)); ret=(c/c.shift(n)-1)*100; threshold=float(p.get("threshold",0 if code=="RETURN_N_ABOVE" else 20)); return ret>threshold,{"return_pct":ret,"threshold":pd.Series(threshold,index=df.index)}
    prev_bull=c.shift()>o.shift(); prev_bear=c.shift()<o.shift(); bull_now=c>o; bear_now=c<o; body_abs=(c-o).abs(); upper_shadow=h-pd.concat([c,o],axis=1).max(axis=1); lower_shadow=pd.concat([c,o],axis=1).min(axis=1)-l
    patterns={
        "BULLISH_ENGULFING":prev_bear&bull_now&(o<=c.shift())&(c>=o.shift()), "BEARISH_ENGULFING":prev_bull&bear_now&(o>=c.shift())&(c<=o.shift()),
        "INVERTED_HAMMER":bull_now&(upper_shadow>=body_abs*2)&(lower_shadow<=body_abs*.5), "SHOOTING_STAR":bear_now&(upper_shadow>=body_abs*2)&(lower_shadow<=body_abs*.5),
        "HARAMI_BULLISH":prev_bear&bull_now&(o>c.shift())&(c<o.shift()), "HARAMI_BEARISH":prev_bull&bear_now&(o<c.shift())&(c>o.shift()),
        "PIERCING_PATTERN":prev_bear&bull_now&(o<c.shift())&(c>(o.shift()+c.shift())/2)&(c<o.shift()), "DARK_CLOUD_COVER":prev_bull&bear_now&(o>c.shift())&(c<(o.shift()+c.shift())/2)&(c>o.shift()),
        "MORNING_STAR":(c.shift(2)<o.shift(2))&(body_abs.shift(1)<body_abs.shift(2)*.5)&bull_now&(c>(o.shift(2)+c.shift(2))/2),
        "EVENING_STAR":(c.shift(2)>o.shift(2))&(body_abs.shift(1)<body_abs.shift(2)*.5)&bear_now&(c<(o.shift(2)+c.shift(2))/2),
    }
    if code in patterns:return patterns[code],{"body_pct":body}
    if code=="HIGH_VOLATILITY": n=int(p.get("period",20)); vol=c.pct_change().rolling(n,min_periods=n).std()*100; return vol>=float(p.get("threshold",4)),{"volatility":vol}
    return false,metrics


def default_signal_matches(df: pd.DataFrame, timeframe: str, *, history_days: int = 120) -> list[dict]:
    out=[]
    if df.empty:return out
    cutoff=df.date.iloc[max(0,len(df)-history_days)]
    # 默认目录的 MACD/KDJ/ADX 等核心参数共用同一套指标列；每个条件重复计算整套
    # EMA/滚动指标会让 5500 只股票的盘后任务放大近百倍。
    precomputed=_indicators(df,{})
    for definition in _DEFINITIONS:
        if definition.requires: continue
        result=evaluate_condition(
            df,{"signal_code":definition.signal_code,"params":dict(definition.defaults)},
            precomputed=precomputed,
        )
        mask=result["mask"]
        indexes=[len(df)-1] if definition.signal_kind=="state" and bool(mask.iloc[-1]) else list(df.index[mask & (df.date>=cutoff)])
        for i in indexes:
            values={}
            for key,series in result["metrics"].items():
                try:
                    value=series.iloc[i]
                    if pd.notna(value): values[key]=round(float(value),6)
                except (AttributeError,IndexError,TypeError,ValueError): pass
            out.append({"date":df.date.iloc[i].date().isoformat(),"timeframe":timeframe,"signal_code":definition.signal_code,"signal_name":definition.signal_name,"signal_kind":definition.signal_kind,"direction":definition.direction,"params":result["params"],"metrics":values,"algorithm_ver":ALGORITHM_VERSION})
    return out
