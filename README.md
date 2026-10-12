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
│   ├── publish.py      Promote browser-staged uploads into playable videos
│   └── lib/            Shared helpers (ffmpeg, S3, catalog, thumbnails)
├── site/               The website — served by CloudFront at the same domain
│   ├── index.html      Login, archive, and player views (single page app)
│   ├── app.js          Routing, HLS playback, resume, cookie-based auth
│   └── styles.css      Brutalist editorial design system
├── backend/            Lambda API + deployment tooling
│   ├── app/handler.py  Login, catalog, stream auth, upload presigning
│   ├── deploy.py       Idempotent backend provisioning (DynamoDB, SSM, Lambda, HTTP API)
│   ├── sync_cloudfront.py  Wire site + API + videos into one distribution
│   ├── deploy_site.py  Upload the website and invalidate the CDN cache
│   ├── ensure_cors.py  Bucket CORS for direct browser uploads
│   ├── create_user.py  Create a viewer account
│   ├── seed_catalog.py Sync the local catalog into DynamoDB
│   └── e2e_test.py     End-to-end verification of the whole stack
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

## How the site works

One CloudFront distribution serves everything from a single domain — `https://d31uqelgcxwhug.cloudfront.net`:

| Path | Served by | Access |
| --- | --- | --- |
| `/` | Site files in S3 (`index.html`, `app.js`, …) | Public — it's just the app shell, no data |
| `/api/*` | API Gateway → Lambda (login, catalog, stream auth, upload presigning) | Public endpoints, authenticated logic |
| `/videos/*` | The HLS videos in S3 | Signed cookies required — 403 for everyone else |
| `uploads/*` (in S3) | Staged originals waiting for packaging | Presigned part URLs only |

Because the site, the API, and the videos share one domain, logging in sets CloudFront signed cookies once and the browser attaches them to every playlist, segment, and API call automatically. No CORS, no per-URL signatures, no juggling tokens. Expired playback cookies renew themselves mid-stream via `/api/stream-auth`.

### Uploading from the browser (Phase 5)

Click **UPLOAD** in the site's nav bar (or drag recordings onto the upload page):

1. The browser picks up your files and asks the Lambda for presigned part URLs
2. It streams 16 MB chunks **directly to S3** — with a live progress bar, speed, and ETA; nothing flows through the API
3. Close the tab mid-upload? Reopen the page and re-pick the file — it resumes from wherever S3 already has bytes
4. Finished files sit in `uploads/` as *staged* originals

Then, from your PC, close the loop (this is the one step a browser can't do, because packaging needs ffmpeg):

```powershell
python scripts\publish.py
```

That downloads each staged original, remuxes it to HLS losslessly, uploads the stream package, removes the staged copy, and refreshes the catalog — the videos appear on the site immediately. `--dry-run` previews; `--keep-source` retains the originals.

### Adding new users

```powershell
python backend\create_user.py <username> <password>
```

### Deploying the site after changes

```powershell
python backend\deploy_site.py
```

### Backend / infrastructure updates

```powershell
python backend\deploy.py           # Lambda code, env, IAM
python backend\sync_cloudfront.py  # distribution behaviors (rarely changes)
python backend\ensure_cors.py      # bucket CORS for browser uploads (idempotent)
```

## Costs

| Item | Cost |
| --- | --- |
| S3 storage (320 GB) | ≈ $8 / month |
| CloudFront egress | $0 — first 1 TB/month is free |
| ffmpeg remuxing | $0 (runs on your PC, lossless) |
| Backend (Lambda + DynamoDB) | ≈ $0 (free tier) |
| **Total** | **≈ $8 / month** |

## Privacy model

The S3 bucket blocks all public access. The only way in is through CloudFront, and every `/videos/*` request must carry cookies signed with a private key that lives in SSM Parameter Store — viewers get them only after logging in. So you plus the people you invite can watch, and everyone else gets a 403. See `docs/aws-setup.md` for the exact setup.

## Roadmap

- [x] **Phase 1** — AWS setup + local remux/upload pipeline (this repo)
- [x] **Phase 2** — first videos migrated (Valorant batch, 3.7 GB, 327 segments)
- [x] **Phase 3** — backend: login, folder catalog, signed cookie issuing (Lambda + API Gateway + DynamoDB)
- [x] **Phase 4** — website: brutalist login → folder browse → gallery → HLS player with resume
- [x] **Phase 5** — web uploader: drag-and-drop or file picker, direct-to-S3 multipart with progress, speed, ETA, and refresh-proof resume; `publish.py` closes the loop from your PC
- [ ] **Phase 6** — iOS app via [xtool](https://xtool.sh) (SwiftUI + AVPlayer, built from Windows)
- [ ] **Phase 7** — region migration script (ap-south-1 → eu-central-1) and niceties

## FAQ

**Do I need my PC on 24/7?** No — only when adding new videos. Once on S3, playback runs entirely on AWS, even with your PC off for a month.

**Why not just play the MP4s directly from S3?** That works with `faststart`-optimized files, but HLS gives instant seeking, chunked playback, and native iOS support — the difference is very noticeable on real connections.

**Why not MediaConvert?** It re-encodes in the cloud (nice, but $180–800 for this library, plus doubled storage). Remuxing is free and lossless. If adaptive bitrate (auto quality switching) ever matters, MediaConvert can be added for new uploads only.

**Why not just use YouTube unlisted?** Terms of service, privacy, no API for a private app, ads, and zero control. Also — building it is the fun part.

## License

[MIT](LICENSE)
