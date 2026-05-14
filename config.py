import os

# Telegram
TELEGRAM_TOKEN  = "8668134981:AAFI6iZCi4rMSb8sqSKE2QGg7lIclgR_61w"   # @BotFather'dan
TELEGRAM_CHAT_ID = "7638106132"         # Senin chat ID'n

# Binance API (binance.com → API Management)
BINANCE_API_KEY    = "xGgMLBExH2sGJ4R9vJqRAloqHDN7zGmoplbtzn44qsfdCO8yccmjzaZtaDADrEh5"
BINANCE_API_SECRET = "wfRY8WDuwefk36RWQovvwNECstw7VywWS18sZb883vWvMqG5IRwcC7L217W5Dbui"

# Başlangıç bakiyesi (USDT)
STARTING_BALANCE_USDT = 13.0   # ~500 TL

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
