import os
import zipfile
import smtplib
import requests
from pathlib import Path
from email import encoders
from email.mime.base import MIMEBase
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
import time
import json
from dotenv import load_dotenv

# ── Load environment variables ──────────────────────────────────────────────
load_dotenv()

BASE        = os.getenv("API_BASE")
TOKEN       = os.getenv("TOKEN")
AGENT_ID    = os.getenv("AGENT_ID")

GMAIL_USER  = os.getenv("GMAIL_USER")       # your Gmail address
GMAIL_PASS  = os.getenv("GMAIL_PASS")       # Gmail App Password (not your login password)
EMAIL_TO    = os.getenv("EMAIL_TO")         # recipient address
EMAIL_CC    = os.getenv("EMAIL_CC", "")     # optional CC, comma-separated

HEADERS = {"Authorization": f"Bearer {TOKEN}"}

# ── Step 1: Get the latest execution ID ─────────────────────────────────────
def get_latest_execution_id() -> str:
    print("📡 Fetching latest execution ID...")
    resp = requests.get(
        f"{BASE}/executions",
        headers=HEADERS,
        params={"agent_id": AGENT_ID},
    )
    resp.raise_for_status()
    items = resp.json().get("items", [])
    if not items:
        raise ValueError("No executions found for this agent.")
    # execution_id = items[0].get("id").replace("-", "")    sometimes it work
    execution_id = items[0].get("id") # majorly this 
    print(f"   ✅ Latest execution ID: {execution_id}")
    return execution_id


# ── Step 2: Get execution metadata ──────────────────────────────────────────
def get_execution_metadata(execution_id: str) -> dict:
    print("📋 Fetching execution metadata...")
    resp = requests.get(
        f"{BASE}/agents/{AGENT_ID}/runtime/proxy/v1/executions/{execution_id}",
        headers=HEADERS,
    )
    resp.raise_for_status()
    data = resp.json()
    files = data.get("files", [])
    print(f"   ✅ Found {len(files)} artifact(s) in the execution.")
    return data


# ── Step 3: Download artifacts zip ──────────────────────────────────────────
def download_artifacts_zip(execution_id: str) -> Path:
    print("📦 Downloading artifacts zip...")
    resp = requests.get(
        f"{BASE}/agents/{AGENT_ID}/runtime/executions/{execution_id}/artifacts.zip",
        headers=HEADERS,
    )
    resp.raise_for_status()

    zip_path = Path(f"execution-{execution_id}-artifacts.zip")
    zip_path.write_bytes(resp.content)
    print(f"   ✅ Saved zip: {zip_path} ({zip_path.stat().st_size:,} bytes)")
    return zip_path


# ── Step 4: Extract zip ──────────────────────────────────────────────────────
def extract_zip(zip_path: Path) -> tuple[Path, list[Path]]:
    extract_dir = Path(f"extracted-{zip_path.stem}")
    extract_dir.mkdir(exist_ok=True)

    print(f"📂 Extracting to: {extract_dir}/")
    with zipfile.ZipFile(zip_path, "r") as zf:
        zf.extractall(extract_dir)

    extracted_files = [p for p in extract_dir.rglob("*") if p.is_file()]
    print(f"   ✅ Extracted {len(extracted_files)} file(s):")
    for f in extracted_files:
        print(f"      • {f.relative_to(extract_dir)}")

    return extract_dir, extracted_files


# ── Step 5: Send email with attachments ─────────────────────────────────────
def send_email(execution_id: str, extracted_files: list[Path]) -> None:
    print("✉️  Preparing email...")

    msg = MIMEMultipart()
    msg["From"]    = GMAIL_USER
    msg["To"]      = EMAIL_TO
    msg["Subject"] = f"Execution Artifacts — {execution_id}"

    if EMAIL_CC:
        msg["Cc"] = EMAIL_CC

    body = (
        f"Hi,\n\n"
        f"Please find attached the artifact files from execution ID:\n"
        f"  {execution_id}\n\n"
        f"Files included ({len(extracted_files)}):\n"
        + "\n".join(f"  • {f.name}" for f in extracted_files)
        + "\n\nThis email was generated automatically.\n"
    )
    msg.attach(MIMEText(body, "plain"))

    for file_path in extracted_files:
        with open(file_path, "rb") as fp:
            part = MIMEBase("application", "octet-stream")
            part.set_payload(fp.read())
        encoders.encode_base64(part)
        part.add_header(
            "Content-Disposition",
            f"attachment; filename={file_path.name}",
        )
        msg.attach(part)
        print(f"   📎 Attached: {file_path.name}")

    # Build recipient list (To + CC)
    recipients = [EMAIL_TO]
    if EMAIL_CC:
        recipients += [addr.strip() for addr in EMAIL_CC.split(",") if addr.strip()]

    print("📤 Connecting to Gmail SMTP...")
    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
        server.login(GMAIL_USER, GMAIL_PASS)
        server.sendmail(GMAIL_USER, recipients, msg.as_string())

    print(f"   ✅ Email sent to: {EMAIL_TO}" + (f" (CC: {EMAIL_CC})" if EMAIL_CC else ""))


STATE_FILE = "pipeline__status.json"
POLL_INTERVAL = 60
MAX_DURATION = 3600

def load_state():
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE, "r") as f:
            return json.load(f)
    return {"last_processed_id":None}

def save_state(state):
    with open(STATE_FILE, "w") as f:
        json.dump(state, f)

def run_pipeline(execution_id):
    print("="*55)
    print(f"Artifact Pipeline: Fetch -> Download -> Email")
    print(f"Exectuion ID: {execution_id}")
    print("="*55)

    _metadata = get_execution_metadata(execution_id)
    zip_path = download_artifacts_zip(execution_id)
    _dir , files = extract_zip(zip_path)
    send_email(execution_id, files)
    print()
    print("Done ! All artifacts sent successfully")

# ── Main ─────────────────────────────────────────────────────────────────────
def main():
    state      = load_state()
    start_time = time.time()
    elapsed    = 0

    print(f"Polling every {POLL_INTERVAL}s for up to {MAX_DURATION // 60} minutes...")
    print(f"Last processed ID: {state['last_processed_id']}")

    while elapsed < MAX_DURATION:
        execution_id = get_latest_execution_id()

        if execution_id != state["last_processed_id"]:
            print(f"\n[{time.strftime('%H:%M:%S')}] New execution detected: {execution_id}")
            run_pipeline(execution_id)
            state["last_processed_id"] = execution_id
            save_state(state)
        else:
            print(f"[{time.strftime('%H:%M:%S')}] No new execution (ID: {execution_id}) — skipping.")

        time.sleep(POLL_INTERVAL)
        elapsed = time.time() - start_time

    print("\nPolling window (1 hr) complete. Exiting.")


if __name__ == "__main__":
    main()
