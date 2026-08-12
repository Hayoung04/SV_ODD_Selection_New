"""
로컬 Motional 파일들을 Kafka 토픽(odd-tagging.input)으로 발행하는 Producer.
신규 파일만 자동 감지해서 발행하는 기능 포함 (cron으로 주기 실행 권장).

ODD 서비스는 jobId가 자기 시스템에서 발급한 값과 일치할 때만 반응하므로,
우리가 명확히 구분되는 jobId(motional-verify- 접두사)를 사용하면
ODD 서비스는 이 메시지를 무시하고 우리 쪽 Consumer만 처리하게 된다.

사용법:
    python3 motional_producer.py              # 신규 파일만 자동 발행 (기본, 안전)
    python3 motional_producer.py --all         # 전체 재발행 (상태 무시)
    python3 motional_producer.py --one         # 신규 파일 중 1건만 발행 (수동 검증용)
    python3 motional_producer.py --dry-run     # 발행 안 하고 대상 목록만 출력
"""
import json
import os
import sys
import glob
from datetime import datetime, timezone
from confluent_kafka import Producer
from aws_msk_iam_sasl_signer import MSKAuthTokenProvider


def load_dotenv(path):
    if not os.path.exists(path):
        return
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key, value = key.strip(), value.strip()
            if key and key not in os.environ:
                os.environ[key] = value


SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(SCRIPT_DIR, ".env"))

BROKER = os.environ.get("KAFKA_BROKER")
REGION = os.environ.get("KAFKA_REGION")
os.environ["AWS_PROFILE"] = os.environ.get("AWS_PROFILE", "odd-msk")

TOPIC = "dev.shared.odd-tagging.input"
MOTIONAL_DIR = "/home/odd-selection/machet18_data/motional"
JOBID_PREFIX = "motional-verify-"
STATE_FILE = os.path.join(SCRIPT_DIR, "motional_published_state.json")
LOG_FILE = os.path.join(SCRIPT_DIR, "motional_producer.log")


def log(msg):
    line = f"[{datetime.now(timezone.utc).isoformat()}] {msg}"
    print(line)
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def load_state():
    if not os.path.exists(STATE_FILE):
        return {"published": {}, "next_seq": 1}
    with open(STATE_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def save_state(state):
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


def oauth_cb(oauth_config):
    token, expiry_ms = MSKAuthTokenProvider.generate_auth_token(REGION)
    return token, expiry_ms / 1000


def delivery_report(err, msg):
    if err is not None:
        log(f"  ✗ 발행 실패: {err}")
    else:
        log(f"  ✓ 발행 완료: 파티션={msg.partition()}, 오프셋={msg.offset()}")


def main():
    if not BROKER or not REGION:
        log("✗ .env에 KAFKA_BROKER, KAFKA_REGION이 필요합니다.")
        return

    force_all = "--all" in sys.argv
    only_one = "--one" in sys.argv
    dry_run = "--dry-run" in sys.argv

    state = load_state()
    published = state["published"]  # recording_id -> jobId
    next_seq = state["next_seq"]

    all_files = sorted(glob.glob(os.path.join(MOTIONAL_DIR, "*_odd_selection_tags_v2.json")))
    if not all_files:
        log(f"✗ Motional 파일을 찾지 못했습니다: {MOTIONAL_DIR}")
        return

    if force_all:
        targets = all_files
        log("⚠ --all: 전체 재발행 모드 (상태 무시)")
    else:
        targets = [
            p for p in all_files
            if os.path.basename(p).replace("_odd_selection_tags_v2.json", "") not in published
        ]

    if not targets:
        log("신규 Motional 파일 없음. 발행할 대상이 없습니다.")
        return

    if only_one:
        targets = targets[:1]

    log(f"발행 대상: {len(targets)}개 (전체 {len(all_files)}개 중 신규 {len(targets)}개)")

    if dry_run:
        for p in targets:
            log(f"  [dry-run] {os.path.basename(p)}")
        return

    conf = {
        "bootstrap.servers": BROKER,
        "security.protocol": "SASL_SSL",
        "sasl.mechanisms": "OAUTHBEARER",
        "oauth_cb": oauth_cb,
    }
    producer = Producer(conf)

    for i, path in enumerate(targets, 1):
        recording_id = os.path.basename(path).replace("_odd_selection_tags_v2.json", "")
        job_id = published.get(recording_id) or f"{JOBID_PREFIX}{next_seq:03d}"
        message = {
            "jobId": job_id,
            "type": "motional",
            "recording_id": recording_id,
            "path": path,
        }
        log(f"[{i}/{len(targets)}] 발행 중: {recording_id} (jobId={job_id})")
        producer.produce(
            TOPIC,
            key=job_id.encode("utf-8"),
            value=json.dumps(message, ensure_ascii=False).encode("utf-8"),
            callback=delivery_report,
        )
        producer.poll(0)

        if recording_id not in published:
            published[recording_id] = job_id
            next_seq += 1

    producer.flush(timeout=10)

    state["published"] = published
    state["next_seq"] = next_seq
    save_state(state)

    log(f"전체 발행 완료. 누적 발행 건수: {len(published)}")


if __name__ == "__main__":
    main()