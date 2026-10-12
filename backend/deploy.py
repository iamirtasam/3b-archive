"""Deploy (or update) the backend: tables, role, Lambda, HTTP API.

Usage:
    python backend/deploy.py            # full deploy or in-place update

Everything is idempotent - running it again updates code and configuration.
Requires AWS credentials with permission to create these resources
(`aws configure` already done).

What it creates:
    - DynamoDB tables svg_users / svg_sessions (with TTL) / svg_videos
    - An SSM SecureString parameter holding the CloudFront private key
    - An IAM role for the Lambda (least-privilege, managed logs policy)
    - The Lambda function (Python 3.12, cryptography packaged for Linux)
    - An HTTP API with a $default route straight to the Lambda
"""
from __future__ import annotations

import json
import subprocess
import sys
import time
import zipfile
from pathlib import Path

import boto3
from botocore.exceptions import ClientError

ROOT = Path(__file__).resolve().parent.parent
BUILD = Path(__file__).resolve().parent / "build"

REGION = json.loads((ROOT / "config.json").read_text())["aws"]["region"]
PREFIX = "s3-streaming"
TABLE_PREFIX = "svg_"
FUNCTION_NAME = f"{PREFIX}-api"
API_NAME = f"{PREFIX}-api"
ROLE_NAME = f"{PREFIX}-lambda-role"
SSM_PARAM_NAME = f"/{PREFIX}/cloudfront-private-key"
DOMAIN = json.loads((ROOT / "config.json").read_text())["cloudfront"]["domain"]
KEY_PAIR_ID = json.loads((ROOT / "config.json").read_text())["cloudfront"]["key_pair_id"]
PRIVATE_KEY_PATH = Path(json.loads((ROOT / "config.json").read_text())["cloudfront"]["private_key_path"])
BUCKET = json.loads((ROOT / "config.json").read_text())["s3"]["bucket"]


def main() -> None:
    session = boto3.session.Session(region_name=REGION)
    print(f"Region: {REGION}")
    print(f"Domain: {DOMAIN}")

    create_tables(session)
    put_private_key(session)
    role_arn = ensure_role(session)
    zip_path = build_lambda_zip()
    ensure_function(session, role_arn, zip_path)
    api_endpoint = ensure_api(session)
    (Path(__file__).parent / "api_endpoint.json").write_text(
        json.dumps({"endpoint": api_endpoint}, indent=2) + "\n", encoding="utf-8"
    )

    print()
    print("=" * 60)
    print("Deploy complete.")
    print(f"  API endpoint : {api_endpoint}")
    print(f"  Health check : {api_endpoint}api/health")
    print()
    print("Next steps:")
    print(f"  python backend/create_user.py <username> <password>")
    print(f"  python backend/seed_catalog.py")
    print(f"  python backend/e2e_test.py    (after creating a user)")


# ------------------------------------------------------------------ DynamoDB

def create_tables(session: boto3.session.Session) -> None:
    ddb = session.client("dynamodb")
    tables = {
        f"{TABLE_PREFIX}users": ("username", None),
        f"{TABLE_PREFIX}sessions": ("token_hash", "expires"),
        f"{TABLE_PREFIX}videos": ("slug", None),
    }
    for name, (hash_key, ttl_attr) in tables.items():
        try:
            ddb.describe_table(TableName=name)
            print(f"Table exists: {name}")
            continue
        except ClientError as err:
            if err.response["Error"]["Code"] != "ResourceNotFoundException":
                raise
        ddb.create_table(
            TableName=name,
            AttributeDefinitions=[
                {"AttributeName": hash_key, "AttributeType": "S"}
            ],
            KeySchema=[{"AttributeName": hash_key, "KeyType": "HASH"}],
            BillingMode="PAY_PER_REQUEST",
        )
        waiter = ddb.get_waiter("table_exists")
        waiter.wait(TableName=name, WaiterConfig={"Delay": 2, "MaxAttempts": 30})
        print(f"Table created: {name}")
        if ttl_attr:
            ddb.update_time_to_live(
                TableName=name,
                TimeToLiveSpecification={"Enabled": True, "AttributeName": ttl_attr},
            )
            print(f"  TTL enabled on: {ttl_attr}")


# ----------------------------------------------------------------------- SSM

def put_private_key(session: boto3.session.Session) -> None:
    ssm = session.client("ssm")
    pem = PRIVATE_KEY_PATH.read_text(encoding="utf-8")
    try:
        existing = ssm.get_parameter(Name=SSM_PARAM_NAME, WithDecryption=True)
        if existing["Parameter"]["Value"] == pem:
            print(f"SSM parameter up to date: {SSM_PARAM_NAME}")
            return
        ssm.put_parameter(Name=SSM_PARAM_NAME, Value=pem, Type="SecureString", Overwrite=True)
        print(f"SSM parameter updated: {SSM_PARAM_NAME}")
    except ClientError as err:
        if err.response["Error"]["Code"] != "ParameterNotFound":
            raise
        ssm.put_parameter(Name=SSM_PARAM_NAME, Value=pem, Type="SecureString")
        print(f"SSM parameter created: {SSM_PARAM_NAME}")


# ----------------------------------------------------------------------- IAM

def ensure_role(session: boto3.session.Session) -> str:
    iam = session.client("iam")
    account = boto3.client("sts").get_caller_identity()["Account"]
    assume = {
        "Version": "2012-10-17",
        "Statement": [{
            "Effect": "Allow",
            "Principal": {"Service": "lambda.amazonaws.com"},
            "Action": "sts:AssumeRole",
        }],
    }
    try:
        role = iam.get_role(RoleName=ROLE_NAME)["Role"]
        print(f"IAM role exists: {ROLE_NAME}")
    except ClientError as err:
        if err.response["Error"]["Code"] != "NoSuchEntity":
            raise
        role = iam.create_role(
            RoleName=ROLE_NAME,
            AssumeRolePolicyDocument=json.dumps(assume),
            Description="Execution role for the s3-streaming API Lambda",
        )
        print(f"IAM role created: {ROLE_NAME}")
        time.sleep(8)  # propagation before the role can be assumed

    table_arns = [
        f"arn:aws:dynamodb:{REGION}:{account}:table/{TABLE_PREFIX}*"
    ]
    iam.put_role_policy(
        RoleName=ROLE_NAME,
        PolicyName=f"{PREFIX}-permissions",
        PolicyDocument=json.dumps({
            "Version": "2012-10-17",
            "Statement": [
                {
                    "Effect": "Allow",
                    "Action": [
                        "dynamodb:GetItem", "dynamodb:PutItem",
                        "dynamodb:DeleteItem", "dynamodb:Query",
                        "dynamodb:Scan", "dynamodb:UpdateItem",
                    ],
                    "Resource": table_arns,
                },
                {
                    "Effect": "Allow",
                    "Action": "ssm:GetParameter",
                    "Resource": f"arn:aws:ssm:{REGION}:{account}:parameter{SSM_PARAM_NAME}",
                },
                {
                    # Presigning itself needs no permission; these allow the
                    # Lambda to create/list/abort multipart uploads so the
                    # browser can stream files directly to S3.
                    "Effect": "Allow",
                    "Action": [
                        "s3:ListBucketMultipartUploads",
                        "s3:AbortMultipartUpload",
                        "s3:ListMultipartUploadParts",
                    ],
                    "Resource": [
                        f"arn:aws:s3:::{BUCKET}",
                        f"arn:aws:s3:::{BUCKET}/uploads/*",
                    ],
                },
            ],
        }),
    )
    try:
        iam.attach_role_policy(
            RoleName=ROLE_NAME,
            PolicyArn="arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole",
        )
    except ClientError as err:
        if err.response["Error"]["Code"] != "EntityAlreadyExists":
            raise
    print("IAM policies attached (DynamoDB, SSM, CloudWatch logs)")
    return role["Arn"]


# -------------------------------------------------------------------- Lambda

def build_lambda_zip() -> Path:
    """Package handler + Linux cryptography wheel into build/function.zip."""
    package_dir = BUILD / "package"
    app_dir = BUILD / "app"
    zip_path = BUILD / "function.zip"

    if not (package_dir / "cryptography").exists():
        package_dir.mkdir(parents=True, exist_ok=True)
        print("Installing cryptography for Linux (Lambda runtime)...")
        subprocess.run(
            [
                sys.executable, "-m", "pip", "install", "--quiet",
                "--platform", "manylinux2014_x86_64",
                "--implementation", "cp", "--python-version", "3.12",
                "--only-binary=:all:", "--target", str(package_dir),
                "cryptography",
            ],
            check=True,
        )

    app_dir.mkdir(parents=True, exist_ok=True)
    (app_dir / "__init__.py").write_text("", encoding="utf-8")
    (app_dir / "handler.py").write_text(
        (Path(__file__).parent / "app" / "handler.py").read_text(encoding="utf-8"),
        encoding="utf-8",
    )

    print(f"Building {zip_path} ...")
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for path in sorted(package_dir.rglob("*")):
            if path.is_file():
                zf.write(path, path.relative_to(package_dir))
        for path in sorted(app_dir.rglob("*")):
            if path.is_file():
                zf.write(path, path.relative_to(BUILD))
    print(f"Lambda package: {zip_path.stat().st_size / 1e6:.1f} MB")
    return zip_path


def ensure_function(
    session: boto3.session.Session, role_arn: str, zip_path: Path
) -> None:
    lam = session.client("lambda")
    env = {
        "Variables": {
            "DOMAIN": DOMAIN,
            "KEY_PAIR_ID": KEY_PAIR_ID,
            "SSM_PARAM": SSM_PARAM_NAME,
            "TABLE_PREFIX": TABLE_PREFIX,
            "BUCKET": BUCKET,
        }
    }
    try:
        lam.get_function(FunctionName=FUNCTION_NAME)
        print(f"Updating Lambda: {FUNCTION_NAME}")
        lam.update_function_code(
            FunctionName=FUNCTION_NAME, ZipFile=zip_path.read_bytes()
        )
        waiter = lam.get_waiter("function_updated")
        waiter.wait(FunctionName=FUNCTION_NAME)
        lam.update_function_configuration(
            FunctionName=FUNCTION_NAME,
            Runtime="python3.12",
            Timeout=15,
            MemorySize=256,
            Environment=env,
        )
        waiter.wait(FunctionName=FUNCTION_NAME)
    except ClientError as err:
        if err.response["Error"]["Code"] not in ("ResourceNotFoundException", "ResourceNotFound"):
            raise
        print(f"Creating Lambda: {FUNCTION_NAME}")
        lam.create_function(
            FunctionName=FUNCTION_NAME,
            Runtime="python3.12",
            Role=role_arn,
            Handler="app.handler.lambda_handler",
            Code={"ZipFile": zip_path.read_bytes()},
            Timeout=15,
            MemorySize=256,
            Environment=env,
        )
        waiter = lam.get_waiter("function_active_v2")
        waiter.wait(FunctionName=FUNCTION_NAME)
    print("Lambda ready.")


# ----------------------------------------------------------------- HTTP API

def ensure_api(session: boto3.session.Session) -> str:
    lam = session.client("lambda")
    apigw = session.client("apigatewayv2")
    fn_arn = lam.get_function(FunctionName=FUNCTION_NAME)["Configuration"]["FunctionArn"]

    existing = [a for a in apigw.get_apis()["Items"] if a.get("Name") == API_NAME]
    if existing:
        api = existing[0]
        print(f"HTTP API exists: {api['ApiId']}")
    else:
        api = apigw.create_api(
            Name=API_NAME,
            ProtocolType="HTTP",
            Target=fn_arn,  # quick-create: $default route + integration + permission
            CorsConfiguration={
                "AllowCredentials": True,
                "AllowHeaders": ["content-type"],
                "AllowMethods": ["GET", "POST", "OPTIONS"],
                "AllowOrigins": ["http://localhost:8000", "http://127.0.0.1:8000"],
                "MaxAge": 86400,
            },
        )
        print(f"HTTP API created: {api['ApiId']}")

    endpoint = f"https://{api['ApiId']}.execute-api.{REGION}.amazonaws.com"

    # Re-assert the invoke permission (harmless if it already exists).
    try:
        lam.add_permission(
            FunctionName=FUNCTION_NAME,
            StatementId="apigw-invoke",
            Action="lambda:InvokeFunction",
            Principal="apigateway.amazonaws.com",
            SourceArn=f"arn:aws:execute-api:{REGION}:{fn_arn.split(':')[4]}:{api['ApiId']}/*",
        )
    except ClientError as err:
        if "ResourceConflictException" not in str(err):
            raise

    return f"{endpoint}/"


if __name__ == "__main__":
    main()
