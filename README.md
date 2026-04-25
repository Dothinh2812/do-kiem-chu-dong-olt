# do-kiem-chu-dong-olt

Bo ma nguon theo doi va canh bao chu dong cho OLT, bao gom:

- xu ly batch do trang thai ONU
- phat hien mat lien lac dien rong va su co thue bao
- cau noi gui canh bao qua Telegram va Zalo
- bo test cho luong canh bao va delivery log

## Tep chinh

- `app.py`: luong thu thap va xu ly batch
- `alert_engine.py`: state machine phat hien su co
- `notification_service.py`: gui canh bao va ghi log
- `tests/`: test tu dong cho alert engine va notification

## Chay test

```bash
pytest -q
```

## Cau hinh

Su dung bien moi truong hoac `.env` cuc bo cho cac thong tin nhu `BAOCAO_USERNAME`, `BAOCAO_PASSWORD`, `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`.
