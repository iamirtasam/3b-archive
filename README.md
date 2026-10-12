# s3-app-streaming

A self-hosted, YouTube-style video streaming platform built on AWS — your own private video library, streamable from a website and an iOS app, for about the price of a coffee per month.

## Why this exists

I have roughly 320 GB of video recordings and wanted to watch them anywhere — in a browser or on my iPhone — without downloading files, without subscription fees, and without handing them to a third-party service. So this project does the obvious-but-fiddly thing: store the videos in Amazon S3, package them for proper streaming, and put a private, signed-URL-protected front end on top.

The playback experience is the familiar one — videos start instantly, seeking is fast, and only the few seconds of video you are actually watching travel over the wire. Nothing is "downloaded" in any meaningful sense.

## How it works

```
Your PC (ffmpeg, free) ──▶ S3 bucket (private)
                              │
                        CloudFront CDN (signed URLs — only logged-in viewers)
                              │
              ┌───────────────┴───────────────┐
        Website (hls.js)                iOS app (AVPlayer)
              └───────────────┬───────────────┘
                    Small API (Lambda + DynamoDB)
                    login, folder catalog, signed playback URLs
```

The key idea is **HLS** (HTTP Live Streaming): each video is split into ~6-second segments. A player loads the tiny playlist file, then fetches segments on demand — which is precisely how YouTube-style playback works. iOS plays HLS natively with `AVPlayer`; browsers use `hls.js`.

The videos are **remuxed, not re-encoded**. ffmpeg takes the existing H.264 stream and splits it into segments without touching the actual video data — completely lossless, effectively disk-speed, and completely free. (The paid alternative, AWS MediaConvert, would cost $180–800 for a library this size.)

## What's in this repo

```
├── scripts/            Python pipeline: remux → thumbnails → upload to S3
│   ├── prepare.py      Package recordings into HLS (resumable, lossless)
│   ├── upload.py       Upload prepared videos to S3 (resumable, multipart)
│   ├── pipeline.py     Both of the above in one command
│   └── lib/            Shared helpers (ffmpeg, S3, catalog, thumbnails)
├── docs/
│   └── aws-setup.md    One-time cloud setup: bucket, CloudFront, signing keys
└── config.example.json Copy to config.json and edit
```

## Quick start

### 1. Install the tools

```powershell
winget install Gyan.FFmpeg
winget install Amazon.AWSCLI
pip install -r scripts/requirements.txt
aws configure   # access key + secret + region (ap-south-1)
```

### 2. One-time AWS setup

Follow [docs/aws-setup.md](docs/aws-setup.md) — create a private S3 bucket and a CloudFront distribution with signed access. About 30 minutes, $0.

### 3. Configure

```powershell
copy config.example.json config.json
# edit: source_dir (where your recordings live), bucket name, region
```

### 4. Push a batch of videos

```powershell
python scripts/pipeline.py --source "H:\Recordings\Batch 3"
```

Interrupted? Run it again — it resumes exactly where it stopped. Your PC is only needed for these few minutes or hours of packaging and uploading; streaming afterwards is entirely on AWS.

## Costs

| Item | Cost |
| --- | --- |
| S3 storage (320 GB) | ≈ $8 / month |
| CloudFront egress | $0 — first 1 TB/month is free |
| ffmpeg remuxing | $0 (runs on your PC, lossless) |
| Backend (Lambda + DynamoDB) | ≈ $0 (free tier) |
| **Total** | **≈ $8 / month** |

## Privacy model

The S3 bucket blocks all public access. The only way in is through CloudFront URLs signed with a private key held on your machine. The planned backend issues short-lived signed URLs only after login — so you plus the people you invite can watch, and nobody else. See `docs/aws-setup.md` for the exact setup.

## Roadmap

- [x] **Phase 1** — AWS setup + local remux/upload pipeline (this repo)
- [ ] **Phase 2** — bulk migration of the 320 GB library
- [ ] **Phase 3** — backend: login, folder catalog, signed URL issuing (Lambda + API Gateway + DynamoDB)
- [ ] **Phase 4** — website: folder browse → gallery → HLS player
- [ ] **Phase 5** — web uploader: browser file picker with multipart upload progress, live speed, and ETA (presigned S3 URLs)
- [ ] **Phase 6** — iOS app via [xtool](https://xtool.sh) (SwiftUI + AVPlayer, built from Windows)
- [ ] **Phase 7** — region migration script (ap-south-1 → eu-central-1) and niceties

## FAQ

**Do I need my PC on 24/7?** No — only when adding new videos. Once on S3, playback runs entirely on AWS, even with your PC off for a month.

**Why not just play the MP4s directly from S3?** That works with `faststart`-optimized files, but HLS gives instant seeking, chunked playback, and native iOS support — the difference is very noticeable on real connections.

**Why not MediaConvert?** It re-encodes in the cloud (nice, but $180–800 for this library, plus doubled storage). Remuxing is free and lossless. If adaptive bitrate (auto quality switching) ever matters, MediaConvert can be added for new uploads only.

**Why not just use YouTube unlisted?** Terms of service, privacy, no API for a private app, ads, and zero control. Also — building it is the fun part.

## License

[MIT](LICENSE)
