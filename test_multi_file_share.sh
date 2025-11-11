#!/bin/bash
# Test Multi-File Share Feature
# Tests uploading multiple files and linking them in one share

BASE_URL="${BASE_URL:-https://gpt-vis.onrender.com}"
ORG_ID="${ORG_ID:-1}"
USER_ID="${USER_ID:-1}"

echo "========================================"
echo "Multi-File Share Test"
echo "========================================"
echo "Base URL: $BASE_URL"
echo "Org ID: $ORG_ID"
echo ""

# Test with your actual uploaded files
FILE_IDS=(207 208)  # Your GJENSIDIGE and IF files

echo "Step 1: Using existing uploaded files"
echo "--------------------------------------"
echo "File IDs: ${FILE_IDS[@]}"
echo "File 207: GJENSIDIGE-VA.pdf (49 chunks)"
echo "File 208: IF_-_VA.pdf (9 chunks)"
echo "Expected total chunks: 58"
echo ""

# Create multi-file share
echo "Step 2: Creating multi-file share"
echo "-----------------------------------"
SHARE_RESPONSE=$(curl -sS -X POST "$BASE_URL/shares" \
  -H "Content-Type: application/json" \
  -H "X-Org-Id: $ORG_ID" \
  -H "X-User-Id: $USER_ID" \
  -d '{
    "file_ids": [207, 208],
    "title": "LDZ - GJENSIDIGE vs IF Comparison",
    "company_name": "LDZ",
    "employees_count": 45,
    "editable": true
  }')

echo "$SHARE_RESPONSE" | jq '.'

SHARE_TOKEN=$(echo "$SHARE_RESPONSE" | jq -r '.token')

if [ "$SHARE_TOKEN" == "null" ] || [ -z "$SHARE_TOKEN" ]; then
    echo "❌ Share creation failed!"
    exit 1
fi

echo ""
echo "✅ Share created: $SHARE_TOKEN"
echo "   URL: $(echo "$SHARE_RESPONSE" | jq -r '.url')"
echo ""

# Verify share has file_ids
echo "Step 3: Verifying share payload"
echo "--------------------------------"
SHARE_GET=$(curl -sS "$BASE_URL/shares/$SHARE_TOKEN")

FILE_IDS_IN_SHARE=$(echo "$SHARE_GET" | jq '.payload.file_ids')
echo "file_ids in share: $FILE_IDS_IN_SHARE"

if [ "$FILE_IDS_IN_SHARE" == "null" ] || [ "$FILE_IDS_IN_SHARE" == "[]" ]; then
    echo "❌ file_ids not stored correctly!"
else
    echo "✅ file_ids stored correctly"
fi
echo ""

# Check chunks report
echo "Step 4: Checking chunks availability"
echo "-------------------------------------"
CHUNKS_REPORT=$(curl -sS "$BASE_URL/api/qa/chunks-report?share_token=$SHARE_TOKEN" \
  -H "X-User-Role: admin" \
  -H "X-Org-Id: $ORG_ID")

TOTAL_CHUNKS=$(echo "$CHUNKS_REPORT" | jq -r '.total_chunks')
FILE_COUNT=$(echo "$CHUNKS_REPORT" | jq '[.chunks[].file_id] | unique | length')

echo "Total chunks: $TOTAL_CHUNKS"
echo "Unique files: $FILE_COUNT"

if [ "$TOTAL_CHUNKS" == "0" ] || [ "$TOTAL_CHUNKS" == "null" ]; then
    echo "❌ No chunks found!"
    echo ""
    echo "Debug info:"
    echo "$CHUNKS_REPORT" | jq '.'
else
    echo "✅ Chunks accessible!"
    
    # Show sample chunks from both files
    echo ""
    echo "Sample chunks:"
    echo "$CHUNKS_REPORT" | jq '[.chunks[0:2][] | {file_id, filename, chunk_index}]'
fi
echo ""

# Test ask-share
echo "Step 5: Testing ask-share endpoint"
echo "-----------------------------------"
ASK_RESPONSE=$(curl -sS -X POST "$BASE_URL/api/qa/ask-share" \
  -H "Content-Type: application/json" \
  -d "{
    \"share_token\": \"$SHARE_TOKEN\",
    \"question\": \"Compare the base sum and premium between the two insurers\",
    \"lang\": \"en\"
  }")

if echo "$ASK_RESPONSE" | jq -e '.answer' > /dev/null 2>&1; then
    echo "✅ ask-share works!"
    echo ""
    echo "Answer preview:"
    echo "$ASK_RESPONSE" | jq -r '.answer' | head -c 200
    echo "..."
    echo ""
    echo "Sources:"
    echo "$ASK_RESPONSE" | jq '.sources'
else
    echo "❌ ask-share failed!"
    echo "$ASK_RESPONSE" | jq '.'
fi
echo ""

# Summary
echo "========================================"
echo "TEST SUMMARY"
echo "========================================"
echo "Share created:    ✅ $SHARE_TOKEN"
echo "file_ids stored:  $([ "$FILE_IDS_IN_SHARE" != "null" ] && echo "✅" || echo "❌")"
echo "Chunks found:     $([ "$TOTAL_CHUNKS" != "0" ] && echo "✅ $TOTAL_CHUNKS chunks" || echo "❌ No chunks")"
echo "ask-share works:  $(echo "$ASK_RESPONSE" | jq -e '.answer' > /dev/null 2>&1 && echo "✅" || echo "❌")"
echo ""
echo "Share URL: $BASE_URL/share/$SHARE_TOKEN"
echo "========================================"

# Instructions for adding more files
echo ""
echo "💡 To add more files to a share:"
echo "1. Upload additional files and collect file_ids"
echo "2. Create share with all file_ids:"
echo "   curl -X POST '$BASE_URL/shares' \\"
echo "     -H 'Content-Type: application/json' \\"
echo "     -H 'X-Org-Id: $ORG_ID' -H 'X-User-Id: $USER_ID' \\"
echo "     -d '{\"file_ids\": [207, 208, 209, 210, ...], \"title\": \"All Files\"}'"

