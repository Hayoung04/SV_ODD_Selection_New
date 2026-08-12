"""
kafka_consumer.py
==================
dev.shared.odd-tagging.input 토픽을 상시 구독하며, 새 메시지가 도착할
때마다 kafka_message_handler.process_kafka_message()로 넘겨 처리하는
상시 구동형 Consumer.

사용법:
    python3 kafka_consumer.py   (Ctrl+C로 종료)

[Consumer Group]
  group.id: odd-selection-consumer
  (ODD 서비스 등 다른 서비스와는 별도 그룹으로 동일 토픽을 독립적으로 구독함.
   peek_topic.py 등 읽기 전용 확인용 스크립트와도 그룹을 분리해서 운영)

[오프셋 커밋 정책 — 처리 성공 시에만 커밋]
  enable.auto.commit = False 로 자동 커밋을 끄고, 메시지 처리가 성공했을
  때만 수동으로 commit() 호출.
    → 처리 도중 예외가 나거나(예: Elasticsearch 다운) 실패하면 오프셋이
      커밋되지 않으므로, Consumer를 재시작하면 그 메시지부터 자동으로
      다시 처리됨 (메시지 유실 방지). 실제로 ES 컨테이너가 다운됐을 때
      이 재처리 동작으로 데이터 유실 없이 복구된 사례 있음.
  각 메시지는 최대 3회(MAX_RETRY)까지 재시도 후에도 실패하면 커밋하지 않고
  다음 메시지로 넘어감 (로그에 실패한 파티션/오프셋을 남겨 추적 가능하게 함)

[색인 큐 flush 주기]
  unified_pipeline.py의 Bulk 색인 큐를 5초(FLUSH_INTERVAL_S)마다 강제로
  비움 — 메시지가 뜸하게 들어와도 색인 지연이 오래 쌓이지 않도록 함.
  종료 시(Ctrl+C)에도 남은 큐를 마지막으로 한 번 더 비운 뒤 종료.

[인증]
  AWS MSK IAM 인증(OAUTHBEARER) 사용. Python 클라이언트는 AWS_MSK_IAM이
  아닌 OAUTHBEARER 메커니즘만 지원하므로 oauth_cb에서 매 토큰 발급 시
  MSKAuthTokenProvider로 리전 서명된 토큰을 발급받음.

[환경 변수 — .env]
  KAFKA_BROKER, KAFKA_REGION, AWS_PROFILE(기본값 odd-msk)
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
