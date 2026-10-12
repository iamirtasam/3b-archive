# Changelog

All notable changes to this project are documented here. Dates are when the phase landed.

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
