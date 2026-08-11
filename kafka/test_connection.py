import socket
import os

# ── .env 파일 읽기 ──
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

if not BROKER or not REGION:
    print("✗ .env에 KAFKA_BROKER, KAFKA_REGION이 필요합니다.")
    exit(1)

from confluent_kafka.admin import AdminClient
from aws_msk_iam_sasl_signer import MSKAuthTokenProvider


def oauth_cb(oauth_config):
    token, expiry_ms = MSKAuthTokenProvider.generate_auth_token(REGION)
    return token, expiry_ms / 1000


def main():
    first_broker = BROKER.split(",")[0]
    host, port = first_broker.split(":")
    try:
        with socket.create_connection((host, int(port)), timeout=5):
            print(f"✓ 네트워크 연결 성공: {BROKER}")
    except Exception as e:
        print(f"✗ 네트워크 연결 실패: {e}")
        return

    conf = {
        "bootstrap.servers": BROKER,
        "security.protocol": "SASL_SSL",
        "sasl.mechanisms": "OAUTHBEARER",
        "oauth_cb": oauth_cb,
    }
    admin = AdminClient(conf)
    try:
        md = admin.list_topics(timeout=10)
        print(f"✓ 카프카 인증 성공! 토픽 {len(md.topics)}개 확인됨")
        for name in md.topics:
            print(f"    - {name}")
    except Exception as e:
        print(f"✗ 카프카 인증/조회 실패: {e}")


if __name__ == "__main__":
    main()