-- Diagnostic SQL Script: Trace Share → Batch → Files → Chunks
-- Usage: Replace 'YOUR_SHARE_TOKEN_HERE' with actual share token
-- Run in PostgreSQL console or pgAdmin

\set share_token 'Q4ZIHIb9OYtPuEbm1mP2mQ'

\echo '================================================'
\echo 'STEP 1: SHARE RECORD'
\echo '================================================'
SELECT 
    token as share_token,
    org_id,
    payload->>'batch_token' as batch_token_in_share,
    payload->>'document_ids' as document_ids_in_share,
    payload->>'mode' as mode,
    expires_at,
    created_at
FROM public.share_links
WHERE token = :'share_token';

\echo ''
\echo '================================================'
\echo 'STEP 2: BATCH RECORD (if batch_token exists)'
\echo '================================================'
SELECT 
    ob.id as batch_id,
    ob.token as batch_token,
    ob.org_id,
    ob.title,
    ob.status,
    ob.created_at
FROM public.share_links sl
CROSS JOIN LATERAL (
    SELECT (sl.payload->>'batch_token')::text as bt
) extracted
JOIN public.offer_batches ob ON ob.token = extracted.bt
WHERE sl.token = :'share_token'
  AND extracted.bt IS NOT NULL
  AND extracted.bt != '';

\echo ''
\echo '================================================'
\echo 'STEP 3: FILES IN BATCH'
\echo '================================================'
SELECT 
    of.id as file_id,
    of.filename,
    of.embeddings_ready,
    of.storage_path,
    of.insurer_code,
    of.mime_type,
    of.size_bytes,
    of.created_at
FROM public.share_links sl
CROSS JOIN LATERAL (
    SELECT (sl.payload->>'batch_token')::text as bt
) extracted
JOIN public.offer_batches ob ON ob.token = extracted.bt
JOIN public.offer_files of ON of.batch_id = ob.id
WHERE sl.token = :'share_token'
  AND extracted.bt IS NOT NULL
ORDER BY of.id;

\echo ''
\echo '================================================'
\echo 'STEP 4: CHUNK COUNTS PER FILE'
\echo '================================================'
SELECT 
    of.id as file_id,
    of.filename,
    of.embeddings_ready,
    COUNT(oc.id) as chunk_count,
    MIN(oc.chunk_index) as min_chunk_idx,
    MAX(oc.chunk_index) as max_chunk_idx
FROM public.share_links sl
CROSS JOIN LATERAL (
    SELECT (sl.payload->>'batch_token')::text as bt
) extracted
JOIN public.offer_batches ob ON ob.token = extracted.bt
JOIN public.offer_files of ON of.batch_id = ob.id
LEFT JOIN public.offer_chunks oc ON oc.file_id = of.id
WHERE sl.token = :'share_token'
  AND extracted.bt IS NOT NULL
GROUP BY of.id, of.filename, of.embeddings_ready
ORDER BY of.id;

\echo ''
\echo '================================================'
\echo 'STEP 5: TOTAL CHUNKS FOR SHARE'
\echo '================================================'
SELECT 
    COUNT(*) as total_chunks,
    COUNT(DISTINCT oc.file_id) as files_with_chunks
FROM public.share_links sl
CROSS JOIN LATERAL (
    SELECT (sl.payload->>'batch_token')::text as bt
) extracted
JOIN public.offer_batches ob ON ob.token = extracted.bt
JOIN public.offer_files of ON of.batch_id = ob.id
JOIN public.offer_chunks oc ON oc.file_id = of.id
WHERE sl.token = :'share_token'
  AND extracted.bt IS NOT NULL;

\echo ''
\echo '================================================'
\echo 'STEP 6: SAMPLE CHUNK CONTENT'
\echo '================================================'
SELECT 
    of.filename,
    oc.chunk_index,
    LEFT(oc.text, 150) as text_preview,
    oc.metadata->>'length' as chunk_length
FROM public.share_links sl
CROSS JOIN LATERAL (
    SELECT (sl.payload->>'batch_token')::text as bt
) extracted
JOIN public.offer_batches ob ON ob.token = extracted.bt
JOIN public.offer_files of ON of.batch_id = ob.id
JOIN public.offer_chunks oc ON oc.file_id = of.id
WHERE sl.token = :'share_token'
  AND extracted.bt IS NOT NULL
ORDER BY of.id, oc.chunk_index
LIMIT 5;

\echo ''
\echo '================================================'
\echo 'STEP 7: CHECK IF BATCH_TOKEN IS NULL'
\echo '================================================'
-- If batch_token is NULL, try to infer it from document_ids
WITH share_data AS (
    SELECT 
        sl.token as share_token,
        sl.payload->>'batch_token' as batch_token,
        sl.payload->'document_ids' as document_ids_json,
        jsonb_array_elements_text(sl.payload->'document_ids') as doc_id
    FROM public.share_links sl
    WHERE sl.token = :'share_token'
),
extracted_filenames AS (
    SELECT 
        share_token,
        batch_token,
        doc_id,
        split_part(doc_id, '::', 3) as extracted_filename
    FROM share_data
),
matching_files AS (
    SELECT 
        ef.share_token,
        ef.batch_token,
        ef.doc_id,
        ef.extracted_filename,
        of.filename as db_filename,
        of.batch_id,
        ob.token as inferred_batch_token
    FROM extracted_filenames ef
    LEFT JOIN public.offer_files of ON of.filename = ef.extracted_filename
    LEFT JOIN public.offer_batches ob ON ob.id = of.batch_id
)
SELECT 
    share_token,
    batch_token as current_batch_token,
    CASE 
        WHEN batch_token IS NULL OR batch_token = '' 
        THEN '❌ NULL - needs inference'
        ELSE '✅ Present'
    END as batch_token_status,
    doc_id,
    extracted_filename,
    db_filename,
    CASE 
        WHEN db_filename IS NULL THEN '❌ No match in DB'
        WHEN db_filename = extracted_filename THEN '✅ Exact match'
        ELSE '⚠️  Different: ' || db_filename
    END as filename_match_status,
    inferred_batch_token
FROM matching_files
ORDER BY doc_id;

\echo ''
\echo '================================================'
\echo 'STEP 8: FILES WITHOUT CHUNKS (Need Re-Embedding)'
\echo '================================================'
SELECT 
    of.id as file_id,
    of.filename,
    of.embeddings_ready,
    of.storage_path,
    CASE 
        WHEN of.embeddings_ready = false THEN '❌ Need reembed'
        WHEN chunk_count = 0 THEN '❌ No chunks despite ready flag'
        ELSE '✅ OK'
    END as status,
    chunk_count
FROM public.share_links sl
CROSS JOIN LATERAL (
    SELECT (sl.payload->>'batch_token')::text as bt
) extracted
JOIN public.offer_batches ob ON ob.token = extracted.bt
JOIN public.offer_files of ON of.batch_id = ob.id
LEFT JOIN (
    SELECT file_id, COUNT(*) as chunk_count
    FROM public.offer_chunks
    GROUP BY file_id
) chunks ON chunks.file_id = of.id
WHERE sl.token = :'share_token'
  AND extracted.bt IS NOT NULL
  AND (of.embeddings_ready = false OR COALESCE(chunk_count, 0) = 0)
ORDER BY of.id;

\echo ''
\echo '================================================'
\echo 'DIAGNOSTIC SUMMARY'
\echo '================================================'
-- Summary of issues
SELECT 
    CASE 
        WHEN sl.payload->>'batch_token' IS NULL OR sl.payload->>'batch_token' = '' 
        THEN '❌ CRITICAL: batch_token is NULL in share'
        ELSE '✅ batch_token exists: ' || (sl.payload->>'batch_token')
    END as batch_token_check,
    
    CASE 
        WHEN batch_exists.id IS NULL AND (sl.payload->>'batch_token' IS NOT NULL AND sl.payload->>'batch_token' != '')
        THEN '❌ CRITICAL: batch_token points to non-existent batch'
        WHEN batch_exists.id IS NULL THEN 'N/A (no batch_token)'
        ELSE '✅ Batch record exists (id=' || batch_exists.id || ')'
    END as batch_exists_check,
    
    COALESCE(file_count.cnt, 0) as files_in_batch,
    CASE 
        WHEN COALESCE(file_count.cnt, 0) = 0 
        THEN '❌ CRITICAL: No files in batch'
        ELSE '✅ ' || file_count.cnt || ' files found'
    END as files_check,
    
    COALESCE(chunk_count.cnt, 0) as total_chunks,
    CASE 
        WHEN COALESCE(chunk_count.cnt, 0) = 0 
        THEN '❌ CRITICAL: No chunks available'
        ELSE '✅ ' || chunk_count.cnt || ' chunks ready'
    END as chunks_check,
    
    COALESCE(unembedded.cnt, 0) as files_needing_reembed
    
FROM public.share_links sl
LEFT JOIN public.offer_batches batch_exists 
    ON batch_exists.token = (sl.payload->>'batch_token')
LEFT JOIN (
    SELECT of.batch_id, COUNT(*) as cnt
    FROM public.offer_files of
    GROUP BY of.batch_id
) file_count ON file_count.batch_id = batch_exists.id
LEFT JOIN (
    SELECT of.batch_id, COUNT(DISTINCT oc.id) as cnt
    FROM public.offer_files of
    JOIN public.offer_chunks oc ON oc.file_id = of.id
    GROUP BY of.batch_id
) chunk_count ON chunk_count.batch_id = batch_exists.id
LEFT JOIN (
    SELECT of.batch_id, COUNT(*) as cnt
    FROM public.offer_files of
    LEFT JOIN public.offer_chunks oc ON oc.file_id = of.id
    WHERE of.embeddings_ready = false OR oc.id IS NULL
    GROUP BY of.batch_id
) unembedded ON unembedded.batch_id = batch_exists.id
WHERE sl.token = :'share_token';

\echo ''
\echo '================================================'
\echo 'RECOMMENDED ACTIONS'
\echo '================================================'
\echo 'Based on the results above:'
\echo '1. If batch_token is NULL → Update share with correct batch_token'
\echo '2. If files_in_batch = 0 → Files not uploaded or linked to wrong batch'
\echo '3. If total_chunks = 0 → Re-embed files using /api/qa/reembed-file'
\echo '4. If filename mismatch → Check document_ids vs offer_files.filename'
\echo ''
\echo 'Re-embed command for each file_id from Step 8:'
\echo 'curl -X POST "https://gpt-vis.onrender.com/api/qa/reembed-file?file_id=FILE_ID" -H "X-User-Role: admin"'

