import psycopg2
import requests
import configparser

# ---------------------------
# อ่าน config
# ---------------------------
config = configparser.ConfigParser()
config.read("config.ini")

db = config["database"]
moph = config["moph"]

# ---------------------------
# เชื่อม DB
# ---------------------------
conn = psycopg2.connect(
    host=db["host"],
    port=db["port"],
    database=db["dbname"],
    user=db["user"],
    password=db["password"]
)

cur = conn.cursor()

# ---------------------------
# SQL (ปลอดภัย ไม่ใช้ GROUP BY)
# ---------------------------
sql = """
    SELECT
    w.name AS ward,
    r.name AS room,
    p.pname || LEFT(p.fname,4) || 'XX' || ' ' ||
    LEFT(p.lname,4) || 'XX' AS ptname,
    i.hn,
    i.an,
    ip.bedno AS bed,
    re.name AS religion,
    TO_CHAR(i.regdate + INTERVAL '543 years','DD/MM/YYYY') || ' ' || TO_CHAR(i.regtime,'HH24:MI:SS') AS admit,
    TO_CHAR(i.dchdate + INTERVAL '543 years','DD/MM/YYYY') || ' ' || TO_CHAR(i.dchtime,'HH24:MI:SS') AS discharge
    FROM ipt i
        LEFT JOIN iptadm ip ON ip.an = i.an
        LEFT JOIN patient p ON p.hn = i.hn
        LEFT JOIN ward w ON w.ward = i.ward
        LEFT JOIN roomno r ON r.roomno = ip.roomno
        LEFT JOIN religion re ON re.religion = p.religion
        LEFT JOIN pttype pt ON pt.pttype     = i.pttype
    WHERE w.ward_active = 'Y' AND i.pttype = 'J6'
         AND (r.name LIKE 'ห้องพิเศษ%' OR r.name LIKE '%พิเศษ%' )
    AND i.dchdate IS NULL
    ORDER BY i.regdate DESC
    LIMIT 200
"""

cur.execute(sql)
rows = cur.fetchall()

# ---------------------------
# สร้าง message
# ---------------------------
if not rows:
    message = "📋 วันนี้ไม่พบผู้ป่วยห้องพิเศษ"
else:
    message = f"📢 รายชื่อผู้ป่วยห้องพิเศษ จำนวน {len(rows)} คน\n\n"

    for i, r in enumerate(rows, 1):
        ward, room, ptname, hn, an, bed, religion, admit, discharge= r

        message += f"""\
{i}. 👤 {ptname}
🏥 HN: {hn}  AN: {an}
🛏 {ward} | {room} 
🕒 Admit: {admit} 
📝 ศาสนา: {religion or '-'}
---------------------
"""

# ---------------------------
# limit message
# ---------------------------
if len(message) > 3500:
    message = message[:3500] + "\n... (ตัดข้อความ)"

# ---------------------------
# send MOPH
# ---------------------------
url = "https://morpromt2f.moph.go.th/api/notify/send"

headers = {
    "Content-Type": "application/json",
    "client-key": moph["client_key"],
    "secret-key": moph["secret_key"]
}

payload = {
    "messages":[{"type":"text","text":message}]
}

r = requests.post(url, json=payload, headers=headers)

print("STATUS:", r.status_code)
print(r.text)

cur.close()
conn.close()