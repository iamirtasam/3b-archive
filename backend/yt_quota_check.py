"""Check today's YouTube upload quota usage via the Lambda logs."""
import json
from pathlib import Path

import boto3
from datetime import datetime, timedelta, timezone

CFG = json.loads((Path(__file__).resolve().parent.parent / "config.json").read_text())
logs = boto3.client("logs", region_name=CFG["aws"]["region"])
start = int((datetime.now(timezone.utc) -
             timedelta(hours=24)).timestamp() * 1000)
ev = logs.filter_log_events(
    logGroupName="/aws/lambda/s3-streaming-api",
    startTime=start,
    filterPattern="?\"YT pushed\" ?\"uploadLimitExceeded\"")["events"]
pushed = [e["message"] for e in ev if "YT pushed" in e["message"]]
quota = [e["message"] for e in ev if "uploadLimitExceeded" in e["message"]]
print(f"pushed today: {len(pushed)}")
print(f"quota-stopped attempts: {len(quota)}")
for m in pushed[-5:]:
    print(" ", m[:120])
