"""
Binance Otomatik Trading Bot
- Onay yok, kendi karar verir
- Alım/satım sonuçlarını Telegram'a bildirir
- 500 TL (~13 USDT) başlangıç bakiyesi
"""

import asyncio
import logging
import time
import os
from datetime import datetime

import numpy as np
from binance.client import Client
from binance.exceptions import BinanceAPIException
from telegram import Bot, Update
from telegram.ext import Application, CommandHandler, ContextTypes

import config

logging.basicConfig(
    format="%(asctime)s — %(levelname)s — %(message)s",
    level=logging.INFO,
    handlers=[logging.FileHandler("bot.log"), logging.StreamHandler()]
)
log = logging.getLogger(__name__)

binance = Client(config.BINANCE_API_KEY, config.BINANCE_API_SECRET)

import json

# Açık pozisyonlar
open_positions = {}

# Son sinyal zamanları
last_signal_time = {}
SIGNAL_COOLDOWN = 3600

# Bakiye takibi
current_balance_usdt = 0.0
total_profit = 0.0

POSITIONS_FILE = "positions.json"

def save_positions():
    try:
        with open(POSITIONS_FILE, "w") as f:
            json.dump({"positions": open_positions, "total_profit": total_profit}, f)
    except Exception as e:
        log.error(f"Pozisyon kaydetme hatası: {e}")

def load_positions():
    global open_positions, total_profit
    try:
        with open(POSITIONS_FILE, "r") as f:
            data = json.load(f)
            open_positions = data.get("positions", {})
            total_profit = data.get("total_profit", 0.0)
            if open_positions:
                log.info(f"Kaydedilmiş {len(open_positions)} pozisyon yüklendi.")
    except:
        pass


# ============================================================
#  TEKNİK ANALİZ
# ============================================================

def calculate_rsi(prices, period=14):
    if len(prices) < period + 1:
        return 50.0
    deltas = np.diff(prices)
    gains  = np.where(deltas > 0, deltas, 0)
    losses = np.where(deltas < 0, -deltas, 0)
    avg_gain = np.mean(gains[-period:])
    avg_loss = np.mean(losses[-period:])
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return round(100 - (100 / (1 + rs)), 2)

def calculate_ema(prices, period):
    if len(prices) < period:
        return prices[-1]
    k = 2 / (period + 1)
    ema = prices[0]
    for p in prices[1:]:
        ema = p * k + ema * (1 - k)
    return ema

def analyze_coin(symbol):
    try:
        klines = binance.get_klines(
            symbol=symbol,
            interval=Client.KLINE_INTERVAL_15MINUTE,
            limit=100
        )
        if len(klines) < 50:
            return None

        closes  = [float(k[4]) for k in klines]
        volumes = [float(k[5]) for k in klines]
        highs   = [float(k[2]) for k in klines]
        lows    = [float(k[3]) for k in klines]

        price  = closes[-1]
        vol    = volumes[-1]
        avg_vol = np.mean(volumes[-21:-1])
        vol_ratio = vol / avg_vol if avg_vol > 0 else 0

        rsi  = calculate_rsi(closes)
        ema9  = calculate_ema(closes, 9)
        ema21 = calculate_ema(closes, 21)

        tr_list = [max(highs[i]-lows[i], abs(highs[i]-closes[i-1]), abs(lows[i]-closes[i-1]))
                   for i in range(1, len(closes))]
        atr_pct = (np.mean(tr_list[-14:]) / price) * 100 if tr_list else 0

        conditions = {
            "RSI aşırı satım":  rsi < config.RSI_BUY_THRESHOLD,
            "Hacim patlaması":  vol_ratio >= config.MIN_VOLUME_SPIKE,
            "Trend yukarı":     ema9 > ema21,
            "Volatilite makul": 0.3 < atr_pct < 8.0,
        }

        passed = sum(conditions.values())
        # RSI mutlaka 60 altında olmalı (zorunlu koşul)
        if rsi > 60:
            return None
        if passed < 3:
            return None

        now = time.time()
        if now - last_signal_time.get(symbol, 0) < SIGNAL_COOLDOWN:
            return None

        score = int((passed / len(conditions)) * 100)
        score = min(100, score + max(0, (config.RSI_BUY_THRESHOLD - rsi) * 2)
                             + min(20, (vol_ratio - config.MIN_VOLUME_SPIKE) * 5))

        return {
            "symbol":    symbol,
            "price":     price,
            "rsi":       rsi,
            "vol_ratio": round(vol_ratio, 2),
            "score":     round(score),
            "passed":    passed,
            "total":     len(conditions),
            "reason":    ", ".join(k for k, v in conditions.items() if v),
        }
    except Exception as e:
        log.debug(f"{symbol}: {e}")
        return None


# ============================================================
#  BAKIYE & POZİSYON BOYUTU
# ============================================================

def get_real_balance():
    """Binance'den gerçek USDT bakiyesini al"""
    try:
        bal = binance.get_asset_balance(asset="USDT")
        return float(bal["free"]) if bal else 0.0
    except Exception as e:
        log.error(f"Bakiye hatası: {e}")
        return 0.0

def calc_position_size(balance_usdt):
    """
    Bakiyeye göre akıllı pozisyon boyutu.
    - Küçük bakiyede: %40-50 (2-3 farklı coin)
    - Min 6 USDT (Binance notional filter için)
    """
    if len(open_positions) >= 3:
        return 0  # Maks 3 açık pozisyon

    free_slots = 3 - len(open_positions)
    per_trade = balance_usdt / max(free_slots, 2)
    per_trade = min(per_trade, balance_usdt * 0.45)
    per_trade = max(per_trade, 6.0)  # Min 6 USDT
    if per_trade > balance_usdt:
        return 0
    return round(per_trade, 2)


# ============================================================
#  İŞLEM YAPMA
# ============================================================

def fmt(p):
    if p >= 1000:  return f"${p:,.2f}"
    if p >= 1:     return f"${p:.4f}"
    if p >= 0.001: return f"${p:.6f}"
    return f"${p:.8f}"

def fmt_try(usdt):
    try_val = usdt * 38.5
    return f"₺{try_val:.2f} (${usdt:.2f})"

def execute_buy(symbol, price, amount_usdt):
    if config.TEST_MODE:
        qty = round(amount_usdt / price, 6)
        return qty
    try:
        # Piyasa açık mı kontrol et
        info = binance.get_symbol_info(symbol)
        if not info or info.get("status") != "TRADING":
            log.info(f"{symbol} piyasası kapalı, atlanıyor.")
            return None
        step = float([f["stepSize"] for f in info["filters"] if f["filterType"] == "LOT_SIZE"][0])
        qty  = amount_usdt / price
        qty  = round(qty - (qty % step), 8)
        binance.order_market_buy(symbol=symbol, quantity=qty)
        return qty
    except BinanceAPIException as e:
        log.error(f"Alım hatası {symbol}: {e}")
        return None

def execute_sell(symbol, qty, reason=""):
    if config.TEST_MODE:
        return True
    try:
        info = binance.get_symbol_info(symbol)
        step = float([f["stepSize"] for f in info["filters"] if f["filterType"] == "LOT_SIZE"][0])
        qty  = round(qty - (qty % step), 8)
        binance.order_market_sell(symbol=symbol, quantity=qty)
        return True
    except BinanceAPIException as e:
        log.error(f"Satım hatası {symbol}: {e}")
        return False


# ============================================================
#  TELEGRAM
# ============================================================

async def notify(bot, text):
    ids = [config.TELEGRAM_CHAT_ID]
    if config.TELEGRAM_CHAT_ID_2:
        ids.append(config.TELEGRAM_CHAT_ID_2)
    for chat_id in ids:
        try:
            await bot.send_message(
                chat_id=chat_id,
                text=text,
                parse_mode="Markdown"
            )
        except Exception as e:
            log.error(f"Bildirim hatası {chat_id}: {e}")


# ============================================================
#  TARAMA & İŞLEM DÖNGÜSÜ
# ============================================================

def get_tradeable_symbols():
    try:
        tickers = binance.get_ticker()
        syms = [t["symbol"] for t in tickers
                if t["symbol"].endswith("USDT")
                and float(t.get("quoteVolume", 0)) >= config.MIN_24H_VOLUME_USDT]
        log.info(f"Taranacak coin: {len(syms)}")
        return syms
    except Exception as e:
        log.error(f"Sembol listesi hatası: {e}")
        return []

async def scan_and_trade(bot):
    global current_balance_usdt, total_profit

    balance = get_real_balance()
    current_balance_usdt = balance
    log.info(f"Bakiye: {balance} USDT")
    await notify(bot, f"🔍 Bakiye kontrol: `{balance:.4f} USDT`")

    if balance < 1.0:
        await notify(bot, "⚠️ *Bakiye yetersiz!* USDT bakiyeniz 1$'ın altında.")
        return

    symbols  = get_tradeable_symbols()
    signals  = []

    for sym in symbols:
        if sym in open_positions:
            continue
        if sym in {"TVKUSDT", "POLYUSDT", "BTCSTUSDT", "LENDUSDT"}:
            continue
        sig = analyze_coin(sym)
        if sig:
            signals.append(sig)
        await asyncio.sleep(0.08)

    signals.sort(key=lambda x: x["score"], reverse=True)
    log.info(f"Tarama bitti — {len(signals)} sinyal")

    # En iyi sinyalleri al (maks 2 yeni pozisyon per tur)
    bought = 0
    for sig in signals[:2]:
        sym   = sig["symbol"]
        coin  = sym.replace("USDT", "")
        price = sig["price"]

        amount = calc_position_size(balance)
        log.info(f"{coin} için pozisyon boyutu: {amount} USDT")
        if amount < 1.0:
            log.info(f"{coin} atlandı: miktar çok düşük ({amount})")
            continue

        qty = execute_buy(sym, price, amount)
        if not qty:
            continue

        stop = price * (1 - config.STOP_LOSS_PCT / 100)
        tp   = price * (1 + config.TAKE_PROFIT_PCT / 100)

        open_positions[sym] = {
            "buy_price":   price,
            "qty":         qty,
            "amount_usdt": amount,
            "stop_loss":   stop,
            "take_profit": tp,
            "time":        time.time(),
            "reason":      sig["reason"],
            "rsi":         sig["rsi"],
            "vol_ratio":   sig["vol_ratio"],
            "score":       sig["score"],
        }
        last_signal_time[sym] = time.time()
        balance -= amount
        bought += 1
        save_positions()

        mode = "⚠️ TEST" if config.TEST_MODE else "✅ GERÇEK"
        await notify(bot,
            f"🛒 *{coin} ALINDI* [{mode}]\n\n"
            f"💰 Alış fiyatı: `{fmt(price)}`\n"
            f"💵 Harcanan: `{fmt_try(amount)}`\n"
            f"📊 RSI: `{sig['rsi']}` | Hacim x{sig['vol_ratio']}\n"
            f"⚡ Güç skoru: `{sig['score']}/100`\n"
            f"📋 Sebepler: _{sig['reason']}_\n\n"
            f"🛡 Stop-Loss: `{fmt(stop)}`\n"
            f"🎯 Hedef: `{fmt(tp)}`\n"
            f"💼 Kalan bakiye: `{fmt_try(balance)}`"
        )

    if bought == 0:
        log.info("Bu turda alım yapılmadı.")

async def monitor_positions(bot):
    global current_balance_usdt, total_profit

    for sym, pos in list(open_positions.items()):
        try:
            coin    = sym.replace("USDT", "")
            ticker  = binance.get_symbol_ticker(symbol=sym)
            current = float(ticker["price"])
            buy     = pos["buy_price"]
            change  = ((current - buy) / buy) * 100

            reason = None
            if current <= pos["stop_loss"]:
                reason = "stop_loss"
            elif current >= pos["take_profit"]:
                reason = "take_profit"

            if not reason:
                continue

            ok = execute_sell(sym, pos["qty"], reason)
            if not ok:
                continue

            sell_val = pos["qty"] * current
            profit   = sell_val - pos["amount_usdt"]
            total_profit += profit
            current_balance_usdt += sell_val

            del open_positions[sym]
            save_positions()

            if reason == "stop_loss":
                emoji = "🔴"
                baslik = "STOP-LOSS — ZARAR KESİLDİ"
            else:
                emoji = "🟢"
                baslik = "KAR ALINDI"

            mode = "⚠️ TEST" if config.TEST_MODE else "✅ GERÇEK"
            await notify(bot,
                f"{emoji} *{coin} SATILDI — {baslik}* [{mode}]\n\n"
                f"📥 Alış: `{fmt(buy)}`\n"
                f"📤 Satış: `{fmt(current)}`\n"
                f"📈 Değişim: `%{change:+.2f}`\n"
                f"{'💰' if profit >= 0 else '💸'} {'Kâr' if profit >= 0 else 'Zarar'}: "
                f"`{fmt_try(abs(profit))}`\n\n"
                f"💼 Güncel bakiye: `{fmt_try(current_balance_usdt)}`\n"
                f"📊 Toplam kâr/zarar: `{fmt_try(total_profit)}`"
            )

        except Exception as e:
            log.error(f"Pozisyon izleme hatası {sym}: {e}")


# ============================================================
#  KOMUTLAR
# ============================================================

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    bal = get_real_balance()
    await update.message.reply_text(
        "🤖 *Binance Trading Bot Aktif*\n\n"
        f"💼 Bakiye: `{fmt_try(bal)}`\n"
        f"⚙️ Tarama: her {config.SCAN_INTERVAL//60} dakika\n"
        f"🛡 Stop-Loss: %{config.STOP_LOSS_PCT}\n"
        f"🎯 Kar Al: %{config.TAKE_PROFIT_PCT}\n"
        f"{'⚠️ TEST MODU' if config.TEST_MODE else '⚡ GERÇEK MOD'}\n\n"
        "/durum — açık pozisyonlar\n"
        "/bakiye — anlık bakiye\n"
        "/tara — hemen tara\n"
        "/iptal — tüm pozisyonları kapat",
        parse_mode="Markdown"
    )

async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not open_positions:
        await update.message.reply_text("📭 Açık pozisyon yok.")
        return
    lines = [f"📊 *Açık Pozisyonlar* ({len(open_positions)})\n"]
    for sym, pos in open_positions.items():
        coin = sym.replace("USDT", "")
        try:
            cur = float(binance.get_symbol_ticker(symbol=sym)["price"])
            chg = ((cur - pos["buy_price"]) / pos["buy_price"]) * 100
            icon = "🟢" if chg >= 0 else "🔴"
            lines.append(f"{icon} *{coin}*: {fmt(cur)} ({chg:+.1f}%)")
        except:
            lines.append(f"• *{coin}*: fiyat alınamadı")
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")

async def cmd_balance(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        balances = binance.get_account()
        lines = ["💼 *Bakiye*\n"]
        for b in balances["balances"]:
            free = float(b["free"])
            if free > 0:
                lines.append(f"`{b['asset']}`: {free}")
        if len(lines) == 1:
            lines.append("Hiç bakiye bulunamadı.")
        await update.message.reply_text("\n".join(lines), parse_mode="Markdown")
    except Exception as e:
        await update.message.reply_text(f"Hata: {e}")

async def cmd_scan_now(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🔍 Tarama başlıyor...")
    await scan_and_trade(context.bot)

async def cmd_close_all(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not open_positions:
        await update.message.reply_text("Açık pozisyon yok.")
        return
    for sym, pos in list(open_positions.items()):
        execute_sell(sym, pos["qty"], "Manuel")
        del open_positions[sym]
    await update.message.reply_text("✅ Tüm pozisyonlar kapatıldı.")


# ============================================================
#  ANA DÖNGÜ
# ============================================================

async def main_loop(bot):
    load_positions()
    bal = get_real_balance()
    await notify(bot,
        f"🚀 *Bot başladı!*\n"
        f"💼 Başlangıç bakiyesi: `{fmt_try(bal)}`\n"
        f"{'⚠️ TEST MODU' if config.TEST_MODE else '⚡ GERÇEK MOD'}\n"
        f"Her {config.SCAN_INTERVAL//60} dakikada Binance taranıyor."
    )
    while True:
        await scan_and_trade(bot)
        for _ in range(config.SCAN_INTERVAL // 60):
            await monitor_positions(bot)
            await asyncio.sleep(60)

async def run():
    app = Application.builder().token(config.TELEGRAM_TOKEN).build()
    app.add_handler(CommandHandler("start",  cmd_start))
    app.add_handler(CommandHandler("durum",  cmd_status))
    app.add_handler(CommandHandler("bakiye", cmd_balance))
    app.add_handler(CommandHandler("tara",   cmd_scan_now))
    app.add_handler(CommandHandler("iptal",  cmd_close_all))

    async with app:
        await app.start()
        await app.updater.start_polling()
        log.info("Bot çalışıyor...")
        await main_loop(app.bot)

if __name__ == "__main__":
    asyncio.run(run())
