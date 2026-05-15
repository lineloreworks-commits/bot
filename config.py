import os

# Telegram
TELEGRAM_TOKEN   = os.environ.get("TELEGRAM_TOKEN", "8668134981:AAFI6iZCi4rMSb8sqSKE2QGg7lIclgR_61w")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "7638106132")
TELEGRAM_CHAT_ID_2 = os.environ.get("TELEGRAM_CHAT_ID_2", "8775760035")  # Kız arkadaşının ID'si

# Binance
BINANCE_API_KEY    = os.environ.get("BINANCE_API_KEY", "mWTJHuD40rgOCX6LCaIWDp3SFxZxbrPG0gZnwxDeFqArw8HKOAFNPhJH8qUYVwPC")
BINANCE_API_SECRET = os.environ.get("BINANCE_API_SECRET", "zxhv2UzDdfdl0SSPFcAex3C29lpr9o5NzSMIEkxKGOMbJJvZRKrVF4DnhhITzaYj")

# Stop-loss / kar al
STOP_LOSS_PCT   = 3.0
TAKE_PROFIT_PCT = 6.0

# Tarama sıklığı (saniye)
SCAN_INTERVAL = 300  # 5 dakika

# Minimum günlük hacim
MIN_24H_VOLUME_USDT = 1_000_000

# RSI eşikleri
RSI_BUY_THRESHOLD  = 38
RSI_SELL_THRESHOLD = 70

# Hacim spike çarpanı
MIN_VOLUME_SPIKE = 2.0

# Test modu — False = gerçek işlem
TEST_MODE = False
