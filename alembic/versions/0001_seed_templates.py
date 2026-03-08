"""seed job templates

Revision ID: 0001_seed_templates
Revises:
Create Date: 2026-03-08

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0001_seed_templates"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


TEMPLATES = [
    {
        "name": "Health Check (HTTP)",
        "description": "Ping a URL and check for a 200 response. Set TARGET_URL in env vars.",
        "category": "monitoring",
        "script_type": "python",
        "default_cron": "*/5 * * * *",
        "script_content": '''"""Health check — pings TARGET_URL and exits non-zero on failure."""
import os
import sys
import urllib.request

url = os.environ.get("TARGET_URL", "https://example.com")
print(f"Checking {url} ...")

try:
    req = urllib.request.Request(url, method="GET")
    with urllib.request.urlopen(req, timeout=30) as resp:
        status = resp.getcode()
        print(f"Status: {status}")
        if status != 200:
            print(f"FAIL: expected 200, got {status}")
            sys.exit(1)
except Exception as e:
    print(f"FAIL: {e}")
    sys.exit(1)

print("OK")
''',
    },
    {
        "name": "Database Backup (pg_dump)",
        "description": "Dump a PostgreSQL database to a timestamped file. Set PG_HOST, PG_USER, PG_DB, BACKUP_DIR in env vars.",
        "category": "backup",
        "script_type": "bash",
        "default_cron": "0 2 * * *",
        "script_content": """#!/bin/bash
# PostgreSQL backup script
set -euo pipefail

TIMESTAMP=$(date +%Y%m%d_%H%M%S)
BACKUP_DIR="${BACKUP_DIR:-/tmp/backups}"
FILENAME="${PG_DB:-mydb}_${TIMESTAMP}.sql.gz"

mkdir -p "$BACKUP_DIR"

echo "Backing up ${PG_DB:-mydb} to ${BACKUP_DIR}/${FILENAME} ..."
PGPASSWORD="${PG_PASSWORD:-}" pg_dump \\
    -h "${PG_HOST:-localhost}" \\
    -U "${PG_USER:-postgres}" \\
    "${PG_DB:-mydb}" | gzip > "${BACKUP_DIR}/${FILENAME}"

echo "Backup complete: $(du -h "${BACKUP_DIR}/${FILENAME}" | cut -f1)"

# Clean up backups older than 7 days
find "$BACKUP_DIR" -name "*.sql.gz" -mtime +7 -delete
echo "Old backups cleaned up"
""",
    },
    {
        "name": "Disk Space Alert",
        "description": "Check disk usage and exit with error if above threshold. Set THRESHOLD_PERCENT (default 85).",
        "category": "monitoring",
        "script_type": "python",
        "default_cron": "0 * * * *",
        "script_content": '''"""Check disk usage and alert if above threshold."""
import os
import shutil

threshold = int(os.environ.get("THRESHOLD_PERCENT", "85"))
total, used, free = shutil.disk_usage("/")

percent_used = (used / total) * 100
print(f"Disk usage: {percent_used:.1f}% ({used // (1024**3)} GB used / {total // (1024**3)} GB total)")
print(f"Free: {free // (1024**3)} GB")

if percent_used > threshold:
    print(f"ALERT: Disk usage {percent_used:.1f}% exceeds threshold {threshold}%")
    exit(1)

print(f"OK: Under {threshold}% threshold")
''',
    },
    {
        "name": "Send Slack Message",
        "description": "Post a message to a Slack channel via webhook. Set SLACK_WEBHOOK_URL and MESSAGE in env vars.",
        "category": "notification",
        "script_type": "python",
        "default_cron": "0 9 * * 1",
        "script_content": '''"""Send a message to Slack via incoming webhook."""
import json
import os
import sys
import urllib.request

webhook_url = os.environ.get("SLACK_WEBHOOK_URL", "")
message = os.environ.get("MESSAGE", "Hello from sCron!")

if not webhook_url:
    print("ERROR: SLACK_WEBHOOK_URL not set")
    sys.exit(1)

payload = json.dumps({"text": message}).encode("utf-8")
req = urllib.request.Request(
    webhook_url, data=payload,
    headers={"Content-Type": "application/json"},
)

try:
    with urllib.request.urlopen(req, timeout=10) as resp:
        print(f"Sent! Status: {resp.getcode()}")
except Exception as e:
    print(f"Failed to send: {e}")
    sys.exit(1)
''',
    },
    {
        "name": "Clean Old Files",
        "description": "Delete files older than N days from a directory. Set CLEAN_DIR, MAX_AGE_DAYS, FILE_PATTERN in env vars.",
        "category": "maintenance",
        "script_type": "bash",
        "default_cron": "0 3 * * 0",
        "script_content": """#!/bin/bash
# Clean files older than MAX_AGE_DAYS from CLEAN_DIR
set -euo pipefail

DIR="${CLEAN_DIR:-/tmp/cleanup}"
DAYS="${MAX_AGE_DAYS:-30}"
PATTERN="${FILE_PATTERN:-*}"

if [ ! -d "$DIR" ]; then
    echo "Directory $DIR does not exist"
    exit 1
fi

echo "Cleaning files matching '$PATTERN' older than $DAYS days in $DIR ..."
COUNT=$(find "$DIR" -name "$PATTERN" -type f -mtime +$DAYS | wc -l)
find "$DIR" -name "$PATTERN" -type f -mtime +$DAYS -delete
echo "Deleted $COUNT files"
""",
    },
    {
        "name": "Python Script Runner",
        "description": "Blank Python template with logging and error handling.",
        "category": "general",
        "script_type": "python",
        "default_cron": "0 * * * *",
        "script_content": '''"""sCron Python job template."""
import os
import sys
from datetime import datetime

def main():
    print(f"Job started at {datetime.utcnow().isoformat()}")

    # Your logic here
    print("Hello from sCron!")

    print(f"Job finished at {datetime.utcnow().isoformat()}")

if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)
''',
    },
]


def upgrade() -> None:
    # Seed the job_templates table with default templates
    templates_table = sa.table(
        "job_templates",
        sa.column("name", sa.String),
        sa.column("description", sa.Text),
        sa.column("category", sa.String),
        sa.column("script_content", sa.Text),
        sa.column("script_type", sa.String),
        sa.column("default_cron", sa.String),
        sa.column("user_id", sa.Integer),
    )
    op.bulk_insert(
        templates_table,
        [
            {
                "name": t["name"],
                "description": t["description"],
                "category": t["category"],
                "script_content": t["script_content"],
                "script_type": t["script_type"],
                "default_cron": t["default_cron"],
                "user_id": None,
            }
            for t in TEMPLATES
        ],
    )


def downgrade() -> None:
    # Remove seeded templates (user_id IS NULL = system templates)
    op.execute("DELETE FROM job_templates WHERE user_id IS NULL")
