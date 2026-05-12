"""
Binance Trading Bot — Telegram onaylı, RSI + hacim analizi
"""

import asyncio
import logging
import time
from datetime import datetime
from collections import defaultdict

import requests
import pandas as pd
import numpy as np
from binance.client import Client
from binance.exceptions import BinanceAPIException
from telegram import Bot, InlineKeyboardMarkup, InlineKeyboardButton, Update
from telegram.ext import Application, CallbackQueryHandler, CommandHandler, ContextTypes

import config

# ============================================================
logging.basicConfig(
    format="%(asctime)s — %(levelname)s — %(message)s",
    level=logging.INFO,
    handlers=[
        logging.FileHandler("bot.log"),
        logging.StreamHandler()
    ]
)
log = logging.getLogger(__name__)
# ============================================================

binance = Client(config.BINANCE_API_KEY, config.BINANCE_API_SECRET)

# Açık pozisyonlar: {symbol: {buy_price, amount, stop_loss, take_profit, msg_id}}
open_positions = {}

# Bekleyen onaylar: {callback_data: {symbol, price, signal_data}}
pending_approvals = {}

# Son sinyal zamanları (aynı coin için spam önleme)
last_signal_time = defaultdict(float)
SIGNAL_COOLDOWN = 3600  # Aynı coin için 1 saat bekleme


# ============================================================
#  TEKNİK ANALİZ
# ============================================================

def calculate_rsi(prices: list, period: int = 14) -> float:
    """RSI hesapla"""
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


def calculate_ema(prices: list, period: int) -> float:
    """EMA hesapla"""
    if len(prices) < period:
        return prices[-1]
    k = 2 / (period + 1)
    ema = prices[0]
    for p in prices[1:]:
        ema = p * k + ema * (1 - k)
    return ema


def analyze_coin(symbol: str) -> dict | None:
    """
    Bir coin için sinyal analizi yap.
    Sinyal varsa dict döner, yoksa None döner.
    """
    try:
        # Son 100 mum (15 dakikalık)
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

        current_price  = closes[-1]
        current_volume = volumes[-1]

        # Hacim analizi — son 20 mum ortalamasına kıyasla
        avg_volume = np.mean(volumes[-21:-1])
        volume_ratio = current_volume / avg_volume if avg_volume > 0 else 0

        # RSI
        rsi = calculate_rsi(closes)

        # EMA 9 ve EMA 21 (trend yönü)
        ema9  = calculate_ema(closes, 9)
        ema21 = calculate_ema(closes, 21)

        # ATR (volatilite ölçümü)
        tr_list = []
        for i in range(1, len(closes)):
            tr = max(
                highs[i] - lows[i],
                abs(highs[i] - closes[i-1]),
                abs(lows[i] - closes[i-1])
            )
            tr_list.append(tr)
        atr = np.mean(tr_list[-14:]) if tr_list else 0
        atr_pct = (atr / current_price) * 100

        # ============================================================
        #  SİNYAL KURALLARI
        #  Tüm koşullar sağlanmalı
        # ============================================================
        buy_conditions = {
            "RSI aşırı satım bölgesi":  rsi < config.RSI_BUY_THRESHOLD,
            "Hacim patlaması":           volume_ratio >= config.MIN_VOLUME_SPIKE,
            "EMA trend yukarı":          ema9 > ema21,
            "Volatilite makul":          0.3 < atr_pct < 8.0,
        }

        passed = sum(buy_conditions.values())
        total  = len(buy_conditions)

        # En az 3/4 koşul sağlanmalı
        if passed < 3:
            return None

        # Cooldown kontrolü
        now = time.time()
        if now - last_signal_time[symbol] < SIGNAL_COOLDOWN:
            return None

        # Güç skoru (0-100)
        score = int((passed / total) * 100)
        # RSI ne kadar düşükse sinyal o kadar güçlü
        rsi_bonus = max(0, (config.RSI_BUY_THRESHOLD - rsi) * 2)
        # Hacim ne kadar yüksekse bonus
        vol_bonus = min(20, (volume_ratio - config.MIN_VOLUME_SPIKE) * 5)
        score = min(100, score + rsi_bonus + vol_bonus)

        return {
            "symbol":       symbol,
            "price":        current_price,
            "rsi":          rsi,
            "volume_ratio": round(volume_ratio, 2),
            "ema9":         round(ema9, 6),
            "ema21":        round(ema21, 6),
            "atr_pct":      round(atr_pct, 2),
            "score":        round(score),
            "conditions":   buy_conditions,
            "passed":       passed,
            "total":        total,
        }

    except Exception as e:
        log.debug(f"{symbol} analiz hatası: {e}")
        return None


# ============================================================
#  BİNANCE TARAMA
# ============================================================

def get_tradeable_symbols() -> list[str]:
    """Minimum hacmi geçen tüm USDT çiftlerini getir"""
    try:
        tickers = binance.get_ticker()
        symbols = []
        for t in tickers:
            sym = t["symbol"]
            if not sym.endswith("USDT"):
                continue
            vol = float(t.get("quoteVolume", 0))
            if vol >= config.MIN_24H_VOLUME_USDT:
                symbols.append(sym)
        log.info(f"Taranacak coin sayısı: {len(symbols)}")
        return symbols
    except Exception as e:
        log.error(f"Sembol listesi alınamadı: {e}")
        return []


# ============================================================
#  TELEGRAM BİLDİRİMLERİ
# ============================================================

def fmt_price(p: float) -> str:
    if p >= 1000:   return f"${p:,.2f}"
    if p >= 1:      return f"${p:.4f}"
    if p >= 0.001:  return f"${p:.6f}"
    return f"${p:.8f}"


async def send_signal(bot: Bot, signal: dict):
    """Telegram'a sinyal gönder ve onay bekle"""
    sym   = signal["symbol"]
    price = signal["price"]
    coin  = sym.replace("USDT", "")

    stop  = price * (1 - config.STOP_LOSS_PCT / 100)
    tp    = price * (1 + config.TAKE_PROFIT_PCT / 100)

    # Koşul satırları
    cond_lines = ""
    for name, ok in signal["conditions"].items():
        icon = "✅" if ok else "❌"
        cond_lines += f"  {icon} {name}\n"

    text = (
        f"🚨 *AL SİNYALİ — {coin}*\n\n"
        f"💰 Fiyat: `{fmt_price(price)}`\n"
        f"📊 RSI: `{signal['rsi']}`\n"
        f"📈 Hacim x{signal['volume_ratio']} (ortalamadan)\n"
        f"⚡ Güç skoru: `{signal['score']}/100`\n\n"
        f"*Analiz sonuçları ({signal['passed']}/{signal['total']}):\n*"
        f"{cond_lines}\n"
        f"🛡 Stop-Loss: `{fmt_price(stop)}` (-%{config.STOP_LOSS_PCT})\n"
        f"🎯 Kar Al: `{fmt_price(tp)}` (+%{config.TAKE_PROFIT_PCT})\n"
        f"💵 İşlem: `${config.TRADE_AMOUNT_USDT} USDT`\n\n"
        f"{'⚠️ TEST MODU — gerçek işlem yapılmaz' if config.TEST_MODE else '⚡ GERÇEK İŞLEM'}\n\n"
        f"_{datetime.now().strftime('%H:%M:%S')}_"
    )

    callback_approve = f"approve_{sym}_{int(time.time())}"
    callback_reject  = f"reject_{sym}_{int(time.time())}"

    keyboard = InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ ONAYLA", callback_data=callback_approve),
        InlineKeyboardButton("❌ REDDET", callback_data=callback_reject),
    ]])

    msg = await bot.send_message(
        chat_id=config.TELEGRAM_CHAT_ID,
        text=text,
        parse_mode="Markdown",
        reply_markup=keyboard
    )

    # Onay kaydı
    pending_approvals[callback_approve] = {
        "symbol":   sym,
        "price":    price,
        "signal":   signal,
        "stop":     stop,
        "tp":       tp,
        "msg_id":   msg.message_id,
        "time":     time.time(),
    }
    pending_approvals[callback_reject] = pending_approvals[callback_approve].copy()

    last_signal_time[sym] = time.time()
    log.info(f"Sinyal gönderildi: {sym} @ {fmt_price(price)}")


async def send_message(bot: Bot, text: str):
    """Basit mesaj gönder"""
    await bot.send_message(
        chat_id=config.TELEGRAM_CHAT_ID,
        text=text,
        parse_mode="Markdown"
    )


# ============================================================
#  İŞLEM YAPMA
# ============================================================

def execute_buy(symbol: str, price: float) -> dict | None:
    """Binance'de market alım emri ver"""
    if config.TEST_MODE:
        log.info(f"[TEST] Alım simüle edildi: {symbol} @ {fmt_price(price)}")
        qty = round(config.TRADE_AMOUNT_USDT / price, 6)
        return {"symbol": symbol, "qty": qty, "price": price}

    try:
        # Minimum lot size için sembol bilgisi al
        info = binance.get_symbol_info(symbol)
        step = float([f["stepSize"] for f in info["filters"] if f["filterType"] == "LOT_SIZE"][0])
        qty  = config.TRADE_AMOUNT_USDT / price
        qty  = round(qty - (qty % step), 8)

        order = binance.order_market_buy(symbol=symbol, quantity=qty)
        log.info(f"Alım emri verildi: {symbol} qty={qty}")
        return {"symbol": symbol, "qty": qty, "price": price, "order": order}
    except BinanceAPIException as e:
        log.error(f"Alım emri hatası {symbol}: {e}")
        return None


def execute_sell(symbol: str, qty: float, reason: str = "") -> bool:
    """Binance'de market satım emri ver"""
    if config.TEST_MODE:
        log.info(f"[TEST] Satım simüle edildi: {symbol} qty={qty} ({reason})")
        return True

    try:
        info = binance.get_symbol_info(symbol)
        step = float([f["stepSize"] for f in info["filters"] if f["filterType"] == "LOT_SIZE"][0])
        qty  = round(qty - (qty % step), 8)

        binance.order_market_sell(symbol=symbol, quantity=qty)
        log.info(f"Satım emri verildi: {symbol} qty={qty} ({reason})")
        return True
    except BinanceAPIException as e:
        log.error(f"Satım emri hatası {symbol}: {e}")
        return False


# ============================================================
#  POZİSYON TAKİBİ
# ============================================================

async def monitor_positions(bot: Bot):
    """Açık pozisyonları izle, stop-loss / take-profit kontrol et"""
    if not open_positions:
        return

    for sym, pos in list(open_positions.items()):
        try:
            ticker = binance.get_symbol_ticker(symbol=sym)
            current = float(ticker["price"])
            buy_price = pos["buy_price"]
            change_pct = ((current - buy_price) / buy_price) * 100

            coin = sym.replace("USDT", "")

            # Stop-loss
            if current <= pos["stop_loss"]:
                ok = execute_sell(sym, pos["qty"], "STOP-LOSS")
                if ok:
                    del open_positions[sym]
                    await send_message(bot,
                        f"🛑 *STOP-LOSS — {coin}*\n"
                        f"Giriş: `{fmt_price(buy_price)}`\n"
                        f"Çıkış: `{fmt_price(current)}`\n"
                        f"Zarar: `%{change_pct:.2f}`"
                    )

            # Take-profit
            elif current >= pos["take_profit"]:
                ok = execute_sell(sym, pos["qty"], "TAKE-PROFIT")
                if ok:
                    del open_positions[sym]
                    await send_message(bot,
                        f"🎯 *KAR ALINDI — {coin}*\n"
                        f"Giriş: `{fmt_price(buy_price)}`\n"
                        f"Çıkış: `{fmt_price(current)}`\n"
                        f"Kâr: `+%{change_pct:.2f}` 🎉"
                    )

        except Exception as e:
            log.error(f"Pozisyon izleme hatası {sym}: {e}")


# ============================================================
#  TELEGRAM HANDLER'LAR
# ============================================================

async def callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Onayla / Reddet butonlarını işle"""
    query  = update.callback_query
    data   = query.data
    bot    = context.bot

    await query.answer()

    if data not in pending_approvals:
        await query.edit_message_text("⏰ Bu sinyal süresi doldu.")
        return

    approval = pending_approvals.pop(data)
    sym      = approval["symbol"]
    coin     = sym.replace("USDT", "")

    # Timeout kontrolü
    if time.time() - approval["time"] > config.APPROVAL_TIMEOUT:
        await query.edit_message_text(f"⏰ {coin} sinyali zaman aşımına uğradı.")
        return

    if data.startswith("approve_"):
        # Karşılıklı reddet butonunu da temizle
        for k in list(pending_approvals.keys()):
            if pending_approvals[k].get("symbol") == sym:
                del pending_approvals[k]

        result = execute_buy(sym, approval["price"])
        if result:
            open_positions[sym] = {
                "buy_price":   approval["price"],
                "qty":         result["qty"],
                "stop_loss":   approval["stop"],
                "take_profit": approval["tp"],
                "time":        time.time(),
            }
            mode = "TEST MODU" if config.TEST_MODE else "GERÇEK"
            await query.edit_message_text(
                f"✅ *{coin} ALINDI [{mode}]*\n"
                f"Fiyat: `{fmt_price(approval['price'])}`\n"
                f"Stop: `{fmt_price(approval['stop'])}`\n"
                f"Hedef: `{fmt_price(approval['tp'])}`",
                parse_mode="Markdown"
            )
        else:
            await query.edit_message_text(f"❌ {coin} alım emri başarısız oldu.")

    elif data.startswith("reject_"):
        await query.edit_message_text(f"❌ *{coin} sinyali reddedildi.*", parse_mode="Markdown")
        log.info(f"Sinyal reddedildi: {sym}")


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🤖 *Binance Trading Bot Aktif*\n\n"
        f"⚙️ Tarama: {config.SCAN_INTERVAL//60} dakikada bir\n"
        f"💵 İşlem: ${config.TRADE_AMOUNT_USDT} USDT\n"
        f"🛡 Stop-Loss: %{config.STOP_LOSS_PCT}\n"
        f"🎯 Kar Al: %{config.TAKE_PROFIT_PCT}\n"
        f"{'⚠️ TEST MODU' if config.TEST_MODE else '⚡ GERÇEK MOD'}\n\n"
        "Komutlar:\n"
        "/durum — açık pozisyonlar\n"
        "/tara — hemen tara\n"
        "/iptal — tüm pozisyonları kapat",
        parse_mode="Markdown"
    )


async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not open_positions:
        await update.message.reply_text("📭 Açık pozisyon yok.")
        return

    lines = ["📊 *Açık Pozisyonlar*\n"]
    for sym, pos in open_positions.items():
        coin = sym.replace("USDT", "")
        ticker = binance.get_symbol_ticker(symbol=sym)
        current = float(ticker["price"])
        change = ((current - pos["buy_price"]) / pos["buy_price"]) * 100
        icon = "🟢" if change >= 0 else "🔴"
        lines.append(
            f"{icon} *{coin}*: {fmt_price(current)} "
            f"({change:+.2f}%)"
        )

    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


async def cmd_scan_now(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🔍 Tarama başlıyor...")
    await scan_loop_once(context.bot)


async def cmd_close_all(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not open_positions:
        await update.message.reply_text("Açık pozisyon yok.")
        return
    for sym, pos in list(open_positions.items()):
        execute_sell(sym, pos["qty"], "Manuel kapatma")
        del open_positions[sym]
    await update.message.reply_text("✅ Tüm pozisyonlar kapatıldı.")


# ============================================================
#  ANA TARAMA DÖNGÜSÜ
# ============================================================

async def scan_loop_once(bot: Bot):
    """Bir tarama turu yap"""
    log.info("Tarama başladı...")
    symbols   = get_tradeable_symbols()
    signals   = []
    scanned   = 0

    for sym in symbols:
        # Zaten açık pozisyon varsa atla
        if sym in open_positions:
            continue
        signal = analyze_coin(sym)
        if signal:
            signals.append(signal)
        scanned += 1
        # API rate limit için kısa bekleme
        await asyncio.sleep(0.1)

    log.info(f"Tarama bitti: {scanned} coin, {len(signals)} sinyal")

    # Sinyalleri güce göre sırala, en iyi 3'ünü gönder
    signals.sort(key=lambda x: x["score"], reverse=True)
    for sig in signals[:3]:
        await send_signal(bot, sig)

    if not signals:
        log.info("Bu turda sinyal bulunamadı.")


async def main_loop(bot: Bot):
    """Sürekli tarama ve pozisyon izleme döngüsü"""
    await send_message(bot,
        f"🚀 *Bot başladı!*\n"
        f"{'⚠️ TEST MODU' if config.TEST_MODE else '⚡ GERÇEK MOD'}\n"
        f"Her {config.SCAN_INTERVAL//60} dakikada tüm Binance taranıyor."
    )

    while True:
        await scan_loop_once(bot)
        # Pozisyon izleme (her dakika)
        for _ in range(config.SCAN_INTERVAL // 60):
            await monitor_positions(bot)
            await asyncio.sleep(60)


# ============================================================
#  UYGULAMA BAŞLATMA
# ============================================================

async def run():
    app = Application.builder().token(config.TELEGRAM_TOKEN).build()

    app.add_handler(CommandHandler("start",  cmd_start))
    app.add_handler(CommandHandler("durum",  cmd_status))
    app.add_handler(CommandHandler("tara",   cmd_scan_now))
    app.add_handler(CommandHandler("iptal",  cmd_close_all))
    app.add_handler(CallbackQueryHandler(callback_handler))

    async with app:
        await app.start()
        await app.updater.start_polling()
        log.info("Bot çalışıyor...")
        await main_loop(app.bot)


if __name__ == "__main__":
    asyncio.run(run())
