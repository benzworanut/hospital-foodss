"""
admit_watcher.py — Real-time Admit Alert Daemon v1.0
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
รัน:  python admit_watcher.py
หรือ: systemctl start admit-watcher   (ดู admit-watcher.service)

ทำงาน:
  - ตรวจสอบผู้ป่วย Admit ใหม่ใน HosXP ทุก POLL_INTERVAL วินาที
  - ส่งแจ้งเตือน MOPH LINE Notify ทันทีเมื่อพบ Admit ใหม่
  - บันทึก AN ที่แจ้งแล้วใน seen_ans.json (ไม่แจ้งซ้ำ)
"""

import psycopg2
import requests
import configparser
import json
import logging
import time
import os
import signal
import sys
from datetime import datetime
from pathlib import Path

# ──────────────────────────────────────────
# CONFIG
# ──────────────────────────────────────────
POLL_INTERVAL   = 60          # วินาที — ตรวจทุก 1 นาที
SEEN_FILE       = Path("seen_ans.json")   # เก็บ AN ที่แจ้งแล้ว
LOG_FILE        = "admit_watcher.log"
MAX_SEEN        = 5000        # เก็บประวัติสูงสุด (กัน file บวม)

# ──────────────────────────────────────────
# LOGGING
# ──────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE, encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
)
log = logging.getLogger(__name__)

# ──────────────────────────────────────────
# CONFIG.INI  (ใช้ไฟล์เดียวกับ api_server.py)
# ──────────────────────────────────────────
config = configparser.ConfigParser()
config.read("config.ini")
db_cfg   = config["database"]
moph_cfg = config["moph"]

# ──────────────────────────────────────────
# SQL — ดึงผู้ป่วย Admit ปัจจุบัน (ยังไม่ D/C)
# ──────────────────────────────────────────
SQL_CURRENT_ADMITS = """
SELECT
    i.an,
    i.hn,
    w.name                                                      AS ward_name,
    r.name                                                      AS room_name,
    ip.bedno                                                    AS bed_no,
    p.pname || LEFT(p.fname,4) || 'XX' || ' ' ||
        LEFT(p.lname,4) || 'XX'                                AS ptname,
    re.name                                                     AS religion,
    TO_CHAR(i.regdate + INTERVAL '543 years','DD/MM/YYYY') ||
        ' ' || TO_CHAR(i.regtime,'HH24:MI:SS')                 AS admit_date
FROM ipt i
LEFT JOIN iptadm   ip ON ip.an       = i.an
LEFT JOIN patient   p ON p.hn        = i.hn
LEFT JOIN ward      w ON w.ward      = i.ward
LEFT JOIN roomno    r ON r.roomno    = ip.roomno
LEFT JOIN religion re ON re.religion = p.religion
WHERE w.ward_active = 'Y'
  AND i.pttype = 'J6'
  AND i.dchdate IS NULL
  AND (r.name LIKE 'ห้องพิเศษ%%' OR r.name LIKE '%%พิเศษ%%')
ORDER BY i.regdate DESC
LIMIT 200
"""

# ──────────────────────────────────────────
# DB HELPERS
# ──────────────────────────────────────────
def get_conn():
    return psycopg2.connect(
        host=db_cfg["host"],
        port=db_cfg["port"],
        database=db_cfg["dbname"],
        user=db_cfg["user"],
        password=db_cfg["password"],
        connect_timeout=10,
    )

def fetch_admits():
    """ดึงรายชื่อผู้ป่วย Admit ปัจจุบัน → list[dict]"""
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(SQL_CURRENT_ADMITS)
            cols = [d[0] for d in cur.description]
            return [dict(zip(cols, row)) for row in cur.fetchall()]
    finally:
        conn.close()

# ──────────────────────────────────────────
# SEEN-AN STATE
# ──────────────────────────────────────────
def load_seen() -> set:
    if SEEN_FILE.exists():
        try:
            data = json.loads(SEEN_FILE.read_text(encoding="utf-8"))
            return set(data)
        except Exception:
            pass
    return set()

def save_seen(seen: set):
    # ตัดให้เหลือไม่เกิน MAX_SEEN (เก็บท้ายสุด)
    lst = list(seen)
    if len(lst) > MAX_SEEN:
        lst = lst[-MAX_SEEN:]
    SEEN_FILE.write_text(
        json.dumps(lst, ensure_ascii=False, indent=2),
        encoding="utf-8"
    )

# ──────────────────────────────────────────
# MOPH NOTIFY
# ──────────────────────────────────────────
def send_moph(message: str) -> bool:
    url = "https://morpromt2f.moph.go.th/api/notify/send"
    headers = {
        "Content-Type": "application/json",
        "client-key":   moph_cfg["client_key"],
        "secret-key":   moph_cfg["secret_key"],
    }
    payload = {"messages": [{"type": "text", "text": message}]}
    try:
        r = requests.post(url, json=payload, headers=headers, timeout=10)
        if r.ok:
            log.info(f"MOPH notify OK — {r.status_code}")
            return True
        else:
            log.warning(f"MOPH notify FAILED — {r.status_code}: {r.text[:200]}")
            return False
    except Exception as e:
        log.error(f"MOPH notify ERROR: {e}")
        return False

def build_admit_message(pt: dict) -> str:
    sep = "─" * 22
    return (
        f"🏥 ผู้ป่วยรับเข้า (Admit)\n"
        f"{sep}\n"
        f"👤 {pt.get('ptname', '-')}\n"
        f"🪪  HN: {pt.get('hn', '-')}  |  AN: {pt.get('an', '-')}\n"
        f"🏢  {pt.get('ward_name', '-')}\n"
        f"🛏  ห้อง {pt.get('room_name', '-')}  เตียง {pt.get('bed_no', '-')}\n"
        f"📝  ศาสนา: {pt.get('religion', '-')}\n"
        f"🕒  Admit: {pt.get('admit_date', '-')}\n"
        f"{sep}"
    )

# ──────────────────────────────────────────
# MAIN LOOP
# ──────────────────────────────────────────
running = True

def handle_signal(sig, frame):
    global running
    log.info(f"รับสัญญาณ {sig} — กำลังหยุด...")
    running = False

signal.signal(signal.SIGTERM, handle_signal)
signal.signal(signal.SIGINT,  handle_signal)

def main():
    global running
    log.info("=" * 50)
    log.info("admit_watcher เริ่มทำงาน")
    log.info(f"Poll ทุก {POLL_INTERVAL} วินาที")
    log.info("=" * 50)

    seen = load_seen()
    log.info(f"โหลด seen_ans: {len(seen)} AN")

    while running:
        try:
            admits = fetch_admits()
            current_ans = {p["an"] for p in admits}
            new_ans = current_ans - seen

            if new_ans:
                log.info(f"พบ Admit ใหม่ {len(new_ans)} ราย: {new_ans}")
                new_patients = [p for p in admits if p["an"] in new_ans]
                for pt in new_patients:
                    msg = build_admit_message(pt)
                    ok  = send_moph(msg)
                    status = "✅ ส่งแล้ว" if ok else "❌ ส่งไม่ได้"
                    log.info(f"  AN {pt['an']} {pt.get('ptname','')} — {status}")
                    seen.add(pt["an"])
                save_seen(seen)
            else:
                log.debug(f"ไม่มี Admit ใหม่ (มีผู้ป่วย {len(admits)} ราย)")

        except psycopg2.Error as e:
            log.error(f"DB error: {e}")
        except Exception as e:
            log.error(f"Unexpected error: {e}", exc_info=True)

        # รอรอบถัดไป — ตรวจ running ทุก 1 วินาที
        for _ in range(POLL_INTERVAL):
            if not running:
                break
            time.sleep(1)

    log.info("admit_watcher หยุดทำงานแล้ว")

if __name__ == "__main__":
    main()
