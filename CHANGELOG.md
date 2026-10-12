# Changelog

All notable changes to this project are documented here. Dates are when the phase landed.

## 0.5.0 — Phase 5 (2026-10-12)

### Added
- Web uploader at `#/upload`: file picker plus drag-anywhere drop zone, in the same brutalist design language
- Direct-to-S3 multipart uploads (16 MB parts, 3 concurrent) with presigned URLs — upload bytes never touch the API
- Live progress per file: percent bar, parts counter, rolling speed, and ETA
- Refresh-proof resume: S3's own part list is the source of truth — reopen the page, re-pick the file, continue where it stopped
- Staged-uploads list with discard (abort multipart) support
- `scripts/publish.py` — the PC-side loop closer: download staged originals, remux to fMP4 HLS, upload, clean up, seed the catalog; resumable at every step, with `--dry-run` and `--keep-source`
- Upload API routes: start (presign), parts (presign page + received-parts list), complete, abort, list
- Bucket CORS setup (`ensure_cors.py`) enabling browser PUTs with exposed ETags

### Fixed
- Deep links (`#/watch/<slug>`) now resolve on fresh page loads — the router waits for the catalog before first paint
- `app.js`/`styles.css` served with `must-revalidate` so deploys reach browsers immediately instead of up to 5 minutes later
- Lambda IAM: `s3:PutObject` granted for `CreateMultipartUpload` on the uploads prefix

### Removed
- The published test video and its artifacts after the end-to-end verification run

## 0.4.0 — Phase 4 (2026-10-12)

### Added
- Website served from the same CloudFront domain as the videos — one origin for the app shell, `/api/*` for the backend, `/videos/*` for HLS media
- Brutalist editorial design system: warm-gray canvas, oversized condensed display type, mint accent tags, voltage-yellow highlights, flat surfaces, zero shadows
- Three views: login (with hero, ticker, and access card), archive (folder cards + video grid with thumbnails, duration badges, and watch-progress bars), and player (black stage, resume support)
- Editorial motion: staggered card entrances, marquee tickers built from real titles, line-reveal headings, inversion hover states, `prefers-reduced-motion` respected
- HLS playback via vendored hls.js — no external CDN dependency; Safari falls back to native HLS
- Watch-position resume per video via localStorage, surfaced as "N% WATCHED" progress bars on cards
- Expired playback cookies renew themselves mid-stream (403 in hls.js triggers a silent `/api/stream-auth` retry)
- Hash-based routing with deep links (`#/watch/<slug>`, `#/folder/<name>`) and browser back/forward support
- `backend/sync_cloudfront.py` — idempotent multi-origin wiring: API Gateway origin, `/api/*` + `/videos/*` behaviors, public default behavior, `index.html` root object
- `backend/deploy_site.py` — uploads site files with correct content types and cache headers, then invalidates the CDN cache

### Fixed
- UpdateDistribution rejects sparse behaviors: new behaviors now clone the complete default-behavior field set (SmoothStreaming, Compress, etc.)
- Managed-CachingOptimized policy ID corrected; legacy per-behavior TTL fields removed when cache policies are attached

## 0.1.0 — Phase 1 (2026-10-12)

### Added
- Lossless HLS remux pipeline (ffmpeg `-c copy`) — video quality is bit-for-bit identical to the source, at disk speed, for free
- Automatic fallback transcode (H.264/AAC) for sources whose codecs browsers cannot play
- Resumable prepare/upload commands: `prepare.py`, `upload.py`, and the one-shot `pipeline.py`
- JSON catalog keyed on file fingerprints — interrupted runs skip finished work, edited files are re-detected automatically
- Poster thumbnail generation per video with out-of-range seek fallback
- Multipart S3 uploader (16 MB chunks, threaded) that skips files already present at the same size
- Content-type mapping for HLS playlists, segments, and thumbnails
- AWS setup guide: private S3 bucket, CloudFront distribution with signed access via key groups, billing alarm
- Project README with architecture, cost breakdown (≈ $12/month), privacy model, and roadmap
- CI workflow: syntax check, JSON validation, and import check on every push
