"""
notify_cron.py — ส่งแจ้งเตือน MOPH LINE ทุกวัน
ตั้ง cron: 0 7 * * * /usr/bin/python3 /path/to/notify_cron.py
"""

import psycopg2
import psycopg2.extras
import requests
import configparser
import logging
from datetime import datetime, date

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[
        logging.FileHandler("notify_cron.log", encoding="utf-8"),
        logging.StreamHandler(),
    ],
)
log = logging.getLogger(__name__)

# ─────────────────────────────────────────
config = configparser.ConfigParser()
config.read("config.ini")
DB   = config["database"]
MOPH = config["moph"]
# ─────────────────────────────────────────

def get_conn():
    return psycopg2.connect(
        host=DB["host"],
        port=int(DB.get("port", 5432)), 
        database=DB["dbname"],
        user=DB["user"],
        password=DB["password"],
    )

# ── ดึงผู้ป่วยที่สั่งอาหารวันนี้ ──
SQL_FOOD_ORDERS_TODAY = """
SELECT
  w.name                                                  AS ward,
  r.name                                                  AS room,
  p.pname || ' ' || LEFT(p.fname,4) || repeat('X',GREATEST(length(p.fname)-4,0)) AS ptname,
  i.hn,
  i.an,
  ip.bedno                                                AS bed,
  re.name                                                 AS religion,
  TO_CHAR(i.regdate + INTERVAL '543 years','DD/MM/YYYY') AS admit,
  STRING_AGG(DISTINCT fdd.nutrition_food_name, ', ')      AS foods,
  STRING_AGG(DISTINCT sd.nutrition_food_sub_day_name, ', ') AS meals,
  STRING_AGG(DISTINCT ft.nutrition_food_sub_type_name, ', ') AS diet_types,
  STRING_AGG(DISTINCT ordd.nutrition_food_ord_detail, ', ')  AS notes
FROM ipt i
  INNER JOIN nutrition_food_ord ord
          ON ord.an = i.an
         AND DATE(ord.order_date) = CURRENT_DATE
  INNER JOIN nutrition_food_ord_detail ordd
          ON ordd.nutrition_food_ord_id = ord.nutrition_food_ord_id
  LEFT JOIN nutrition_food_sub_type ft
         ON ft.nutrition_food_sub_type_id = ordd.nutrition_food_sub_type_id
  LEFT JOIN nutrition_food fdd
         ON fdd.nutrition_food_id = ordd.nutrition_food_id
  LEFT JOIN nutrition_food_day fd
         ON fd.nutrition_food_day_id = ordd.nutrition_food_day_id
  LEFT JOIN nutrition_food_sub_day sd
         ON sd.nutrition_food_sub_day_id = fd.nutrition_food_sub_day_id
  LEFT JOIN patient p   ON p.hn     = i.hn
  LEFT JOIN iptadm ip   ON ip.an    = i.an
  LEFT JOIN ward w      ON w.ward   = i.ward
  LEFT JOIN roomno r    ON r.roomno = ip.roomno
  LEFT JOIN religion re ON re.religion = p.religion
WHERE w.ward_active = 'Y'
  AND r.name LIKE 'ห้องพิเศษ%'
GROUP BY
  w.name, r.name, p.pname, p.fname, p.lname,
  i.hn, i.an, ip.bedno, re.name, i.regdate
ORDER BY w.name, r.name
"""

# ── ดึงผู้ป่วย Discharge วันนี้ ──
SQL_DISCHARGE_TODAY = """
SELECT
  w.name                                                  AS ward,
  r.name                                                  AS room,
  p.pname || ' ' || LEFT(p.fname,4) || repeat('X',GREATEST(length(p.fname)-4,0)) AS ptname,
  i.hn,
  i.an,
  ip.bedno                                                AS bed,
  TO_CHAR(i.regdate + INTERVAL '543 years','DD/MM/YYYY') AS admit,
  TO_CHAR(i.dchdate + INTERVAL '543 years','DD/MM/YYYY')
    || ' ' || TO_CHAR(i.dchtime,'HH24:MI:SS')             AS discharge
FROM ipt i
  LEFT JOIN iptadm ip ON ip.an    = i.an
  LEFT JOIN patient p ON p.hn     = i.hn
  LEFT JOIN ward w    ON w.ward   = i.ward
  LEFT JOIN roomno r  ON r.roomno = ip.roomno
WHERE w.ward_active = 'Y'
  AND r.name LIKE 'ห้องพิเศษ%'
  AND i.dchdate = CURRENT_DATE
ORDER BY i.dchtime
"""


def send_line(message: str) -> bool:
    url = "https://morpromt2f.moph.go.th/api/notify/send"
    headers = {
        "Content-Type": "application/json",
        "client-key": MOPH.get("client_key", ""),
        "secret-key":  MOPH.get("secret_key", ""),
    }
    payload = {"messages": [{"type": "text", "text": message}]}
    try:
        r = requests.post(url, json=payload, headers=headers, timeout=20)
        log.info("LINE Notify: HTTP %s — %s", r.status_code, r.text[:200])
        return r.ok
    except Exception as e:
        log.error("LINE Notify error: %s", e)
        return False


def run_food_orders():
    """แจ้งเตือนรายการสั่งอาหารประจำวัน (รันตอนเช้า)"""
    log.info("=== แจ้งเตือนรายการสั่งอาหารวันนี้ ===")
    conn = get_conn()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(SQL_FOOD_ORDERS_TODAY)
            rows = cur.fetchall()
    finally:
        conn.close()

    today_thai = datetime.now().strftime("%d/%m/") + str(datetime.now().year + 543)

    if not rows:
        msg = f"📋 รายการสั่งอาหาร {today_thai}\nวันนี้ยังไม่มีรายการสั่งอาหาร (ห้องพิเศษ)"
        send_line(msg)
        return

    sep = "─" * 24
    msg = f"🍱 รายการสั่งอาหาร {today_thai}\n(ห้องพิเศษ) รวม {len(rows)} ราย\n{sep}\n"

    for i, r in enumerate(rows, 1):
        ward     = r["ward"]     or "-"
        room     = r["room"]     or "-"
        ptname   = r["ptname"]   or "-"
        hn       = r["hn"]       or "-"
        an       = r["an"]       or "-"
        bed      = r["bed"]      or "-"
        religion = r["religion"] or "-"
        admit    = r["admit"]    or "-"
        foods    = r["foods"]    or "-"
        meals    = r["meals"]    or "-"
        diet     = r["diet_types"] or "-"
        notes    = r["notes"]    or "-"

        msg += (
            f"{i}. 🏥 {ward} | 🛏 {room} เตียง {bed}\n"
            f"   👤 {ptname}\n"
            f"   HN: {hn}  AN: {an}\n"
            f"   ศาสนา: {religion}  Admit: {admit}\n"
            f"   🍱 {foods}\n"
            f"   🕒 {meals}\n"
            f"   🥗 {diet}\n"
            f"   📝 {notes}\n"
            f"{sep}\n"
        )

    log.info("ส่งแจ้งเตือนสั่งอาหาร %d ราย", len(rows))
    send_line(msg)


def run_discharge():
    """แจ้งเตือนผู้ป่วย Discharge วันนี้ (รันตอนเช้า)"""
    log.info("=== แจ้งเตือน Discharge วันนี้ ===")
    conn = get_conn()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(SQL_DISCHARGE_TODAY)
            rows = cur.fetchall()
    finally:
        conn.close()

    today_thai = datetime.now().strftime("%d/%m/") + str(datetime.now().year + 543)

    if not rows:
        log.info("ไม่มีผู้ป่วย Discharge วันนี้")
        return

    sep = "─" * 24
    msg = (
        f"🔔 แจ้งเตือน Discharge {today_thai}\n"
        f"(ห้องพิเศษ) {len(rows)} ราย\n{sep}\n"
    )

    for i, r in enumerate(rows, 1):
        msg += (
            f"{i}. 🏥 {r['ward']} | 🛏 {r['room']}\n"
            f"   👤 {r['ptname']}\n"
            f"   HN: {r['hn']}  AN: {r['an']}\n"
            f"   📅 Admit: {r['admit']}\n"
            f"   🔴 D/C: {r['discharge']}\n"
            f"{sep}\n"
        )

    log.info("ส่งแจ้งเตือน Discharge %d ราย", len(rows))
    send_line(msg)


if __name__ == "__main__":
    import sys

    if len(sys.argv) > 1:
        mode = sys.argv[1]
        if mode == "food":
            run_food_orders()
        elif mode == "discharge":
            run_discharge()
        elif mode == "all":
            run_food_orders()
            run_discharge()
        else:
            print("Usage: python notify_cron.py [food|discharge|all]")
    else:
        # default: รันทั้งคู่
        run_food_orders()
        run_discharge()