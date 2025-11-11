#!/bin/bash
# Debug specific share token: onnVqW0hmMemev9svjpCTA

SHARE_TOKEN="onnVqW0hmMemev9svjpCTA"
BASE_URL="https://gpt-vis.onrender.com"

echo "========================================"
echo "DEBUGGING SHARE: $SHARE_TOKEN"
echo "========================================"
echo ""

echo "1. GET SHARE PAYLOAD"
echo "-------------------"
SHARE_DATA=$(curl -sS "$BASE_URL/shares/$SHARE_TOKEN")
echo "$SHARE_DATA" | jq '{
  org_id: .org_id,
  batch_token: .payload.batch_token,
  document_ids: .payload.document_ids,
  mode: .payload.mode,
  company: .payload.company_name
}'

BATCH_TOKEN=$(echo "$SHARE_DATA" | jq -r '.payload.batch_token')
DOC_IDS=$(echo "$SHARE_DATA" | jq -r '.payload.document_ids')

echo ""
echo "Extracted:"
echo "  batch_token: $BATCH_TOKEN"
echo "  document_ids: $DOC_IDS"
echo ""

echo "2. CHECK CHUNKS REPORT"
echo "----------------------"
curl -sS "$BASE_URL/api/qa/chunks-report?share_token=$SHARE_TOKEN" \
  -H "X-User-Role: admin" \
  -H "X-Org-Id: 1" | jq '{
  total_chunks: .total_chunks,
  batch_token: .batch_token,
  org_id: .org_id,
  files: [.chunks[0:3][] | {file_id, filename}]
}'

echo ""
echo "3. RECENT UPLOADS (Should match)"
echo "---------------------------------"
echo "File 207: batch_token=bt_7705d6b1b25d4fa2922adcef, chunks=49, file=GJENSIDIGE-VA.pdf"
echo "File 208: batch_token=bt_a327fe36a8c14706a5ecd13e, chunks=9, file=IF_-_VA.pdf"
echo ""

if [ "$BATCH_TOKEN" = "null" ] || [ -z "$BATCH_TOKEN" ]; then
    echo "❌ PROBLEM: batch_token is NULL in share!"
    echo ""
    echo "This means filename inference failed."
    echo "Need to check if document_ids match uploaded filenames."
    echo ""
    echo "4. MANUAL BATCH TOKEN FIX"
    echo "-------------------------"
    echo "If the share should use file 207 (GJENSIDIGE):"
    echo "UPDATE share_links SET payload = jsonb_set(payload, '{batch_token}', '\"bt_7705d6b1b25d4fa2922adcef\"') WHERE token = '$SHARE_TOKEN';"
    echo ""
    echo "If the share should use file 208 (IF):"
    echo "UPDATE share_links SET payload = jsonb_set(payload, '{batch_token}', '\"bt_a327fe36a8c14706a5ecd13e\"') WHERE token = '$SHARE_TOKEN';"
    echo ""
    echo "Or recreate share with explicit batch_token:"
    echo "curl -X POST '$BASE_URL/shares' \\"
    echo "  -H 'Content-Type: application/json' \\"
    echo "  -H 'X-Org-Id: 1' -H 'X-User-Id: 1' \\"
    echo "  -d '{\"batch_token\": \"bt_7705d6b1b25d4fa2922adcef\", \"title\": \"Fixed Share\"}'"
elif [ "$BATCH_TOKEN" = "bt_7705d6b1b25d4fa2922adcef" ]; then
    echo "✅ batch_token matches file 207 (GJENSIDIGE-VA.pdf, 49 chunks)"
    echo "   Chunks should be available. Checking why query fails..."
elif [ "$BATCH_TOKEN" = "bt_a327fe36a8c14706a5ecd13e" ]; then
    echo "✅ batch_token matches file 208 (IF_-_VA.pdf, 9 chunks)"
    echo "   Chunks should be available. Checking why query fails..."
else
    echo "⚠️  batch_token = $BATCH_TOKEN"
    echo "   Does not match either recent upload!"
    echo "   This share might be pointing to a different batch."
fi

echo ""
echo "5. TEST ASK-SHARE ENDPOINT"
echo "--------------------------"
ASK_RESULT=$(curl -sS -X POST "$BASE_URL/api/qa/ask-share" \
  -H "Content-Type: application/json" \
  -d "{\"share_token\":\"$SHARE_TOKEN\",\"question\":\"test\",\"lang\":\"en\"}" 2>&1)

if echo "$ASK_RESULT" | jq -e '.answer' >/dev/null 2>&1; then
    echo "✅ ask-share WORKS!"
    echo "$ASK_RESULT" | jq '{answer: (.answer[:100] + "..."), sources}'
else
    echo "❌ ask-share FAILED"
    echo "$ASK_RESULT" | jq '.'
fi

echo ""
echo "========================================"
echo "NEXT STEPS"
echo "========================================"
echo "1. If batch_token is NULL → Update share manually (SQL above)"
echo "2. If batch_token doesn't match uploads → Share is for different files"
echo "3. If chunks_report shows 0 → Files weren't embedded (shouldn't happen)"
echo "4. If everything looks right but still fails → Check server logs"

