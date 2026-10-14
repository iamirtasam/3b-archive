# AWS Setup — S3 + CloudFront + Lambda

The complete one-time cloud setup behind 3B Archive. Everything is doable in
the Console (click paths given) or the CLI (commands given). Total time:
about 30 minutes. Total cost: $0 — this only creates free-tier resources.

For the *why* behind each choice, see the [README's architecture
section](../README.md#why-these-aws-decisions-the-cert-study-version).

## 0. Prerequisites

- AWS CLI 2.x — `aws --version`
- `aws configure` with an IAM user access key (create under
  *IAM → Users → Security credentials → Create access key*, CLI case)
- Region: `ap-south-1` (Mumbai) used here — pick yours and stay consistent

## 1. Private bucket

> Bucket names are globally unique. Examples use `my-video-archive`.

**Console:** S3 → *Create bucket* → Region → **Block Public Access: ALL four
boxes checked** (CloudFront gets access via OAC separately, not by making
the bucket public) → versioning off → SSE-S3 encryption → Create.

**CLI:**

```bash
aws s3 mb s3://my-video-archive --region ap-south-1
aws s3api put-public-access-block --bucket my-video-archive \
  --public-access-block-configuration \
  BlockPublicAcls=true,IgnorePublicAcls=true,BlockPublicPolicy=true,RestrictPublicBuckets=true
```

## 2. CloudFront signing key pair

This is what makes playback private: only requests carrying a signature made
with your private key pass the edge.

```bash
openssl genrsa -out cloudfront-private-key.pem 2048
openssl rsa -pubout -in cloudfront-private-key.pem -out cloudfront-public-key.pem
```

**Console:** CloudFront → *Key management → Public keys → Add public key*
→ paste `cloudfront-public-key.pem` (headers included) → note the generated
**key pair ID**. Then *Key groups → Create key group* with that key → note
the **key group ID**.

> The private key never goes to AWS except (later) into SSM Parameter Store
> so the Lambda can sign cookies. Never commit it — it's gitignored.

## 3. Distribution with Origin Access Control

**Console:** CloudFront → *Create distribution*:

| Setting | Value |
|---|---|
| Origin | your bucket's `.s3.ap-south-1.amazonaws.com` endpoint |
| Origin access | **Origin access control** (OAC, S3 type) |
| Viewer protocol | Redirect HTTP → HTTPS |
| Methods | GET, HEAD |
| Restrict viewer access | **Yes — trusted key group from step 2** |
| Cache policy | CachingOptimized |

On creation, the console offers **"Copy policy"** — paste it into the
bucket's policy so OAC can read. Note the distribution domain
(`xxxx.cloudfront.net`).

### The two extra behaviors (what makes this one domain)

The deploy script (`backend/sync_cloudfront.py`) wires all three; the shape:

| Behavior | Path | Origin | Cache policy | Viewer access |
|---|---|---|---|---|
| default | `/*` | S3 (site files) | CachingOptimized | public |
| 2 | `/api/*` | API Gateway | CachingDisabled | public (auth in Lambda) |
| 3 | `/videos/*` | S3 (videos) | CachingOptimized | **trusted key group** |

Free-plan gotcha learned here: **custom cache policies are a paid feature**
— this stack uses only AWS managed policies
(`CachingOptimized`/`CachingDisabled` + `AllViewerExceptHostHeader` origin
request policy so signed cookies reach the origin).

## 4. Backend resources

`python backend/deploy.py` creates everything idempotently:

| Resource | Purpose | Notes |
|---|---|---|
| `svg_users` | accounts (PBKDF2 hash + salt) | |
| `svg_sessions` | opaque session tokens | TTL attribute `expires`, DynamoDB auto-purges |
| `svg_videos` | the catalog (slug → title/folder/duration/…) | |
| SSM `/s3-streaming/cloudfront-private-key` | signing key at runtime | SecureString |
| Lambda `s3-streaming-api` | the whole API + packager | python3.12, 1024 MB, 15 min, 10 GB scratch, vendored ffmpeg via `backend/fetch_ffmpeg.py` |
| HTTP API | `$default` route → Lambda | free tier: 1 M requests/month |

IAM for the function (least privilege): DynamoDB CRUD on `svg_*`, `ssm:GetParameter`, S3 read/write on `uploads/*` and `videos/*` + `ListBucket`, and `lambda:InvokeFunction` on itself (the async packager kick-off).

## 5. Verify privacy actually works

```bash
# direct S3: denied
aws s3 cp s3://my-video-archive/videos/ ./probe/ --no-sign-request 2>&1 | head -1

# through CloudFront without a signature: denied
curl -i "https://xxxx.cloudfront.net/videos/anything/index.m3u8" | head -3
```

Both must 403. Only signed-cookie requests (post-login) should play.

## 6. Billing alarm — do not skip

Console → *Billing → Budgets → Create budget → **Zero spend budget*** with
your email. The entire architecture runs in the free tier; this emails you
the instant anything would cost a cent.

## Cost recap (steady state)

| Item | Cost |
|---|---|
| Setup (all of the above) | one-time, $0 |
| Key groups, OAC, behaviors | $0 |
| Storage (320 GB S3) | ≈ $8/month — the only real cost |

## What's next

- `python backend/create_user.py <user> <pass>` — first account
- Open the distribution domain, log in, drag videos into the uploader
- `python backend/e2e_test.py` — verify the full stack
