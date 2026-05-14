# ============================================================
#  KONFİGÜRASYON — Sadece bu dosyayı düzenlemen yeterli
# ============================================================

# Telegram
TELEGRAM_TOKEN  = "8668134981:AAFI6iZCi4rMSb8sqSKE2QGg7lIclgR_61w"   # @BotFather'dan
TELEGRAM_CHAT_ID = "7638106132"         # Senin chat ID'n

# Binance API (binance.com → API Management)
BINANCE_API_KEY    = "xGgMLBExH2sGJ4R9vJqRAloqHDN7zGmoplbtzn44qsfdCO8yccmjzaZtaDADrEh5"
BINANCE_API_SECRET = "wfRY8WDuwefk36RWQovvwNECstw7VywWS18sZb883vWvMqG5IRwcC7L217W5Dbui"

# ============================================================
#  STRATEJİ AYARLARI
# ============================================================

# Her işlemde kullanılacak maksimum USDT miktarı
TRADE_AMOUNT_USDT = 20          # örn: 20 dolar per işlem

# Stop-loss yüzdesi (eksi)
STOP_LOSS_PCT = 3.0             # %3 düşünce otomatik sat

# Kar al yüzdesi
TAKE_PROFIT_PCT = 6.0           # %6 çıkınca otomatik sat

# Tarama sıklığı (saniye)
SCAN_INTERVAL = 300             # 5 dakikada bir tara

# Sinyal için minimum hacim artışı
MIN_VOLUME_SPIKE = 2.0          # Ortalamadan 2x fazla hacim

# RSI alt eşiği (al sinyali için)
RSI_BUY_THRESHOLD = 35          # RSI 35 altı = aşırı satım

# RSI üst eşiği (sat uyarısı için)
RSI_SELL_THRESHOLD = 70         # RSI 70 üstü = aşırı alım

# Taranacak minimum hacim (düşük hacimli coinleri atla)
MIN_24H_VOLUME_USDT = 1_000_000  # 1 milyon dolar günlük hacim

# Onay timeout (saniye) — bu kadar içinde onaylanmazsa iptal
APPROVAL_TIMEOUT = 120          # 2 dakika

# Test modu (True = gerçek işlem yapmaz, sadece bildirim gönderir)
TEST_MODE = True   # İLK BAŞTA TRUE BIRAK, test ettikten sonra False yap
