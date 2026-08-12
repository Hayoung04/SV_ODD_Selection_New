"""
kafka_message_handler.py
=========================
Kafka에서 받은 메시지 1건을, 기존 폴링 파이프라인(unified_pipeline.py)의
처리 로직으로 넘겨 색인까지 연결하는 모듈.
실제 Kafka Consumer(kafka_consumer.py)와 로컬 시뮬레이터(simulate_message.py)
둘 다 이 모듈의 process_kafka_message() 하나만 호출해서 동일하게 동작한다.

⚠ 실제 ODD 메시지 스키마가 확정되면 아래 EXTRACT_* 3개 함수만 수정하면 됨
   (extract_message_type, extract_odd_fields, extract_motional_fields)

[메시지 타입 판별 — extract_message_type]
  - test     : {"jobId": ...}만 있는 연결 테스트용 더미 메시지 → 무시
  - odd      : aaa1_tags/lsd_tags 필드가 있거나 taskName이 AAA1/LSD인 경우
  - motional : odd_selection_scenario_events 필드가 있거나 type이 motional인 경우
  - unknown  : 위 어느 것도 아닌 경우 (원본 그대로 로그만 남기고 처리 안 함)

[ODD 메시지 처리 — extract_odd_fields → _index_odd_doc]
  두 가지 스키마 모두 대응하도록 미리 분기해둠:
    - inline   : 메시지 안에 태그 데이터 전체가 들어있는 경우 (imagePath 포함)
    - file_ref : 메시지에는 파일 경로만 있고, 실제 데이터는 그 경로에서 읽어와야 하는 경우
  이후 처리(타임스탬프 추출, 1fps 다운샘플링, Motional 매칭, 색인 큐 등록)는
  unified_pipeline.py의 기존 함수들을 그대로 재사용 — 폴링 경로와 완전히 동일한 로직

[Motional 메시지 처리]
  메시지에는 파일 경로만 담겨 오고, 그 경로의 실제 이벤트 파일을 읽어
  motional_cache에 캐시로 저장 → 해당 recording_id로 이미 색인되어 있는
  ODD 문서들을 unified_pipeline.rematch_recording()으로 재매칭·업데이트함
  (ODD가 먼저 오든 Motional이 먼저 오든, 최종적으로는 항상 병합된 결과가 남는
  기존 폴링 파이프라인의 "늦은 도착 자동 재매칭" 설계를 그대로 재사용)

[의존성]
  scripts/unified_pipeline.py 를 그대로 import해서 사용
  (색인 큐, 다운샘플링 상태(best_frame_per_second), Motional 캐시 등
   전역 상태를 폴링 파이프라인과 공유함)
"""
import json
import os
import sys

sys.path.insert(0, os.path.expanduser("/home/odd-selection/scripts"))
import unified_pipeline as pipeline


# ══════════════════════════════════════════════════
#  ⚠ 여기 3개 함수만 실제 스키마 확인되면 수정
# ══════════════════════════════════════════════════

def extract_message_type(data):
    if "jobId" in data and len(data) == 1:
        return "test"
    if "aaa1_tags" in data or "lsd_tags" in data or data.get("taskName") in ("AAA1", "LSD"):
        return "odd"
    if "odd_selection_scenario_events" in data or "motional" in str(data.get("type", "")).lower():
        return "motional"
    return "unknown"


def extract_odd_fields(data):
    if "imagePath" in data:
        return {"mode": "inline", "doc": data}
    elif "path" in data or "file_path" in data:
        path = data.get("path") or data.get("file_path")
        task_name = data.get("taskName", "AAA1")
        return {"mode": "file_ref", "path": path, "taskName": task_name}
    else:
        return {"mode": "unknown", "raw": data}


def extract_motional_fields(data):
    if "path" in data or "file_path" in data:
        path = data.get("path") or data.get("file_path")
        return {"mode": "file_ref", "path": path}
    return {"mode": "unknown", "raw": data}


def _guess_recording_id(image_path):
    parts = image_path.replace("\\", "/").split("/")
    for p in parts:
        if p.startswith("Rec_Drv_"):
            return p
    return None


# ══════════════════════════════════════════════════
#  실제 색인까지 연결하는 부분 (기존 pipeline 함수 재사용)
# ══════════════════════════════════════════════════

def _index_odd_doc(doc, task_name):
    """기존 unified_pipeline의 로직과 동일하게 1건을 색인 큐에 넣음"""
    ts = pipeline.extract_timestamp_s(doc.get("imagePath", ""))
    if ts is None:
        print(f"    ✗ 타임스탬프 추출 실패: {doc.get('imagePath')}")
        return False

    recording_id = _guess_recording_id(doc.get("imagePath", ""))
    if not recording_id:
        print(f"    ✗ recording_id 추출 실패")
        return False

    whole_second = round(ts)
    diff = abs(ts - whole_second)
    key = (task_name, recording_id, whole_second)

    # 기존 파이프라인과 동일한 1fps 다운샘플링 규칙 적용
    if key in pipeline.best_frame_per_second and pipeline.best_frame_per_second[key] <= diff:
        print(f"    - 이미 더 가까운 프레임 존재, 건너뜀 ({recording_id} {whole_second}s)")
        return True
    pipeline.best_frame_per_second[key] = diff

    motional_events_all = pipeline.load_motional_events(recording_id)
    tags, exact, events = pipeline.match_motional(ts, motional_events_all)

    merged = {
        "uuid": doc["uuid"],
        "taskName": task_name,
        "recording_id": recording_id,
        "timestamp_s": ts,
        "whole_second": whole_second,
        "imagePath": doc["imagePath"],
        "tags": doc["tags"],
        "motional_scenarios": tags,
        "motional_exact": exact,
        "motional_events": events,
    }
    doc_id = f"{task_name.lower()}-{recording_id}-{whole_second}"
    pipeline.queue_document(doc_id, merged)
    print(f"    ✓ 색인 큐에 추가: {doc_id} (motional {len(tags)}개)")
    return True


def process_kafka_message(raw_value, source="kafka"):
    try:
        if isinstance(raw_value, bytes):
            raw_value = raw_value.decode("utf-8")
        data = json.loads(raw_value)
    except (json.JSONDecodeError, UnicodeDecodeError) as e:
        print(f"  ✗ [{source}] JSON 파싱 실패: {e}")
        return False

    msg_type = extract_message_type(data)
    print(f"  → [{source}] 메시지 타입 판별: {msg_type}")

    if msg_type == "test":
        print(f"    (연결 테스트 메시지, 무시): {data}")
        return True

    if msg_type == "odd":
        fields = extract_odd_fields(data)

        if fields["mode"] == "inline":
            doc = fields["doc"]
            task_name = doc.get("taskName", "AAA1")
            return _index_odd_doc(doc, task_name)

        elif fields["mode"] == "file_ref":
            path = fields["path"]
            task_name = fields["taskName"]
            print(f"    → 파일 참조 방식. 경로: {path}")

            if not path or not os.path.exists(path):
                print(f"    ✗ 파일이 존재하지 않음: {path}")
                return False

            try:
                with open(path, "r", encoding="utf-8") as f:
                    doc = json.load(f)
            except (json.JSONDecodeError, OSError) as e:
                print(f"    ✗ 파일 읽기 실패: {e}")
                return False

            return _index_odd_doc(doc, task_name)

        else:
            print(f"    ⚠ 알 수 없는 형식, 원본: {fields['raw']}")
            return False

    if msg_type == "motional":
        fields = extract_motional_fields(data)
        path = fields.get("path")
        print(f"    Motional 파일 참조: {path}")

        if not path or not os.path.exists(path):
            print(f"    ✗ Motional 파일이 존재하지 않음: {path}")
            return False

        recording_id = os.path.basename(path).replace("_odd_selection_tags_v2.json", "")
        try:
            events = pipeline.parse_motional_file(path)
        except (json.JSONDecodeError, OSError, KeyError) as e:
            print(f"    ✗ Motional 파싱 실패: {e}")
            return False

        pipeline.motional_cache[recording_id] = events
        print(f"    ✓ Motional 캐시 갱신: {recording_id} ({len(events)}개 이벤트)")

        queued = pipeline.rematch_recording(recording_id, events)
        if queued > 0:
            print(f"    ↻ 재매칭: {queued}건 업데이트")
        return True

    print(f"    ⚠ 처리 규칙 없음, 원본 그대로 출력: {json.dumps(data, ensure_ascii=False)[:300]}")
    return False