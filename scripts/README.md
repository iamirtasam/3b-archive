# Local pipeline scripts

These scripts run on your PC only when you are adding videos. They package your recordings into HLS and upload them to S3 — streaming itself never touches your PC.

## The one-command workflow

```powershell
python pipeline.py --source "H:\Recordings\Batch 3"
```

That single command does everything:

1. **Prepares** — probes each new file, remuxes it into HLS segments (lossless, no re-encoding for H.264/AAC), and grabs a poster thumbnail.
2. **Uploads** — pushes every prepared-but-not-yet-uploaded video to S3 and marks it done in the catalog.

Interrupted? Just run it again. Already-processed files are skipped, so it resumes exactly where it stopped. A file whose size or modified time changed is detected by fingerprint and re-processed automatically.

## Install the Python dependencies

```powershell
pip install -r scripts/requirements.txt
```

## Configure

```powershell
copy config.example.json config.json
# then edit config.json:
#   paths.source_dir  - where your recordings live
#   s3.bucket         - your bucket name
#   aws.region        - e.g. ap-south-1
```

## The individual steps

### 1. Prepare only

```powershell
python prepare.py                       # everything in config's source_dir
python prepare.py --source "D:\Clips"   # one specific folder
python prepare.py --limit 5             # first 5 files only (good for testing)
python prepare.py --dry-run             # list what would happen, change nothing
python prepare.py --force               # redo even already-prepared files
```

Output lands in `.data/hls/<slug>/` (playlist + `.ts` segments) and `.data/thumbs/<slug>.jpg`. The catalog at `.data/catalog.json` remembers everything — it is the resume state, so back it up if you reorganize folders.

### 2. Upload only

```powershell
python upload.py                        # everything ready but not yet uploaded
python upload.py --slug my-video-9f2c41ab   # just one video
python upload.py --dry-run
```

Uploads use multipart transfers (16 MB chunks, 8 threads by default) and skip files that S3 already has with the same size — so a dropped connection costs you seconds, not hours.

### 3. Full pipeline (most of the time)

```powershell
python pipeline.py                      # prepare + upload in one go
```

## What "slug" means

Each video gets a stable, URL-friendly ID derived from its relative path plus a short fingerprint, e.g. `talks-intro-9f2c41ab`. It becomes the folder name on S3:

```
s3://my-video-archive/videos/talks-intro-9f2c41ab/
├── index.m3u8
├── seg_00000.ts
├── seg_00001.ts
├── ...
└── thumbnail.jpg
```

Once uploaded, the HLS playlist URL (via CloudFront) is what your website and iOS app will play.

### 4. Watch a video (signed playback URL)

```powershell
python sign-url.py list                                  # see uploaded slugs
python sign-url.py valorant-2026-05-29-13-50-45-02-3aae6ecb
```

Paste the printed URL into **VLC → Media → Open Network Stream** (or Safari, or any hls.js page). The URL is valid 4 hours by default (`--hours 8` to change) and unlocks that one video's playlist, segments, and thumbnail — nothing else.

Requires the `cloudfront` block in `config.json` (domain, key pair ID, private key path) from the AWS setup. Note: HLS needs one signed policy covering the whole video folder — per-file signed URLs would 403 on every segment fetch.

## Good to know

- **Remux vs transcode** — H.264/AAC files (almost all phone and screen recordings) are remuxed instantly and losslessly. Anything exotic triggers an automatic fallback transcode (slower, still fine). The log tells you which path each file took.
- **Fingerprints** — the catalog keys off file size + modified time. If you re-encode or edit a source file, it is detected as new and re-processed on the next run.
- **Deleted sources** — when you delete a recording locally, its catalog entry is pruned on the next run (the uploaded copy on S3 stays until you remove it there).
- **Failures don't stop the run** — a corrupt file is logged and the pipeline moves on; you will see a summary at the end.
