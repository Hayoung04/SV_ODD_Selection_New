"""토픽에서 메시지를 몇 건만 읽어와 실제 구조를 확인하는 스크립트.
아무것도 처리/저장하지 않고 화면에 출력만 함."""
import json
import os
from confluent_kafka import Consumer
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


load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"))

BROKER = os.environ.get("KAFKA_BROKER")
REGION = os.environ.get("KAFKA_REGION")
os.environ["AWS_PROFILE"] = os.environ.get("AWS_PROFILE", "odd-msk")

TOPIC = "dev.shared.odd-tagging.input"
MAX_MESSAGES = 5   # 딱 5건만 훑어보기


def oauth_cb(oauth_config):
    token, expiry_ms = MSKAuthTokenProvider.generate_auth_token(REGION)
    return token, expiry_ms / 1000


def main():
    conf = {
        "bootstrap.servers": BROKER,
        "security.protocol": "SASL_SSL",
        "sasl.mechanisms": "OAUTHBEARER",
        "oauth_cb": oauth_cb,
        "group.id": "odd-selection-peek",   # 미리보기 전용 그룹 (실제 처리 그룹과 분리)
        "auto.offset.reset": "earliest",     # 토픽 맨 처음부터 읽기
        "enable.auto.commit": False,         # 읽었다고 표시 안 함 (다시 읽어도 안전)
    }

    consumer = Consumer(conf)
    consumer.subscribe([TOPIC])

    print(f"토픽 구독 시작: {TOPIC}")
    print(f"최대 {MAX_MESSAGES}건까지만 읽고 종료합니다.\n")

    count = 0
    try:
        while count < MAX_MESSAGES:
            msg = consumer.poll(timeout=10.0)
            if msg is None:
                print("10초간 새 메시지 없음. 종료합니다.")
                break
            if msg.error():
                print(f"✗ 에러: {msg.error()}")
                continue

            count += 1
            print(f"=== 메시지 {count} ===")
            print(f"  파티션: {msg.partition()}, 오프셋: {msg.offset()}")
            print(f"  키: {msg.key()}")

            raw = msg.value()
            try:
                parsed = json.loads(raw)
                print(f"  값(JSON):")
                print(json.dumps(parsed, indent=2, ensure_ascii=False)[:1000])
            except (json.JSONDecodeError, TypeError):
                print(f"  값(원본, JSON 아님): {raw[:500]}")
            print()

    finally:
        consumer.close()
        print(f"완료. 총 {count}건 확인.")


if __name__ == "__main__":
    main()
