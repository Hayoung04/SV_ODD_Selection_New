"""
simulate_message.py
=====================
실제 Kafka 브로커에 연결하지 않고, 예상되는 여러 메시지 형태를 직접
만들어서 kafka_message_handler.process_kafka_message()에 바로 흘려보내는
로컬 테스트 스크립트.

실제 ODD/Motional 데이터가 Kafka로 흐르기 전에, 처리 로직(타입 판별,
스키마 분기, 색인 큐 등록 등)이 예상 형태에 맞게 잘 동작하는지 미리
검증하기 위한 용도. Kafka 연결·인증이 필요 없어 언제든 빠르게 실행 가능.

사용법:
    python3 simulate_message.py

[검증하는 4가지 시나리오]
  1. 연결 테스트 메시지 — 실제로 input 토픽에서 받았던 더미 메시지
     ({"jobId": "conntest-..."})가 "test" 타입으로 판별되어 무시되는지 확인
  2. ODD 메시지가 "파일 경로만" 알려주는 경우 (file_ref 모드) — 아직 실제
     스키마가 확정되지 않아, extract_odd_fields()가 이 형태도 처리할 수
     있는지 미리 확인해두기 위한 가정 시나리오
  3. ODD 메시지가 "태깅 내용 전체"를 메시지 안에 담고 있는 경우 (inline 모드)
     — 위와 마찬가지로 실 스키마 확정 전 대비용 가정 시나리오
  4. 알 수 없는 형식의 메시지 — 방어 로직(unknown 타입 처리)이 에러 없이
     정상적으로 걸러지는지 확인

⚠ 시나리오 2, 3은 실제 ODD 서비스가 어떤 스키마로 메시지를 보낼지 아직
   확정되지 않은 상태에서 "이런 형태로 올 수도 있다"고 가정한 것.
   실 스키마가 확정되면 kafka_message_handler.py의 EXTRACT_* 함수들을
   그 형태에 맞게 수정하고, 이 스크립트의 시나리오도 실제 형태로 갱신 필요.
"""
import json
from kafka_message_handler import process_kafka_message

print("=" * 60)
print("시나리오 1: 실제로 받았던 연결 테스트 메시지")
print("=" * 60)
process_kafka_message(json.dumps({"jobId": "conntest-07d13930"}), source="simulate")

print()
print("=" * 60)
print("시나리오 2: ODD 메시지가 '파일 경로만' 알려주는 경우라고 가정")
print("=" * 60)
process_kafka_message(json.dumps({
    "jobId": "job-001",
    "taskName": "AAA1",
    "path": "/mnt/data-center/scenes/GER/MACHET18/260414/"
            "Rec_Drv_GER_MACHET18_20260414_103936/aaa1_tags/"
            "일부uuid_tag.json"
}), source="simulate")

print()
print("=" * 60)
print("시나리오 3: ODD 메시지가 '태깅 내용 전체'를 담고 있는 경우라고 가정")
print("=" * 60)
process_kafka_message(json.dumps({
    "uuid": "test-uuid-1234",
    "taskName": "AAA1",
    "promptVersion": "aaa1_prompt_v1",
    "imagePath": "/mnt/data-center/scenes/GER/MACHET18/260414/"
                 "Rec_Drv_GER_MACHET18_20260414_103936/images/2160p_h120_front/"
                 "GER_MACHET18_20260414_103936_1776155994998997_2160p_h120_front.jpg",
    "tags": {"aaa1_tags": {"weather": "clear", "time": "day"}},
}), source="simulate")

print()
print("=" * 60)
print("시나리오 4: 알 수 없는 형식 (방어 로직 확인)")
print("=" * 60)
process_kafka_message(json.dumps({"randomField": "???"}), source="simulate")

print()
print("모든 시나리오 실행 완료.")
