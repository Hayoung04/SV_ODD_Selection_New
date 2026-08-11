import json
import os
import time
import requests

# ── 경로 설정 ──
DATA_ROOT = os.path.expanduser("~/odd-search/machet18_data")
ODD_TASK_NAMES = ["AAA1", "LSD"]
MOTIONAL_ROOT = os.path.join(DATA_ROOT, "motional")

ES_URL = "http://localhost:9200"
ES_INDEX = "odd-frames-v2"
ES_AUTH = ("elastic", "sv_odd_selection_2026")

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