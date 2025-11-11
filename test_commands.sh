#!/bin/bash
# Test commands for /api/qa/ask-share debugging
# Generated: 2025-11-11

BASE_URL="https://gpt-vis.onrender.com"
SHARE_TOKEN="Q4ZIHIb9OYtPuEbm1mP2mQ"

echo "================================"
echo "1. Check OpenAPI docs for route"
echo "================================"
curl -sS "${BASE_URL}/docs" | grep -E "api.?/qa/ask-share" -i || echo "❌ Route not found in /docs"
echo ""

echo "================================"
echo "2. Ping endpoint (GET)"
echo "================================"
curl -i "${BASE_URL}/api/qa/ask-share/ping"
echo ""

echo "================================"
echo "3. Real POST to ask-share"
echo "================================"
curl -i "${BASE_URL}/api/qa/ask-share" \
  -H "Content-Type: application/json" \
  -d '{
    "lang": "en",
    "question": "What is covered?",
    "share_token": "'"${SHARE_TOKEN}"'"
  }'
echo ""

echo "================================"
echo "4. Check chunks availability"
echo "================================"
curl -sS "${BASE_URL}/api/qa/chunks-report?share_token=${SHARE_TOKEN}" | jq '{
  total_chunks: .total_chunks,
  org_id: .org_id,
  batch_token: .batch_token,
  sample_files: [.chunks[0:3][] | {file_id, filename, chunk_index}]
}'
echo ""

echo "================================"
echo "5. Verify share payload"
echo "================================"
curl -sS "${BASE_URL}/shares/${SHARE_TOKEN}" | jq '{
  batch_token: .payload.batch_token,
  document_ids: .payload.document_ids,
  company: .payload.company_name,
  org_id: .payload.org_id
}'
echo ""

echo "================================"
echo "NEXT STEPS IF CHUNKS = 0:"
echo "================================"
echo "# 1. Get batch_token from share above"
echo "# 2. Find file IDs:"
echo "#    SELECT id, filename FROM public.offer_files WHERE batch_id = (SELECT id FROM public.offer_batches WHERE token = '<batch_token>');"
echo "# 3. Re-embed files:"
echo "#    curl -X POST '${BASE_URL}/api/qa/reembed-file?file_id=<FILE_ID>' -H 'X-User-Role: admin'"
echo ""

echo "================================"
echo "SQL QUERIES (run in DB console)"
echo "================================"
cat <<'SQL'
-- Replace <batch_token> with actual token from share payload

-- 1. Check batch exists
SELECT id, token, title, status, created_at 
FROM public.offer_batches 
WHERE token = '<batch_token>';

-- 2. List files in batch
SELECT id, filename, storage_path, embeddings_ready, retrieval_file_id, insurer_code
FROM public.offer_files 
WHERE batch_id = (SELECT id FROM public.offer_batches WHERE token = '<batch_token>')
ORDER BY id;

-- 3. Count chunks per file
SELECT 
  of.id AS file_id, 
  of.filename, 
  COUNT(oc.id) AS chunk_count,
  of.embeddings_ready
FROM public.offer_files of
LEFT JOIN public.offer_chunks oc ON oc.file_id = of.id
WHERE of.batch_id = (SELECT id FROM public.offer_batches WHERE token = '<batch_token>')
GROUP BY of.id, of.filename, of.embeddings_ready
ORDER BY of.id;

-- 4. Total chunks for batch
SELECT COUNT(*) AS total_chunks
FROM public.offer_chunks 
WHERE file_id IN (
  SELECT id FROM public.offer_files 
  WHERE batch_id = (SELECT id FROM public.offer_batches WHERE token = '<batch_token>')
);

-- 5. Sample chunk content
SELECT oc.chunk_index, LEFT(oc.text, 100) AS text_preview, of.filename
FROM public.offer_chunks oc
JOIN public.offer_files of ON of.id = oc.file_id
WHERE of.batch_id = (SELECT id FROM public.offer_batches WHERE token = '<batch_token>')
ORDER BY of.id, oc.chunk_index
LIMIT 5;
SQL

echo ""
echo "================================"
echo "TEST COMPLETED"
echo "================================"

