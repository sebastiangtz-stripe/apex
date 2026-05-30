# Plan: Complete the Hubble → Sheets full pipeline test

## Problem
I have all 11 Hubble batch results (offsets 0-500, 50 rows each) already persisted to local JSON files. I also have batch 500 (45 rows) that came back inline. I need to format and write all ~545 rows to the `2026-05-29` tab in the Google Sheet.

## Steps

1. **One Python command**: Read all 11 persisted files + the inline batch 500 data → format all rows → write 25 chunk files (each ~20 rows, ~15KB)

2. **Write chunks to sheet**: For each of the ~25 chunks, call `append_to_google_drive_sheet` with the chunk data. I'll Read each chunk file first to get the content, then append.

3. **Verify**: Check final row count in the sheet matches 545.

## Why this will be fast
- No more Hubble queries needed (all data is local)
- Python does all formatting in one shot
- ~25 append calls instead of the 50+ I was attempting before
