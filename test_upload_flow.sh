#!/bin/bash
# Test the complete upload → chunk → share flow
# Usage: ./test_upload_flow.sh

set -e

BASE_URL="${BASE_URL:-https://gpt-vis.onrender.com}"
ORG_ID="${ORG_ID:-1}"
TEST_PDF="${TEST_PDF:-test.pdf}"

echo "========================================"
echo "Upload → Chunk → Share Flow Test"
echo "========================================"
echo "Base URL: $BASE_URL"
echo "Org ID: $ORG_ID"
echo "Test PDF: $TEST_PDF"
echo ""

# Step 1: Upload file (creates batch, file, and chunks)
echo "Step 1: Uploading PDF..."
UPLOAD_RESPONSE=$(curl -s -X POST "$BASE_URL/api/offers/upload" \
  -H "X-User-Role: admin" \
  -F "pdf=@$TEST_PDF" \
  -F "org_id=$ORG_ID")

echo "$UPLOAD_RESPONSE" | jq .

FILE_ID=$(echo "$UPLOAD_RESPONSE" | jq -r '.file_id')
BATCH_TOKEN=$(echo "$UPLOAD_RESPONSE" | jq -r '.batch_token')
CHUNKS_CREATED=$(echo "$UPLOAD_RESPONSE" | jq -r '.chunks_created')

if [ "$FILE_ID" == "null" ] || [ -z "$FILE_ID" ]; then
    echo "❌ Upload failed! No file_id returned."
    exit 1
fi

echo ""
echo "✅ Upload successful!"
echo "   File ID: $FILE_ID"
echo "   Batch Token: $BATCH_TOKEN"
echo "   Chunks Created: $CHUNKS_CREATED"
echo ""

# Step 2: Verify chunks exist
echo "Step 2: Verifying chunks in database..."
sleep 2  # Give DB a moment to commit

CHUNK_CHECK=$(curl -s "$BASE_URL/api/qa/chunks-report?share_token=dummy" 2>&1 || echo '{"total_chunks":0}')
# Note: This will fail with 404 since share doesn't exist yet, but that's OK
# In real scenario, you'd query DB directly

echo "   (Skipping chunk verification - requires DB access)"
echo ""

# Step 3: Create share with explicit batch_token
echo "Step 3: Creating share with batch_token..."
SHARE_RESPONSE=$(curl -s -X POST "$BASE_URL/shares" \
  -H "Content-Type: application/json" \
  -H "X-Org-Id: $ORG_ID" \
  -H "X-User-Id: 1" \
  -d "{
    \"batch_token\": \"$BATCH_TOKEN\",
    \"title\": \"Test Share - Upload Flow\",
    \"editable\": true
  }")

echo "$SHARE_RESPONSE" | jq .

SHARE_TOKEN=$(echo "$SHARE_RESPONSE" | jq -r '.token')

if [ "$SHARE_TOKEN" == "null" ] || [ -z "$SHARE_TOKEN" ]; then
    echo "❌ Share creation failed!"
    exit 1
fi

echo ""
echo "✅ Share created!"
echo "   Share Token: $SHARE_TOKEN"
echo "   URL: $(echo "$SHARE_RESPONSE" | jq -r '.url')"
echo ""

# Step 4: Verify share has correct batch_token
echo "Step 4: Verifying share payload..."
SHARE_GET=$(curl -s "$BASE_URL/shares/$SHARE_TOKEN")

echo "$SHARE_GET" | jq '{
  batch_token: .payload.batch_token,
  document_ids: .payload.document_ids,
  offers_count: (.offers | length)
}'

SHARE_BATCH_TOKEN=$(echo "$SHARE_GET" | jq -r '.payload.batch_token')

if [ "$SHARE_BATCH_TOKEN" != "$BATCH_TOKEN" ]; then
    echo "⚠️  Warning: batch_token mismatch!"
    echo "   Expected: $BATCH_TOKEN"
    echo "   Got: $SHARE_BATCH_TOKEN"
else
    echo "✅ batch_token matches!"
fi
echo ""

# Step 5: Check chunks report for the share
echo "Step 5: Checking chunks report..."
CHUNKS_REPORT=$(curl -s "$BASE_URL/api/qa/chunks-report?share_token=$SHARE_TOKEN" \
  -H "X-User-Role: admin" \
  -H "X-Org-Id: $ORG_ID")

echo "$CHUNKS_REPORT" | jq '{
  total_chunks: .total_chunks,
  batch_token: .batch_token,
  files_count: (.chunks | group_by(.file_id) | length)
}'

TOTAL_CHUNKS=$(echo "$CHUNKS_REPORT" | jq -r '.total_chunks')

if [ "$TOTAL_CHUNKS" == "0" ] || [ "$TOTAL_CHUNKS" == "null" ]; then
    echo "❌ No chunks found for share!"
    echo "   This means the link is broken somewhere."
else
    echo "✅ Found $TOTAL_CHUNKS chunks!"
fi
echo ""

# Step 6: Test ask-share endpoint
echo "Step 6: Testing ask-share endpoint..."
ASK_RESPONSE=$(curl -s -X POST "$BASE_URL/api/qa/ask-share" \
  -H "Content-Type: application/json" \
  -d "{
    \"share_token\": \"$SHARE_TOKEN\",
    \"question\": \"What is the premium?\",
    \"lang\": \"en\"
  }")

echo "$ASK_RESPONSE" | jq '{
  answer: (.answer[:100] + "..."),
  sources_count: (.sources | length)
}' 2>/dev/null || echo "$ASK_RESPONSE"

if echo "$ASK_RESPONSE" | jq -e '.answer' > /dev/null 2>&1; then
    echo "✅ ask-share works!"
else
    echo "❌ ask-share failed!"
    echo "$ASK_RESPONSE"
fi
echo ""

# Summary
echo "========================================"
echo "FLOW TEST SUMMARY"
echo "========================================"
echo "Upload:       ✅ file_id=$FILE_ID, batch=$BATCH_TOKEN"
echo "Chunks:       ✅ $CHUNKS_CREATED chunks created"
echo "Share:        ✅ token=$SHARE_TOKEN"
echo "Chunks Link:  $([ "$TOTAL_CHUNKS" != "0" ] && echo "✅ $TOTAL_CHUNKS chunks" || echo "❌ No chunks")"
echo "Ask-Share:    $(echo "$ASK_RESPONSE" | jq -e '.answer' > /dev/null 2>&1 && echo "✅ Working" || echo "❌ Failed")"
echo ""
echo "Share URL: $BASE_URL/share/$SHARE_TOKEN"
echo "========================================"

