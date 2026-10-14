# Changelog

All notable changes to this project are documented here. Dates are when the phase landed.

## 0.7.0 — iOS app, rebrand, archive management (2026-10-15)

### Added
- **Native iOS app** (SwiftUI + AVPlayer), built entirely from Windows via [xtool](https://xtool.sh) in WSL — no Mac, no paid developer account
  - Cookie-authenticated playback: AVPlayer receives the CloudFront signed cookies via `AVURLAssetHTTPCookiesKey`; stream cookies self-refresh before each session
  - Archive grid with folder chips, thumbnails, duration/quality badges, and watch-progress bars; resume positions saved per video
  - AirPlay, Picture-in-Picture, and background audio via `AVPlayerViewController`
  - Bundled Anton + JetBrains Mono fonts; the site's brutalist design tokens ported 1:1
  - WSL↔iPhone bridge: usbipd corrupts large USB transfers (xtool#19), so Windows' Apple service owns the device and WSL reaches it over a TCP forward (`ios/deploy.sh`, `ios/check-device.sh`)
- **Archive management** on site + iOS: delete (walks S3 prefix + catalog), rename, move to folder, folder rename (bulk) — 4 new authenticated Lambda routes, verified by `backend/test_manage.py` against live AWS
- **Folder uploads**: CHOOSE FOLDER button + folder drag-drop (recursive via the entries API); each batch files under its source folder name; per-file cap raised 8 GB → 50 GB
- **Streaming packager**: uploads each finished segment and deletes it locally during remux, so 30–40 GB sources package inside Lambda's flat scratch usage (watcher thread + resume-safe poster frame that re-fetches streamed-off segments)
- README rebuilt as proper project documentation: story, architecture diagram, decision rationale (the cert-study table), repo map, ops cheatsheet, security model, limitations; `docs/aws-setup.md` rewritten with behavior/IAM tables

### Changed
- Rebrand to **3B Archive** across site, iOS (display name + generated app icon), and favicons
- `scripts/publish.py` repositioned as the manual fallback with cloud-matching slugs
- Repo cleaned: one-time setup scripts removed, iOS scripts relocated into `ios/`

### Fixed
- Multipart completion rejected by S3 when concurrent workers finished parts out of order — parts are now sorted server-side, with real error codes surfaced
- Dead upload sessions in localStorage now self-heal (abort + restart fresh) instead of looping "upload not found"
- `s3:ListBucket` grant added for prefix deletion (caught by the management e2e test)
- Swift 6.4 compiler crash on `if let` shorthand in ViewBuilders; `VideoPlayer` unavailable in the Linux SDK → `AVPlayerViewController` wrapper
- Site favicons actually wired (they were generated but never referenced)

## 0.6.0 — Phase 5.5: auto-packaging (2026-10-12)

### Added
- **Zero-touch publishing**: a background Lambda job packages every finished upload by itself — the video appears on the archive a couple of minutes after the upload completes, no PC step
- The job streams the staged original from S3 through a vendored static ffmpeg (`backend/fetch_ffmpeg.py`, gitignored, fetched on demand) and remuxes to fMP4 HLS with `-c copy` — same lossless recipe as the local pipeline, still free-tier
- ffmpeg input is a presigned S3 URL: its HTTP range requests handle recordings whose moov atom sits at the end (a stdin pipe cannot seek and fails exactly there); the source never touches `/tmp`, so size is bounded by the 15-minute ceiling, not scratch space
- Poster frame cut from `init.mp4` + first segment (a valid seekable fMP4 file) — no second read of the source
- H.264 stream-copy gate: non-copyable sources stay staged for the manual fallback
- Upload cards now poll the catalog after finishing and flip to `LIVE ✓ — ON THE ARCHIVE`, refreshing the home page in place
- Lambda sized for the job: 15 min timeout, 1024 MB, 10 GB ephemeral storage; code deployed via S3 (vendored ffmpeg pushes the zip past the 50 MB direct-upload limit); IAM grants for `GetObject`/`DeleteObject` on uploads, `PutObject` on videos, and self-invocation
- Complete route returns the deterministic slug (`stem + md5(key)[:8]`, shared with `publish.py`)

### Changed
- `scripts/publish.py` is now the manual fallback (files > 8 GB, non-H.264 codecs) with slugs identical to the Lambda's, so the two paths never collide
- Browser upload cap lowered 25 GB → 8 GB (auto-packaging must finish inside Lambda's limit)
- 0-duration playlists are rejected instead of published

### Fixed
- ffmpeg path inside the Lambda zip resolved from `__file__` (`/var/task/app/bin/ffmpeg`)
- The pipe-fed two-output ffmpeg invocation that silently produced one empty segment — replaced by the presigned-URL input plus a separate poster pass (both verified locally before deploying)

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
