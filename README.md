# S3 App Streaming

<div align="center">

<img src="site/favicon-180.png" width="72" alt="3B Archive logo" />

**A private, YouTube-style streaming platform built from scratch on AWS.**

*Your entire video library â€” streamed to a website and an iPhone, from a fully private S3 bucket, for ~$8/month.*

[![CI](https://github.com/iamirtasam/3b-archive/actions/workflows/ci.yml/badge.svg)](https://github.com/iamirtasam/3b-archive/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/license-MIT-informational)](LICENSE)
[![AWS](https://img.shields.io/badge/cloud-AWS_S3_%2B_CloudFront_%2B_Lambda-232f3e?logo=amazonaws&logoColor=white)](https://aws.amazon.com)
[![No servers](https://img.shields.io/badge/architecture-serverless-success)](README.md#architecture)
[![Lossless](https://img.shields.io/badge/remux-ffmpeg_-c_copy-blueviolet)](README.md#the-video-pipeline)

</div>

---

## The Story

> **I ran out of disk space.**
>
> Around 320 GB of screen recordings with nowhere to go. The obvious options were all wrong: YouTube unlisted is a ToS gray zone with ads and zero control; a NAS is a machine I have to keep running; a paid streaming service is a subscription for content nobody else should see.
>
> So I built the thing I actually wanted: **my recordings live in a private S3 bucket, stream instantly to a website and my iPhone, and cost about the price of a coffee per month.** Uploading is drag-and-drop from the browser. The cloud packages everything automatically. My PC can stay off for a month and nothing breaks.
>
> **The full stack was written with AI assistance â€” the AWS architecture was designed by me.** Every choice here (trusted key groups over Lambda@Edge, signed cookies over per-URL signing, remuxing over transcoding, one distribution over many origins) was made deliberately, documented below, and would survive a solutions-architect review. That was the point: build something real, and understand every layer of it.

## What It Does

| | |
|---|---|
| **Stream everywhere** | Brutalist-editorial web app (hls.js) + native iOS app (SwiftUI + AVPlayer, built from Windows â€” no Mac) |
| **Upload like YouTube Studio** | Drag a file â€” or an entire folder â€” onto the web uploader. 16 MB chunks stream straight to S3, resumable across reboots, up to 50 GB per file |
| **Zero-touch publishing** | The moment an upload lands, a background Lambda runs ffmpeg **in the cloud**: lossless HLS remux, poster frame, catalog entry, live on the archive in minutes |
| **Actually private** | CloudFront trusted-key-group signed cookies. The bucket blocks all public access; anonymous requests get a 403 before they ever reach S3 |
| **Archive management** | Rename, re-folder, and hard-delete videos and folders â€” from the site or the phone. Deletion walks S3 and removes every segment |
| **Synced progress** | Start a video on the website, resume on the phone |

## Architecture

One CloudFront distribution is the entire front door. The site, the API, and the videos share a single domain â€” which is what makes the privacy model and the playback path simple.

```
                               â”Œâ”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”
                               â”‚        VIEWERS              â”‚
                               â”‚   browser Â· iPhone (AVPlayer)
                               â””â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”¬â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”˜
                                              â”‚  https (one domain)
                                              â–¼
                              â”Œâ”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”
                              â”‚   CLOUDFRONT  (one distro)    â”‚
                              â”‚                               â”‚
                              â”‚   /        â†’  default behaviorâ”‚
                              â”‚   /api/*   â†’  behavior 2      â”‚
                              â”‚   /videos/*â†’  behavior 3      â”‚
                              â”‚               â–²               â”‚
                              â”‚               â”‚ 403 unless    â”‚
                              â”‚               â”‚ signed cookie â”‚
                              â”‚               â”‚ (trusted key  â”‚
                              â”‚               â”‚  group)       â”‚
                              â””â”€â”€â”€â”¬â”€â”€â”€â”€â”€â”€â”€â”€â”€â”¬â”€â”´â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”˜
                    cache+static  â”‚         â”‚  dynamic
                    CachingOptimized        â”‚  CachingDisabled
                    AllViewerExceptHost     â”‚  + AllViewerExceptHost
                              â–¼             â–¼
              â”Œâ”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”   â”Œâ”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”
              â”‚  S3 (private)    â”‚   â”‚  API Gateway (HTTP) â”‚
              â”‚  site/*  videos/*â”‚   â””â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”¬â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”˜
              â”‚  uploads/*       â”‚              â–¼
              â”‚  â–²   â–²      â–²    â”‚   â”Œâ”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”
              â””â”€â”€â”¼â”€â”€â”€â”¼â”€â”€â”€â”€â”€â”€â”¼â”€â”€â”€â”€â”˜   â”‚  LAMBDA (python)    â”‚
   presigned     â”‚   â”‚      â”‚        â”‚  login Â· catalog    â”‚
   PUTs (browser)â”‚   â”‚      â”‚        â”‚  stream-auth Â·      â”‚
   â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”˜   â”‚      â”‚        â”‚  upload presigning  â”‚
   HLS segments      â”‚      â”‚        â”‚  archive mgmt       â”‚
   (packager) â”€â”€â”€â”€â”€â”€â”€â”˜      â”‚        â””â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”¬â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”˜
   staged originals         â”‚                   â”‚
   (uploader) â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”˜        â”Œâ”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”´â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”
                                     â”‚  DYNAMODB           â”‚
                                     â”‚  users Â· sessions   â”‚
                                     â”‚  videos (catalog)   â”‚
                                     â””â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”˜
```

### The request lifecycle, concretely

1. **Login** â€” `POST /api/login` â†’ Lambda checks PBKDF2 (200k iterations, salted) against DynamoDB, sets an `svg_session` cookie, and immediately mints **CloudFront signed cookies** via `/api/stream-auth` using a private key pulled at runtime from **SSM Parameter Store**.
2. **Browse** â€” `GET /api/videos` â†’ Lambda scans the `svg_videos` table â†’ the SPA renders folder chips and video cards.
3. **Play** â€” the browser requests `/videos/<slug>/index.m3u8`. CloudFront validates the signed cookie **at the edge** (trusted key group) â€” Lambda is not in the playback path at all. hls.js then pulls segments on demand; only the seconds you're watching cross the network.
4. **Upload** â€” the browser asks Lambda to create a multipart upload and gets **presigned part URLs**. 16 MB chunks PUT **directly to S3** from the browser â€” the API never touches video bytes.
5. **Auto-publish** â€” the last presign-complete triggers an async self-invocation of the same Lambda, which streams the original from S3 through a vendored static ffmpeg and writes the catalog entry (detail below).

### Why these AWS decisions (the cert-study version)

| Decision | Why | Alternative rejected |
|---|---|---|
| **CloudFront trusted key groups** for `/videos/*` | Signature validation happens **at the edge**, in every PoP, with zero compute cost | Lambda@Edge / CloudFront Functions â€” extra moving part, extra cost, cold starts in the hot path |
| **Signed cookies, not signed URLs** | One signature covers the whole session â€” every playlist and segment request carries it automatically. No CORS, no token juggling, no per-segment expiry math | Signed URLs â€” 500 of them per video (one per segment), each needing refresh mid-playback |
| **One distribution, three behaviors** | Site + API + video share a domain â†’ cookies "just work", no CORS preflights, one DNS name, one TLS cert | Separate API domain â€” instant CORS tax, cookie SameSite headaches, second cache to tune |
| **S3 multipart as upload state** | `ListMultipartUploads` **is** the staged-files database. Refresh-proof resume for free â€” S3 tracks every part forever | DynamoDB upload-tracking table â€” another thing to keep consistent with reality |
| **fMP4 HLS segments** (`init.mp4` + `seg_*.m4s`) | Solves a real bug: MPEG-TS segments muddy AAC audio after the first segment boundary in hls.js. fMP4 is also what modern iOS expects | MPEG-TS â€” the legacy default |
| **Presigned-URL ffmpeg input in Lambda** | ffmpeg's HTTP backend issues range requests, so recordings with a trailing `moov` atom seek correctly. A stdin pipe cannot seek and fails exactly there â€” learned the hard way | Downloading the whole file to `/tmp` â€” 10 GB ceiling |
| **Streaming segment upload in the packager** | A watcher uploads each finished segment and deletes it locally, so even a 40 GB source only ever occupies a few segments of scratch space | Batch upload after remux â€” hard 10 GB `/tmp` ceiling |
| **Remux, never transcode** | `-c copy` is bit-for-bit identical to the source, runs at disk speed, and is free. MediaConvert quoted $180â€“800 for this library | AWS Elemental MediaConvert â€” re-encodes everything, doubles storage, costs real money |

### The video pipeline

```
 recording.mp4 (H.264/AAC, any size up to 50 GB)
        â”‚
        â–¼
 â”Œâ”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”
 â”‚ LAMBDA PACKAGER (async, 15 min ceiling, 10 GB scratch)  â”‚
 â”‚                                                         â”‚
 â”‚  presigned S3 URL â”€â”€â–¶ ffmpeg -c copy                    â”‚
 â”‚        â”‚                    â”‚                           â”‚
 â”‚        â”‚              index.m3u8 + init.mp4 + seg_*.m4sâ”‚
 â”‚        â”‚                    â”‚                           â”‚
 â”‚        â”‚              watcher: upload + delete locally   â”‚
 â”‚        â”‚                    â”‚                           â”‚
 â”‚  poster frame â—€â”€â”€ init.mp4 + first segment (seekable)   â”‚
 â”‚        â”‚                    â”‚                           â”‚
 â”‚        â””â”€â”€â”€â”€â”€â”€â–¶ DynamoDB catalog entry â”€â”€â–¶ delete sourceâ”‚
 â””â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”˜
        â”‚
        â–¼
 /videos/<slug>/index.m3u8  â€”  live on the archive in minutes
```

Remux vs transcode: `-c copy` never re-encodes a pixel. A 6 GB recording packages in a few minutes, inside Lambda's free tier, and the output is visually identical to the source.

## Repo Map

```
â”œâ”€â”€ site/                  The web app (served by CloudFront)
â”‚   â”œâ”€â”€ index.html         SPA shell: login Â· archive Â· player Â· uploader
â”‚   â”œâ”€â”€ app.js             Routing, HLS playback, resume, uploads, management
â”‚   â”œâ”€â”€ styles.css         Brutalist editorial design system
â”‚   â””â”€â”€ hls.min.js         Vendored hls.js â€” no CDN dependency
â”œâ”€â”€ backend/
â”‚   â”œâ”€â”€ app/handler.py     The entire API: auth, catalog, upload presigning,
â”‚   â”‚                      archive management, and the ffmpeg packager
â”‚   â”œâ”€â”€ deploy.py          Idempotent provisioning: tables, SSM, Lambda, HTTP API, IAM
â”‚   â”œâ”€â”€ sync_cloudfront.py Distribution wiring: 3 origins â†’ 3 behaviors
â”‚   â”œâ”€â”€ deploy_site.py     Site upload + CDN invalidation
â”‚   â”œâ”€â”€ ensure_cors.py     Bucket CORS for browser PUTs
â”‚   â”œâ”€â”€ fetch_ffmpeg.py    Downloads the static ffmpeg vendored into Lambda
â”‚   â”œâ”€â”€ create_user.py     Create a viewer account (PBKDF2, salted)
â”‚   â”œâ”€â”€ list_users.py      List / remove accounts
â”‚   â”œâ”€â”€ seed_catalog.py    Sync local catalog â†’ DynamoDB
â”‚   â”œâ”€â”€ test_manage.py     E2E test of the archive management API
â”‚   â””â”€â”€ e2e_test.py        Whole-stack verification
â”œâ”€â”€ ios/                   Native iOS app â€” SwiftUI + AVPlayer, no Mac needed
â”‚   â”œâ”€â”€ Sources/           Login Â· archive grid Â· cookie-authed player
â”‚   â”œâ”€â”€ deploy.sh          One command: build, sign, install (WSL â†’ iPhone)
â”‚   â”œâ”€â”€ setup-wsl-toolchain.sh   Swift 6.4 + xtool installer
â”‚   â””â”€â”€ check-device.sh    iPhone bridge diagnostics
â”œâ”€â”€ scripts/               Local pipeline (fallback for >50 GB / offline work)
â”‚   â”œâ”€â”€ pipeline.py        Remux + upload in one resumable command
â”‚   â”œâ”€â”€ publish.py         Manual publisher â€” slug-compatible with the cloud
â”‚   â”œâ”€â”€ sign-url.py        Manual signed-URL generator (VLC etc.)
â”‚   â””â”€â”€ lib/               ffmpeg Â· S3 Â· catalog Â· thumbnail helpers
â”œâ”€â”€ site-design/           The design system: tokens, type scale, motion spec
â”œâ”€â”€ docs/aws-setup.md      The full one-time AWS setup walkthrough
â””â”€â”€ config.example.json    Copy â†’ config.json, fill in your values
```

## Getting Started

### Prerequisites

- AWS CLI (`aws configure` done, admin-ish credentials for setup)
- Python 3.12+, `pip install -r scripts/requirements.txt`
- ffmpeg on PATH (`winget install Gyan.FFmpeg`)

### 1. One-time AWS setup (~30 min, $0)

Follow **[docs/aws-setup.md](docs/aws-setup.md)**: private S3 bucket â†’ CloudFront key pair + trusted key group â†’ distribution with OAC â†’ zero-spend billing alarm. Every step has Console *and* CLI paths.

### 2. Configure and deploy

```powershell
copy config.example.json config.json    # bucket name, region, CloudFront key info
python backend\deploy.py                # DynamoDB, SSM, Lambda, HTTP API, IAM
python backend\sync_cloudfront.py       # wire the three behaviors
python backend\ensure_cors.py           # browser PUT permissions
python backend\deploy_site.py           # ship the website
python backend\create_user.py <username> <password>
```

### 3. Use it

- **Website** â€” open the distribution domain, log in, drag videos onto the uploader. They publish themselves.
- **iPhone** â€” `bash ios/deploy.sh` from WSL with the phone plugged in. The toolchain setup is [ios/setup-wsl-toolchain.sh](ios/setup-wsl-toolchain.sh) (one-time; needs an Xcode.xip download and an Apple ID).
- **Huge batches** â€” `python scripts/pipeline.py --source "D:\Recordings\Batch"` from the PC, resumable.

## Operations Cheatsheet

| Task | Command |
|---|---|
| Create a viewer | `python backend\create_user.py <user> <pass>` |
| List / delete viewers | `python backend\list_users.py` / `--delete <user>` |
| Update backend code | `python backend\deploy.py` |
| Update the website | `python backend\deploy_site.py` |
| Verify the whole stack | `python backend\e2e_test.py` |
| Test archive management | `python backend\test_manage.py` |
| Watch packager logs | `aws logs tail /aws/lambda/s3-streaming-api` |
| Manual signed URL (VLC) | `python scripts\sign-url.py <slug>` |

## Cost Breakdown

| Item | Monthly | Notes |
|---|---|---|
| S3 Standard (320 GB, ap-south-1) | ~$8 | The only real cost |
| CloudFront egress | $0 | First 1 TB/month free tier |
| Lambda + API Gateway | ~$0 | Well inside free tier (packager â‰ˆ 300 GB-s per video) |
| DynamoDB | $0 | On-demand, a few hundred reads/day |
| SSM Parameter Store | $0 | One SecureString |
| **Total** | **â‰ˆ $8/month** | Cheaper than one month of any streaming service |

## Security Model

- **S3**: all four Block Public Access settings on. No bucket policy entries for the public. Direct S3 URLs 403.
- **Playback**: `/videos/*` requires CloudFront signed cookies minted by Lambda after authentication. Signature validation happens at the edge via trusted key group; the signing private key lives in SSM Parameter Store, fetched at runtime, never on disk in the repo.
- **Auth**: PBKDF2-SHA256, 200,000 iterations, per-user random salt. Sessions are opaque 256-bit tokens, stored hashed, TTL'd in DynamoDB. Login responses are timing-equalized against unknown usernames.
- **Uploads**: presigned part URLs scoped to `uploads/<username>/...` â€” one user cannot write into another's prefix. Multipart abort requires the same ownership check.
- **Management APIs**: every delete/rename/move validates the session and the key path server-side.

## FAQ

**Why not YouTube unlisted?** ToS risk, ads, zero API for a private client, and your content trains their recommendations. Also â€” building it is the fun part.

**Why not Plex/Jellyfin?** They're great, but they want a machine running 24/7. This architecture has zero servers â€” turn the PC off for a month and playback still works. (Jellyfin + S3 storage doesn't really work; Plex's cloud sync was discontinued.)

**Why HLS instead of progressive MP4?** Instant seeking, chunked delivery (only watched seconds travel), native iOS support, and it's the same delivery model YouTube uses. `faststart` MP4s work but seek noticeably slower on real connections.

**Why build an iOS app from Windows?** [xtool](https://xtool.sh) compiles Swift with a Linux toolchain and signs with Apple's own services â€” no Mac, no paid developer account. The interesting bit was the WSLâ†”iPhone USB bridge (usbipd corrupts large USB transfers; the fix is a TCP forward of Windows' Apple socket â€” see `ios/check-device.sh`).

**What happens to a 40 GB upload?** Same as a 2 GB one: chunks stream to S3, the packager streams segments out as ffmpeg produces them. Scratch space usage stays flat regardless of source size; the only limit is the 15-minute Lambda ceiling, which a 50 GB remux fits inside comfortably.

## Known Limitations

- One video quality (the source's) â€” no ABR ladder. MediaConvert can be added per-upload later if needed.
- Playback position syncs per-device, not across devices.
- Free Apple dev accounts require re-signing the iOS app every 7 days (`bash ios/deploy.sh`).
- The packager publishes H.264/AAC remuxes only; exotic codecs fall back to the local pipeline.

## Roadmap

- [x] **Phase 1** â€” AWS setup + local remux/upload pipeline
- [x] **Phase 2** â€” first videos migrated (Valorant batch, 3.7 GB, 327 segments)
- [x] **Phase 3** â€” backend: login, folder catalog, signed cookie issuing
- [x] **Phase 4** â€” website: brutalist login â†’ folder browse â†’ gallery â†’ HLS player with resume
- [x] **Phase 5** â€” web uploader + zero-touch cloud packaging
- [x] **Phase 6** â€” iOS app via xtool (SwiftUI + AVPlayer, built from Windows)
- [x] **Archive management** â€” delete / rename / re-folder, site + iOS
- [x] **Folder uploads** â€” pick a whole folder; 50 GB per file
- [ ] **Nice-to-haves** â€” cross-device progress sync, ABR ladder, re-sign reminder

## License

[MIT](LICENSE)
