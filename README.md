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
- `INDIVIDUAL_OFF_ALERT_GATE_MODE=enforce`
- `INDIVIDUAL_OFF_MIN_DURATION_MINUTES=60`
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
`INDIVIDUAL_OFF_ALERT_GATE_MODE` mac dinh la `enforce`: ca ban tin OFF theo nhom va tin ca nhan chi nhan cac ma du bang chung su co. `shadow` chi ghi nhan quyet dinh nhung van gui nhu cu; `off` bo qua cong loc de rollback nhanh. `INDIVIDUAL_OFF_MIN_DURATION_MINUTES` mac dinh 60 phut. Canh bao dien rong va tin hieu yeu khong di qua cong loc nay.
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

## Canh bao khach hang & Precheck OneBSS

He thong ho tro gui tin Zalo thong bao giand doan den truc tiep khach hang thong qua Telecom Zalo Gateway, duoc bao ve boi co che precheck OneBSS hai luot (two-pass gate) va lease khoa nguyen tu tren SQLite:

- `ENABLE_CUSTOMER_OUTAGE_ALERT=True|False`: Bat/tat tinh nang gui tin cho khach hang (mac dinh `False`).
- `CUSTOMER_ALERT_TIME_WINDOW=07:00-21:00`: Khung gio cho phep gui tin (ngoai khung gio se vao `quiet_hours`).
- `CUSTOMER_ALERT_START_TIME=08:00`: Moc gio bat dau tinh thue bao OFF moi trong ngay cho lan thu dau tien.
- `CUSTOMER_ALERT_END_TIME=16:00`: Moc gio ket thuc tinh thue bao OFF trong ngay cho lan thu dau tien.
- `CUSTOMER_ALERT_HOTLINE=0822036382`: So hotline du phong khi khong tim thay so NVKT dia ban.
- `TELECOM_ZALO_API_URL=http://localhost:3002`: URL dich vu Telecom Zalo API.
- `TELECOM_ZALO_API_KEY`: API Key xac thuc voi Telecom Zalo Gateway.
- `CUSTOMER_ALERT_MAX_PER_DAY=1`: So tin nhan toi da gui cho 1 thue bao trong 24 gio.
- `CUSTOMER_ALERT_MAX_PER_WEEK=3`: So tin nhan toi da gui cho 1 thue bao trong 7 ngay.
- `ENABLE_CUSTOMER_TICKET_PRECHECK=True|False`: Bat/tat precheck phieu bao hong OneBSS (mac dinh `True`).
- `CUSTOMER_TICKET_PRECHECK_REQUEST_TIMEOUT_SECONDS=5.0`: Timeout cho moi request tra cuu OneBSS.
- `CUSTOMER_TICKET_PRECHECK_BATCH_TIMEOUT_SECONDS=30.0`: Timeout tong cho toan bo batch precheck.
- `CUSTOMER_TICKET_PRECHECK_MAX_WORKERS=4`: So luong worker dong thoi tra cuu OneBSS.
- `CUSTOMER_TICKET_PRECHECK_MAX_FACT_AGE_SECONDS=60.0`: Thoi gian song toi da cua ket qua precheck truoc khi gui tin (qua thoi gian nay se danh dau `STALE` va thu lai chu ky sau).
- `CUSTOMER_TICKET_PRECHECK_CLAIM_LEASE_SECONDS=120.0`: Thoi gian lease khoa ban ghi trong SQLite (yeu cau `>= batch_timeout + req_timeout + max_fact_age + gateway_timeout`).

### Quy trinh Bypass va Kich hoat lai (Authorized Bypass Procedure)

1. **Tam thoi bypass precheck:**
   - Truong hop OneBSS gap su co keo dai can tam thoi bo qua cong precheck de gui tin truc tiep theo luong cu, can bo van hanh ghi nhan ly do vao he thong quan ly thay doi/su co.
   - Dat bien `ENABLE_CUSTOMER_TICKET_PRECHECK=false` trong `.env` hoac moi truong.
   - Xac nhan nhat ky khoi dong va tong ket moi chu ky batch hien thi `precheck=BYPASSED` kem canh bao `[WARNING] ... precheck is BYPASSED`.
   - Cac ban ghi da o trang thai cuoi (`SENT`, `SKIPPED_CUSTOMER_TICKET`) van duoc giu nguyen tinh bat bien.
2. **Kich hoat lai (Re-enable):**
   - Sau khi OneBSS hoat dong binh thuong tro lai va kiem tra ket noi/test thanh cong, dat `ENABLE_CUSTOMER_TICKET_PRECHECK=true`.
   - Xac nhan chu ky tiep theo ghi nhan `precheck=ENFORCED` va cac chi so precheck hoat dong day du.
   - He thong tuyet doi khong tu dong bypass khi OneBSS loi (luon fail-closed de tranh spam khach hang).
