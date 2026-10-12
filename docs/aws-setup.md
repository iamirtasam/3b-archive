# Phase 1 — AWS setup (S3 + CloudFront)

This walks you through the one-time cloud setup. Everything is clickable in the AWS Console, and the AWS CLI commands are given as the fast alternative. Total time: about 30 minutes.

## 0. Prerequisites

- AWS CLI installed — `aws --version` should print 2.x
- Run `aws configure` and enter:
  - **AWS Access Key ID / Secret** — create one in the Console under *IAM → Users → your user → Security credentials → Create access key* (use the "Command line interface (CLI)" case)
  - **Region** — `ap-south-1` (Mumbai) or your preferred region

## 1. Create the bucket

> Pick a globally unique name — bucket names are shared across all of AWS. The examples use `my-video-archive`; replace it everywhere.

**Console:** S3 → *Create bucket* → name it → Region: *Asia Pacific (Mumbai)* → **Block Public Access: keep ALL boxes checked** (the bucket must be fully private — CloudFront gets access separately) → Enable *Bucket Versioning* off → Default encryption: SSE-S3 → Create.

**CLI:**

```bash
aws s3 mb s3://my-video-archive --region ap-south-1
aws s3api put-public-access-block \
  --bucket my-video-archive \
  --public-access-block-configuration \
  BlockPublicAcls=true,IgnorePublicAcls=true,BlockPublicPolicy=true,RestrictPublicBuckets=true
```

## 2. Create a CloudFront key group (for signed playback)

Signed URLs are what stop strangers from watching your private videos.

```bash
# Generate a key pair locally (one time)
openssl genrsa -out cloudfront-private-key.pem 2048
openssl rsa -pubout -in cloudfront-private-key.pem -out cloudfront-public-key.pem
```

**Console:** CloudFront → *Key management → Public keys* → *Add public key* → paste the contents of `cloudfront-public-key.pem` (the `-----BEGIN PUBLIC KEY-----` lines included) → copy the generated **key pair ID**.

Then *Key groups* → *Create key group* → add that public key → note the **key group ID**.

> Keep `cloudfront-private-key.pem` safe and never commit it — it is already in `.gitignore`.

## 3. Create the CloudFront distribution

**Console:** CloudFront → *Create distribution*:

| Setting | Value |
| --- | --- |
| Origin domain | your S3 bucket (the dropdown entry ending in `.s3.ap-south-1.amazonaws.com`) |
| Origin access | **Origin access control settings** → create a new OAC (S3) |
| Viewer protocol policy | **Redirect HTTP to HTTPS** |
| Allowed HTTP methods | GET, HEAD |
| Restrict viewer access | **Yes — trusted behaviors, trusted key groups = the one from step 2** |
| Cache policy | CachingOptimized |
| Alternate domain name | none for now (you can add your own domain later) |

When the distribution is created, the console offers a **"Copy policy"** button to update your S3 bucket policy — click it, then paste it into S3 → your bucket → *Permissions → Bucket policy*. This grants CloudFront's OAC read access while the bucket stays otherwise private.

**CLI equivalent (summary):** create an origin access control, then the distribution with that OAC and the key group, then attach the generated bucket policy. Honestly, this one is easier in the console the first time.

Note your **distribution domain name**: `d1234abcd.cloudfront.net`.

## 4. Verify the privacy works

```bash
# S3 direct access: should be denied (403)
aws s3 cp s3://my-video-archive/videos/ ./probe/ --no-sign-request 2>&1 | head -1

# CloudFront without a signature: should be denied (403 MissingKey)
curl -i "https://d1234abcd.cloudfront.net/videos/anything/index.m3u8" | head -3
```

Both should refuse access — that is exactly what we want. Only URLs signed with your private key will play. Phase 3 (the backend) will generate those signatures automatically after login; for a quick manual smoke test you can sign a URL with a tiny Python snippet (ask me — I'll add a `scripts/sign-url.py` utility when we get there).

## 5. Set a billing alarm (2 minutes, do not skip)

Console → *Billing → Budgets → Create budget → Zero spend budget*. With a zero-spend alarm you get an email the moment anything unexpected would cost money — nice peace of mind while you are learning the ropes.

## Cost recap

| Item | Cost |
| --- | --- |
| S3 storage (500 GB) | ≈ $11.50 / month |
| CloudFront egress | $0 up to 1 TB / month |
| CloudFront key groups, OAC | Free |
| This whole setup | One-time, $0 |

## What's next

- **Phase 2** — bulk-upload your library: `python pipeline.py` (see `scripts/README.md`)
- **Phase 3** — a tiny backend (Lambda + DynamoDB) for login, folders, and signed URL generation
- **Phase 4** — the website (folder browse → gallery → HLS player)
- **Phase 5** — the iOS app via xtool (AVPlayer plays HLS natively)
