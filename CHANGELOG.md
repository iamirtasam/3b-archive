# Changelog

All notable changes to this project are documented here. Dates are when the phase landed.

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
