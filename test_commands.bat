@echo off
REM Test commands for /api/qa/ask-share debugging (Windows)
REM Generated: 2025-11-11

set BASE_URL=https://gpt-vis.onrender.com
set SHARE_TOKEN=Q4ZIHIb9OYtPuEbm1mP2mQ

echo ================================
echo 1. Ping endpoint (GET)
echo ================================
curl -i "%BASE_URL%/api/qa/ask-share/ping"
echo.

echo ================================
echo 2. Real POST to ask-share
echo ================================
curl -i "%BASE_URL%/api/qa/ask-share" ^
  -H "Content-Type: application/json" ^
  -d "{\"lang\":\"en\",\"question\":\"What is covered?\",\"share_token\":\"%SHARE_TOKEN%\"}"
echo.

echo ================================
echo 3. Check chunks availability
echo ================================
curl -sS "%BASE_URL%/api/qa/chunks-report?share_token=%SHARE_TOKEN%"
echo.

echo ================================
echo 4. Verify share payload
echo ================================
curl -sS "%BASE_URL%/shares/%SHARE_TOKEN%"
echo.

echo ================================
echo TEST COMPLETED
echo ================================
echo.
echo If chunks = 0:
echo 1. Get batch_token from share payload above
echo 2. Find file IDs in database
echo 3. Re-embed: curl -X POST "%BASE_URL%/api/qa/reembed-file?file_id=FILE_ID" -H "X-User-Role: admin"
pause

