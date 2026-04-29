@echo off
:: รันด้วยสิทธิ์ Administrator

:: สร้างโฟลเดอร์ log
if not exist "D:\hospital-foodss\logs" mkdir "D:\hospital-foodss\logs"

:: ลบ task เก่า (ถ้ามี)
schtasks /delete /tn "HospitalAPI" /f >nul 2>&1

:: สร้าง Task ใหม่ — รันตอน Windows เปิด, ไม่ต้อง login, รันตลอด
schtasks /create ^
  /tn "HospitalAPI" ^
  /tr "D:\hospital-foodss\run_api.bat" ^
  /sc ONSTART ^
  /ru SYSTEM ^
  /rl HIGHEST ^
  /f

echo.
echo ====================================
echo  ติดตั้ง Task Scheduler สำเร็จ
echo ====================================
echo.

:: รันทันทีเลย
schtasks /run /tn "HospitalAPI"
echo กำลังรัน API...
timeout /t 3 >nul

:: ตรวจสอบ
curl -s http://localhost:4000/api/hosxp/ping
echo.
echo ถ้าเห็น ok:true แสดงว่า API รันสำเร็จ
pause
