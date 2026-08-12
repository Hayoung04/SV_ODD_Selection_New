"""
unified_pipeline.py
====================
ODD 태깅 데이터(AAA1/LSD)와 Motional 시나리오 데이터를 통합해
Elasticsearch(odd-frames-v2)에 색인하는 폴링 기반 파이프라인.

[전체 흐름]
  5초(POLL_INTERVAL)마다 두 폴더를 반복 스캔:
    1) DATA_ROOT/motional        → Motional 시나리오 이벤트 파일
    2) DATA_ROOT/{AAA1,LSD}      → ODD 태깅 결과 파일 (10fps, 프레임 1장 = 파일 1개)

  ODD 파일을 발견하면:
    - imagePath 파일명에서 마이크로초 단위 촬영 시각을 추출
    - 같은 정수초(whole_second) 안에서는 가장 오차 작은 프레임 1장만 남김
      → 10fps 원본을 1fps로 다운샘플링 (문서 ID를 taskName-recording_id-정수초로
        고정해, 더 정확한 프레임이 나중에 오면 자동 덮어쓰기됨)
    - 같은 recording_id의 Motional 이벤트와 시각(±0.5초, WINDOW_HALF_S) 기준으로
      매칭해 motional_scenarios(태그명)/motional_exact(정확 매칭)/
      motional_events(실제 구간 정보) 필드를 함께 채워 하나의 문서로 병합
    - 최대 500건(BULK_SIZE) 단위로 모아 Elasticsearch _bulk API로 색인

  Motional 파일을 ODD보다 나중에 발견하면(늦은 도착):
    - 해당 recording_id로 이미 색인되어 있는 ODD 문서들을 ES에서 다시 조회
    - Motional 이벤트로 재매칭해서 필요한 문서만 업데이트 (rematch_recording)
    → ODD/Motional 중 어느 쪽이 먼저 도착해도 최종적으로는 항상 병합된 결과가 남음

[핵심 설계]
  - "조금이라도 겹치면 매칭"이 원칙: 이벤트 구간과 프레임 시각(±0.5초) 사이에
    조금이라도 겹침이 있으면 태그 부여(놓치는 것 방지), 실제로 그 시각에
    구간 안에 있는 것만 별도로 exact 표시
  - 이미 색인된 문서와 값이 완전히 같으면 재색인하지 않음(불필요한 쓰기 방지)
  - 이 스크립트의 통합 처리 로직(다운샘플링·매칭·병합)은 데이터 유입 경로가
    폴링이든 Kafka든 동일하게 재사용되도록 설계됨
    (Kafka 경로: kafka_message_handler.py가 이 파일의 함수들을 그대로 import)

[환경 변수 — .env]
  ES_USER, ES_PASS : Elasticsearch 인증 정보

[주요 상수]
  DATA_ROOT       : 원본 데이터 루트 경로
  ODD_TASK_NAMES   : 스캔 대상 태스크명 목록 (AAA1, LSD)
  POLL_INTERVAL    : 폴더 재스캔 주기 (초)
  WINDOW_HALF_S    : Motional 매칭 허용 오차 (±초)
  BULK_SIZE        : Bulk 색인 배치 크기
"""
import json
import os
import time
import requests

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

# ── 경로 설정 ──
DATA_ROOT = "/home/odd-selection/machet18_data"
ODD_TASK_NAMES = ["AAA1", "LSD"]
MOTIONAL_ROOT = os.path.join(DATA_ROOT, "motional")

ES_URL = "http://localhost:9200"
ES_INDEX = "odd-frames-v2"
ES_AUTH = (os.environ.get("ES_USER"), os.environ.get("ES_PASS"))

if not ES_AUTH[0] or not ES_AUTH[1]:
    print("✗ .env에 ES_USER, ES_PASS가 필요합니다.")
    exit(1)
POLL_INTERVAL = 5
WINDOW_HALF_S = 0.5     
BULK_SIZE = 500

# ── 상태 저장 ──
seen_odd_files = set()
seen_motional_files = set()
best_frame_per_second = {}
motional_cache = {}
bulk_buffer = []

session = requests.Session()
session.auth = ES_AUTH


def extract_timestamp_s(image_path):
    """imagePath 파일명 끝의 마이크로초 epoch 숫자를 초 단위로 변환"""
    fname = os.path.basename(image_path)
    for p in fname.split("_"):
        if p.isdigit() and len(p) >= 15:
            return int(p) / 1_000_000
    return None


def parse_motional_file(path):
    """Motional json 하나를 (시작, 끝, 시나리오) 목록으로 변환"""
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    events = data["tags"]["odd_selection_scenario_events"]
    flat = []
    for scenario, ev_list in events.items():
        for ev in ev_list:
            flat.append((ev["start_timestamp_s"], ev["end_timestamp_s"], scenario))
    flat.sort(key=lambda x: x[0])
    return flat


def load_motional_events(recording_id):
    if recording_id in motional_cache:
        return motional_cache[recording_id]
    path = os.path.join(MOTIONAL_ROOT, f"{recording_id}_odd_selection_tags_v2.json")
    if not os.path.exists(path):
        motional_cache[recording_id] = None
        return None
    flat = parse_motional_file(path)
    motional_cache[recording_id] = flat
    return flat


def match_motional(ts, motional_events):
    """이 프레임에 매칭되는 Motional 정보를 3가지 형태로 반환

    - tags   : 시나리오 이름 목록 (검색용, 중복 제거)
    - exact  : 프레임 시각이 구간 안에 실제로 들어있는 시나리오 이름 목록
    - events : 매칭된 개별 이벤트의 실제 구간 정보
               [{scenario, start, end, exact}, ...]
               → 같은 시나리오라도 구간이 여러 개면 각각 별도 항목
    """
    if not motional_events:
        return [], [], []

    lo, hi = ts - WINDOW_HALF_S, ts + WINDOW_HALF_S
    tags = set()
    exact = set()
    events = []

    for start, end, scenario in motional_events:
        if start > hi:
            break
        if end >= lo:
            is_exact = start <= ts <= end
            tags.add(scenario)
            if is_exact:
                exact.add(scenario)
            events.append({
                "scenario": scenario,
                "start": start,
                "end": end,
                "duration": round(end - start, 3),
                "exact": is_exact,
            })

    # 구간이 긴 것부터(맥락이 큰 것부터) 정렬
    events.sort(key=lambda e: (-e["duration"], e["scenario"]))
    return sorted(tags), sorted(exact), events


# ══════════════════════════════════════════════════
#  Bulk 색인
# ══════════════════════════════════════════════════

def queue_document(doc_id, doc):
    bulk_buffer.append((doc_id, doc))
    if len(bulk_buffer) >= BULK_SIZE:
        flush_bulk()


def flush_bulk():
    if not bulk_buffer:
        return 0

    lines = []
    for doc_id, doc in bulk_buffer:
        lines.append(json.dumps({"index": {"_index": ES_INDEX, "_id": doc_id}}))
        lines.append(json.dumps(doc, ensure_ascii=False))
    payload = "\n".join(lines) + "\n"

    count = len(bulk_buffer)
    bulk_buffer.clear()

    try:
        res = session.post(
            f"{ES_URL}/_bulk",
            data=payload.encode("utf-8"),
            headers={"Content-Type": "application/x-ndjson"},
        )
        if res.status_code != 200:
            print(f"  ✗ Bulk 요청 실패 (HTTP {res.status_code})")
            return 0

        result = res.json()
        if result.get("errors"):
            failed = [
                item["index"]
                for item in result["items"]
                if item.get("index", {}).get("status", 200) >= 400
            ]
            print(f"  ⚠ Bulk 부분 실패: {len(failed)}/{count}건")
            for f in failed[:3]:
                print(f"      - {f.get('_id')}: {f.get('error', {}).get('reason', '')[:100]}")
            return count - len(failed)

        print(f"  ✓ Bulk 색인 완료: {count}건 ({result.get('took', 0)}ms)")
        return count
    except Exception as e:
        print(f"  ✗ Bulk 전송 중 에러: {e}")
        return 0


# ══════════════════════════════════════════════════
#  ODD 파일 처리
# ══════════════════════════════════════════════════

def process_odd_tag_file(task_name, recording_id, fp):
    try:
        with open(fp, "r", encoding="utf-8") as f:
            doc = json.load(f)
    except (json.JSONDecodeError, OSError):
        return

    ts = extract_timestamp_s(doc.get("imagePath", ""))
    if ts is None:
        return

    # ── 1fps 다운샘플링: 정수초별로 가장 가까운 프레임만 유지 ──
    whole_second = round(ts)
    diff = abs(ts - whole_second)
    key = (task_name, recording_id, whole_second)

    if key in best_frame_per_second and best_frame_per_second[key] <= diff:
        return
    best_frame_per_second[key] = diff

    motional_events_all = load_motional_events(recording_id)
    tags, exact, events = match_motional(ts, motional_events_all)

    merged = {
        "uuid": doc["uuid"],
        "taskName": task_name,
        "recording_id": recording_id,
        "timestamp_s": ts,
        "whole_second": whole_second,
        "imagePath": doc["imagePath"],
        "tags": doc["tags"],
        "motional_scenarios": tags,     # 검색용 (이름만)
        "motional_exact": exact,        # 이 시각에 실제 발생 중인 것
        "motional_events": events,      # 실제 구간 정보 (재생/시각화용)
    }
    doc_id = f"{task_name.lower()}-{recording_id}-{whole_second}"
    queue_document(doc_id, merged)


def scan_odd_folders():
    for task_name in ODD_TASK_NAMES:
        task_root = os.path.join(DATA_ROOT, task_name)
        if not os.path.isdir(task_root):
            continue
        for recording_id in os.listdir(task_root):
            rec_dir = os.path.join(task_root, recording_id)
            if not os.path.isdir(rec_dir):
                continue
            for root, _, files in os.walk(rec_dir):
                for fname in files:
                    if not fname.endswith("_tag.json"):
                        continue
                    fp = os.path.join(root, fname)
                    if fp in seen_odd_files:
                        continue
                    seen_odd_files.add(fp)
                    process_odd_tag_file(task_name, recording_id, fp)


# ══════════════════════════════════════════════════
#  Motional 처리 (늦게 도착해도 자동 재매칭)
# ══════════════════════════════════════════════════

def fetch_indexed_docs_by_recording(recording_id):
    query = {
        "query": {"term": {"recording_id": recording_id}},
        "size": 10000,
        "_source": ["uuid", "taskName", "recording_id", "timestamp_s",
                    "whole_second", "imagePath", "tags",
                    "motional_scenarios", "motional_exact", "motional_events"],
    }
    res = session.post(f"{ES_URL}/{ES_INDEX}/_search", json=query)
    if res.status_code != 200:
        return []
    return [(h["_id"], h["_source"]) for h in res.json()["hits"]["hits"]]


def rematch_recording(recording_id, motional_events_all):
    """Motional이 새로 도착한 recording_id의 기존 문서들을 재매칭해서 업데이트"""
    docs = fetch_indexed_docs_by_recording(recording_id)
    if not docs:
        return 0

    queued = 0
    for doc_id, source in docs:
        ts = source.get("timestamp_s")
        if ts is None:
            continue
        new_tags, new_exact, new_events = match_motional(ts, motional_events_all)
        if (sorted(new_tags) == sorted(source.get("motional_scenarios", [])) and
                sorted(new_exact) == sorted(source.get("motional_exact", [])) and
                new_events == source.get("motional_events", [])):
            continue   # 이미 같으면 건드리지 않음
        source["motional_scenarios"] = new_tags
        source["motional_exact"] = new_exact
        source["motional_events"] = new_events
        queue_document(doc_id, source)
        queued += 1
    return queued


def scan_motional_folder():
    if not os.path.isdir(MOTIONAL_ROOT):
        return
    for fname in os.listdir(MOTIONAL_ROOT):
        if not fname.endswith("_odd_selection_tags_v2.json"):
            continue
        fp = os.path.join(MOTIONAL_ROOT, fname)
        if fp in seen_motional_files:
            continue
        seen_motional_files.add(fp)

        recording_id = fname.replace("_odd_selection_tags_v2.json", "")
        try:
            events = parse_motional_file(fp)
        except (json.JSONDecodeError, OSError, KeyError) as e:
            print(f"  ✗ Motional 파싱 실패: {fname} - {e}")
            continue

        was_missing = recording_id in motional_cache and motional_cache[recording_id] is None
        motional_cache[recording_id] = events
        print(f"  [Motional 로드] {recording_id}: {len(events)}개 이벤트")

        queued = rematch_recording(recording_id, events)
        if queued > 0:
            flush_bulk()
            print(f"  ↻ [재매칭] {recording_id}: {queued}건 업데이트"
                  + (" (이전에 Motional 없이 색인됨)" if was_missing else ""))


# ══════════════════════════════════════════════════

def main():
    print("통합 파이프라인 폴링 시작")
    print(f"  ODD 경로     : {DATA_ROOT}/{{AAA1,LSD}}")
    print(f"  Motional 경로: {MOTIONAL_ROOT}")
    print(f"  1fps 다운샘플링, ±{WINDOW_HALF_S}초 매칭(exact/nearby + 구간정보), 자동 재매칭, Bulk({BULK_SIZE}건)")
    while True:
        try:
            scan_motional_folder()
            scan_odd_folders()
            flush_bulk()
        except Exception as e:
            print(f"  ✗ 스캔 중 에러: {e}")
        time.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    main()