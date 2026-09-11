# AGENTS.md — do_chu_dong_api

## What this is

VNPT Hà Nội OLT monitoring system. Continuously scans ~170 OLT devices via CTS web portal, detects subscriber outages, and sends alerts via Telegram + Zalo (OpenZCA CLI).

## Entrypoints

- `app.py` — main daemon loop: login → infinite measurement cycles → alert post-processing
- `scripts/scan_port_status.py` — one-shot L2 port status scan (no measurement)
- `scripts/score_off_subscribers.py` — batch re-score offline subscriber patterns

## Commands

```bash
pytest -q              # run all tests (no pytest.ini — just -q)
python app.py          # start daemon (needs .env and browser login)
```

## Key architecture

- **State machine** (`alert_engine.py`): WATCHING → STABLE_ON/PENDING_OFF → ALERT_OFF/RECOVERED, stored in `subscriber_status_state` table
- **Alert pipeline**: measurement → state machine → pattern scoring → current_off snapshot → dispatch (Telegram + Zalo groups/individuals)
- **Database separation**: `onu_measurements.db` (measurements + alerts), `database.db` (authoritative subscriber directory / danhba)
- **Notification dispatch** (`notification_bridge.py`): wide-area, group outage, individual outage (per-NVKT), weak signal, recovery — each with independent timing config

## Critical gotchas

- **Browser login required**: `app.py` opens Playwright Chromium to login to CTS at `https://cts.vnpt.vn`. Needs OTP from file at `OTP_FILE_PATH` (default `/home/vtst/otp/otp_logs.txt`). Falls back to 20s manual wait. Shared session cookies via `global_cookies`/`global_headers`.
- **Dual import pattern**: Every module uses `try: from .x import ... except ImportError: from x import ...` to support both `python -m` and `python module.py` invocation.
- **SQLite locks**: `app.py` uses `db_lock` (threading.Lock) to serialize writes and `login_lock` for session refresh.
- **openzca**: Zalo messages sent via Node CLI tool at `/home/vtst/.nvm/versions/node/v22.22.2/bin/openzca`. Needs authenticated profile (`TTVTST` via `OPENZCA_PROFILE` env var). Send via `individual_zalo_mapping.json` (NVKT → Zalo user ID) and `doi_vt_mapping.py` (team → Zalo group thread ID).
- **`.env` controls noise**: Separate enable/disable, time windows, batch intervals for group vs individual vs wide-area vs recovery alerts. `WIDE_AREA_ALERT_EXCLUDED_PORTS` skips specific OLT:Port combos.
- **Systemd unit**: `do-quang-chu-dong.service` runs `app.py` as `vtst` user, `Restart=always`.
- **No package manager** — no `requirements.txt` / `pyproject.toml`. Dependencies are system-installed (python3, playwright, pandas, requests, openpyxl, python-dotenv).
- **Tests create real SQLite files** in `tmp_path` fixtures. They seed both `onu_measurements.db` and `database.db` (danhba table).
- **Python 3.10+** (cpython-310 bytecode in `__pycache__`).
