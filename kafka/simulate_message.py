"""
실제 Kafka 없이, 예상되는 메시지 형태를 직접 만들어서
kafka_message_handler.process_kafka_message()로 흘려보내는 테스트.

실제 데이터가 오기 전에 처리 로직을 미리 검증하기 위한 용도.
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
