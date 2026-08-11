"""
실제 Kafka에서 메시지를 계속 받아서 처리하는 상시 구동용 Consumer.
처리 성공 시에만 오프셋을 커밋해서, 중간에 죽어도 메시지 유실이 없게 함.

사용법:
    python3 kafka_consumer.py
"""
import os
import time
from confluent_kafka import Consumer
from aws_msk_iam_sasl_signer import MSKAuthTokenProvider

from kafka_message_handler import process_kafka_message
import sys
sys.path.insert(0, os.path.expanduser("/home/odd-selection/scripts"))
import unified_pipeline as pipeline


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
FLUSH_INTERVAL_S = 5      # 이 주기로 색인 큐를 강제로 비움 (메시지가 뜸해도 지연 없이 반영)
MAX_RETRY = 3


def oauth_cb(oauth_config):
    token, expiry_ms = MSKAuthTokenProvider.generate_auth_token(REGION)
    return token, expiry_ms / 1000


def main():
    if not BROKER or not REGION:
        print("✗ .env에 KAFKA_BROKER, KAFKA_REGION이 필요합니다.")
        return

    conf = {
        "bootstrap.servers": BROKER,
        "security.protocol": "SASL_SSL",
        "sasl.mechanisms": "OAUTHBEARER",
        "oauth_cb": oauth_cb,
        "group.id": "odd-selection-consumer",   # 실제 처리용 그룹 (peek 그룹과 분리)
        "auto.offset.reset": "earliest",
        "enable.auto.commit": False,             # 처리 성공 후 수동 커밋
    }

    consumer = Consumer(conf)
    consumer.subscribe([TOPIC])

    print(f"Kafka Consumer 시작: {TOPIC}")
    print(f"  (Ctrl+C로 종료)")

    last_flush = time.time()

    try:
        while True:
            msg = consumer.poll(timeout=1.0)

            if msg is not None and not msg.error():
                success = False
                for attempt in range(1, MAX_RETRY + 1):
                    try:
                        success = process_kafka_message(msg.value(), source="kafka")
                        break
                    except Exception as e:
                        print(f"  ✗ 처리 중 예외 (시도 {attempt}/{MAX_RETRY}): {e}")
                        time.sleep(1)

                if success:
                    consumer.commit(msg)   # 성공했을 때만 오프셋 커밋
                else:
                    print(f"  ⚠ 처리 실패, 오프셋 커밋 안 함 (파티션={msg.partition()}, 오프셋={msg.offset()})")

            elif msg is not None and msg.error():
                print(f"  ✗ Kafka 에러: {msg.error()}")

            # 주기적으로 색인 큐 비우기 (메시지가 뜸해도 색인 지연 최소화)
            if time.time() - last_flush > FLUSH_INTERVAL_S:
                pipeline.flush_bulk()
                last_flush = time.time()

    except KeyboardInterrupt:
        print("\n종료 신호 받음, 남은 큐 처리 중...")
    finally:
        pipeline.flush_bulk()   # 종료 전 마지막으로 남은 것 전부 반영
        consumer.close()
        print("Consumer 종료 완료.")


if __name__ == "__main__":
    main()
