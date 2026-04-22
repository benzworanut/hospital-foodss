"""
api_server.py — FastAPI Backend v2.1
รัน: uvicorn api_server:app --host 0.0.0.0 --port 4000 --reload
"""
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Optional
import psycopg2, psycopg2.extras, configparser, requests, logging
from datetime import datetime, date as date_type

config = configparser.ConfigParser()
config.read("config.ini")
DB = config["database"]
MOPH = config["moph"]

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
app = FastAPI(title="Hospital Food Order API", version="2.1.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

def get_conn():
    return psycopg2.connect(
        host=DB["host"], port=DB["port"], database=DB["dbname"],
        user=DB["user"], password=DB["password"], connect_timeout=10,
    )

def query(sql, params=None):
    conn = get_conn()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(sql, params)
            return cur.fetchall()
    finally:
        conn.close()

# ── SQL: Ward ทั้งหมด (ที่มีห้องพิเศษ) ──
SQL_WARDS = """
SELECT
    w.name AS ward_name,
    COUNT(r.roomno) AS total_rooms
FROM ward w
LEFT JOIN roomno r ON r.ward = w.ward
WHERE w.ward_active = 'Y'
   AND (r.name LIKE 'ห้องพิเศษ%' OR r.name LIKE '%พิเศษ%' )
GROUP BY w.ward, w.name
ORDER BY w.name
"""

# ── SQL: ห้องทั้งหมด (ไม่ว่าจะว่างหรือมีผู้ป่วย) ──
SQL_ROOMS = """
SELECT
    w.name  AS ward_name,
    r.name  AS room_no,
    r.name  AS room_name
FROM ward w
LEFT JOIN roomno r ON r.ward = w.ward
WHERE w.ward_active = 'Y'
   AND (r.name LIKE 'ห้องพิเศษ%' OR r.name LIKE '%พิเศษ%' )
ORDER BY w.name, r.name
"""

# ── SQL: ผู้ป่วย admit ปัจจุบัน ──
SQL_PATIENTS = """
SELECT 
    w.name                                          AS ward_name,
    r.name                                          AS room_no,
    r.name                                          AS room_name,
    p.pname || LEFT(p.fname,4) || 'XX' || ' ' ||LEFT(p.lname,4) || 'XX' AS ptname,
    i.hn, i.an,
    ip.bedno                                        AS bed_no,
    re.name                                         AS religion,
    TO_CHAR(i.regdate + INTERVAL '543 years','DD/MM/YYYY') || ' ' ||TO_CHAR(i.regtime,'HH24:MI:SS')  AS admit_date,
    NULL::text                                      AS dchdate
FROM ipt i
LEFT JOIN iptadm   ip ON ip.an       = i.an
LEFT JOIN patient   p ON p.hn        = i.hn
LEFT JOIN ward      w ON w.ward      = i.ward
LEFT JOIN roomno    r ON r.roomno    = ip.roomno
LEFT JOIN religion re ON re.religion = p.religion
LEFT JOIN pttype pt ON pt.pttype     = i.pttype
WHERE w.ward_active = 'Y' AND i.pttype = 'J6' AND i.an = '690004317'
   AND (r.name LIKE 'ห้องพิเศษ%' OR r.name LIKE '%พิเศษ%' )
   
ORDER BY i.regdate DESC
LIMIT 200
"""

# ── SQL: Discharge วันนี้ ──
SQL_DISCHARGE = """
SELECT
    w.name                                          AS ward_name,
    r.name                                          AS room_no,
    r.name                                          AS room_name,
    p.pname || LEFT(p.fname,4) || 'XX' || ' ' ||LEFT(p.lname,4) || 'XX' AS ptname,
    i.hn, i.an,
    ip.bedno                                        AS bed_no,
    re.name                                         AS religion,
    TO_CHAR(i.regdate + INTERVAL '543 years','DD/MM/YYYY') || ' ' ||TO_CHAR(i.regtime,'HH24:MI:SS')  AS admit_date,
    TO_CHAR(i.dchdate + INTERVAL '543 years','DD/MM/YYYY') || ' ' ||TO_CHAR(i.dchtime,'HH24:MI:SS') AS discharge
FROM ipt i
LEFT JOIN iptadm   ip ON ip.an       = i.an
LEFT JOIN patient   p ON p.hn        = i.hn
LEFT JOIN ward      w ON w.ward      = i.ward
LEFT JOIN roomno    r ON r.roomno    = ip.roomno
LEFT JOIN religion re ON re.religion = p.religion
LEFT JOIN pttype pt ON pt.pttype     = i.pttype
WHERE i.dchdate = CURRENT_DATE  AND (r.name LIKE 'ห้องพิเศษ%' OR r.name LIKE '%พิเศษ%' ) AND i.pttype = 'J6'
ORDER BY r.name
"""

# ── SQL: จำนวนห้องว่าง (ไม่มีผู้ป่วย admit อยู่) ──
SQL_EMPTY_ROOMS_COUNT = """
SELECT COUNT(*) AS total_empty_rooms
FROM (
    SELECT r.roomno
    FROM ward w
    LEFT JOIN roomno r ON r.ward = w.ward
    WHERE w.ward_active = 'Y'
      AND (r.name LIKE 'ห้องพิเศษ%' OR r.name LIKE '%พิเศษ%')
) allroom
LEFT JOIN (
    SELECT r.roomno
    FROM ipt i
    LEFT JOIN iptadm ip ON ip.an = i.an
    LEFT JOIN roomno r ON r.roomno = ip.roomno
    WHERE i.dchdate IS NULL
) usedroom ON allroom.roomno = usedroom.roomno
WHERE usedroom.roomno IS NULL
"""

# ── SQL: รายการห้องว่าง ──
SQL_EMPTY_ROOMS = """
SELECT
    allroom.ward_name,
    allroom.room_name,
    allroom.roomno
FROM (
    SELECT
        w.name AS ward_name,
        r.name AS room_name,
        r.roomno
    FROM ward w
    LEFT JOIN roomno r ON r.ward = w.ward
    WHERE w.ward_active = 'Y'
      AND (r.name LIKE 'ห้องพิเศษ%' OR r.name LIKE '%พิเศษ%')
) allroom
LEFT JOIN (
    SELECT r.roomno
    FROM ipt i
    LEFT JOIN iptadm ip ON ip.an = i.an
    LEFT JOIN roomno r ON r.roomno = ip.roomno
    WHERE i.dchdate IS NULL
) usedroom ON allroom.roomno = usedroom.roomno
WHERE usedroom.roomno IS NULL
ORDER BY allroom.roomno
"""

# ── SQL: จำนวนห้องพิเศษทั้งหมด ──
SQL_TOTAL_ROOMS = """
SELECT
    COUNT(r.roomno) AS total_rooms
FROM ward w
LEFT JOIN roomno r ON r.ward = w.ward
WHERE w.ward_active = 'Y'
   AND (r.name LIKE 'ห้องพิเศษ%' OR r.name LIKE '%พิเศษ%' )
"""

# ════════════════ ENDPOINTS ════════════════

@app.get("/api/hosxp/ping")
def ping():
    try:
        get_conn().close()
        return {"ok": True, "version": "PostgreSQL", "ts": datetime.now().isoformat()}
    except Exception as e:
        raise HTTPException(500, str(e))

@app.get("/api/hosxp/total-rooms")
def get_total_rooms():
    """จำนวนห้องพิเศษทั้งหมดใน DB"""
    try:
        rows = query(SQL_TOTAL_ROOMS)
        total = int(rows[0]["total_rooms"]) if rows else 0
        return {"success": True, "data": {"total_rooms": total}}
    except Exception as e:
        raise HTTPException(500, str(e))

@app.get("/api/hosxp/wards")
def get_wards():
    """Ward ทั้งหมดที่มีห้องพิเศษ พร้อมจำนวนห้อง"""
    try:
        return {"success": True, "data": query(SQL_WARDS)}
    except Exception as e:
        raise HTTPException(500, str(e))

@app.get("/api/hosxp/rooms")
def get_rooms():
    """ห้องทั้งหมด (รวมว่าง) สำหรับ QR grid"""
    try:
        return {"success": True, "data": query(SQL_ROOMS)}
    except Exception as e:
        raise HTTPException(500, str(e))

@app.get("/api/hosxp/empty-rooms-count")
def get_empty_rooms_count():
    """จำนวนห้องว่าง (ไม่มีผู้ป่วย admit อยู่)"""
    try:
        rows = query(SQL_EMPTY_ROOMS_COUNT)
        total = int(rows[0]["total_empty_rooms"]) if rows else 0
        return {"success": True, "data": {"total_empty_rooms": total}}
    except Exception as e:
        raise HTTPException(500, str(e))

@app.get("/api/hosxp/empty-rooms")
def get_empty_rooms():
    """รายการห้องว่างทั้งหมด"""
    try:
        return {"success": True, "data": query(SQL_EMPTY_ROOMS)}
    except Exception as e:
        raise HTTPException(500, str(e))

@app.get("/api/hosxp/admits")
def admits():
    try:
        return {"success": True, "data": query(SQL_PATIENTS)}
    except Exception as e:
        raise HTTPException(500, str(e))

@app.get("/api/hosxp/discharge-today")
def discharge():
    try:
        return {"success": True, "data": query(SQL_DISCHARGE)}
    except Exception as e:
        raise HTTPException(500, str(e))
    
@app.get("/api/hosxp/patient")
def get_patient(
    an:   Optional[str] = Query(None),
    hn:   Optional[str] = Query(None),
    room: Optional[str] = Query(None),
):
    if not an and not hn and not room:
        raise HTTPException(400, "ต้องระบุ an, hn หรือ room")
    conds = ["w.ward_active = 'Y'"]
    params = []
    if an:   conds.append("i.an = %s");   params.append(an)
    if hn:   conds.append("i.hn = %s");   params.append(hn)
    if room: conds.append("r.name = %s"); params.append(room)
    sql = f"""
        SELECT w.name AS ward_name, r.name AS room_no, r.name AS room_name,
               p.pname || LEFT(p.fname,4) || 'XX' || ' ' ||LEFT(p.lname,4) || 'XX' AS ptname,
               i.hn, i.an, ip.bedno AS bed_no, re.name AS religion,
               TO_CHAR(i.regdate + INTERVAL '543 years','DD/MM/YYYY') || ' ' ||TO_CHAR(i.regtime,'HH24:MI:SS')  AS admit_date,
               TO_CHAR(i.dchdate,'YYYY-MM-DD') AS dchdate
        FROM ipt i
        LEFT JOIN iptadm ip ON ip.an=i.an 
        LEFT JOIN patient p ON p.hn=i.hn
        LEFT JOIN ward w ON w.ward=i.ward 
        LEFT JOIN roomno r ON r.roomno=ip.roomno
        LEFT JOIN religion re ON re.religion=p.religion
        WHERE {' AND '.join(conds)} 
        ORDER BY i.regdate DESC 
        LIMIT 1
    """
    try:
        rows = query(sql, params)
        if not rows: raise HTTPException(404, "ไม่พบข้อมูลผู้ป่วย")
        return {"success": True, "data": dict(rows[0])}
    except HTTPException: raise
    except Exception as e: raise HTTPException(500, str(e))

class NotifyBody(BaseModel):
    message: str

@app.post("/api/moph/notify")
def moph_notify(body: NotifyBody):
    try:
        r = requests.post(
            "https://morpromt2f.moph.go.th/api/notify/send",
            json={"messages": [{"type":"text","text":body.message}]},
            headers={"Content-Type":"application/json",
                     "client-key": MOPH["client_key"],
                     "secret-key": MOPH["secret_key"]},
            timeout=10,
        )
        return {"ok": r.ok, "status": r.status_code}
    except Exception as e:
        raise HTTPException(500, str(e))

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("api_server:app", host="0.0.0.0", port=4000, reload=True)