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

Co the bat/tat va gioi han khung gio gui tung loai ban tin bang `.env`:

- `ENABLE_GROUP_ALERT_NOTIFICATIONS=True|False`
- `GROUP_ALERT_TIME_WINDOW=06:00-21:00`
- `GROUP_ALERT_START_TIME=06:00`
- `GROUP_ALERT_SEND_EVERY_BATCHES=1`
- `ENABLE_INDIVIDUAL_ALERT_NOTIFICATIONS=True|False`
- `INDIVIDUAL_ALERT_TIME_WINDOW=06:00-21:00`
- `INDIVIDUAL_ALERT_START_TIME=06:00`
- `INDIVIDUAL_ALERT_SEND_EVERY_BATCHES=1`
- `INDIVIDUAL_ZALO_MAPPING_FILE=/duong/dan/individual_zalo_mapping.json`
- `ENABLE_WIDE_AREA_ALERT_NOTIFICATIONS=True|False`
- `WIDE_AREA_ALERT_TIME_WINDOW=00:00-23:59`
- `WIDE_AREA_ALERT_EXCLUDED_PORTS=OLT: STY.G22, Port: 0-1-13; OLT: STY.G23, Port: 0-1-14`
- `ENABLE_RECOVERY_ALERT_NOTIFICATIONS=True|False`
- `RECOVERY_ALERT_TIME_WINDOW=00:00-23:59`

Bo trong bien `*_TIME_WINDOW` neu muon gui ca ngay. Dinh dang khung gio la `HH:MM-HH:MM` va co ho tro ca khoang qua dem, vi du `22:00-06:00`.
Bo trong bien `*_ALERT_START_TIME` neu muon gui tat ca thue bao OFF hien co. `CURRENT_OFF_ALERT_START_TIME` chi con la bien tuong thich nguoc, duoc dung lam fallback khi chua cau hinh `GROUP_ALERT_START_TIME` hoac `INDIVIDUAL_ALERT_START_TIME`.
Bo bien `GROUP_ALERT_*` dieu khien rieng luong gui ban tin OFF vao cac nhom DOI_VT.
Bo bien `INDIVIDUAL_ALERT_*` dieu khien rieng luong gui Zalo truc tiep den tung NVKT. Hai luong co cong tac bat/tat, khung gio, moc tinh ma OFF moi, va chu ky batch doc lap.
Bien `*_ALERT_SEND_EVERY_BATCHES` quy dinh nhip gui theo chu ky toan cuc cua tung luong. Gia tri `1` giu hanh vi gui moi chu ky hop le; gia tri `x > 1` chi gui o moi chu ky thu `x`, `2x`, `3x`... Bo dem duoc luu trong SQLite rieng cho tung luong.
Bien `INDIVIDUAL_ZALO_MAPPING_FILE` tro den file JSON anh xa NVKT sang Zalo user id. Neu bo trong, chuong trinh tim file `individual_zalo_mapping.json` trong thu muc repo. Dinh dang:

```json
{
  "VNPT - Nguyen Van A": "zalo-user-id-a",
  "Nguyen Van B": "zalo-user-id-b"
}
```

Tin ca nhan chi gui cac ma OFF moi phat sinh trong ngay tinh tu `INDIVIDUAL_ALERT_START_TIME`; moi `subscriber_key` chi duoc thong bao mot lan trong ngay. Trang thai da gui luu trong SQLite `notification_runtime_state`, tach rieng voi luong gui nhom.
Bien `WIDE_AREA_ALERT_EXCLUDED_PORTS` dung de loai tru cac cong khong can gui ban tin dien rong. Moi muc co dang `OLT: <ten_olt>, Port: <port>` va phan cach nhau bang dau `;`. Ban ghi canh bao van duoc luu trong DB, chi bo qua khau gui thong bao.
