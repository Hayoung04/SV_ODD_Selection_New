import json
import glob
import os
import requests

DATA_ROOT = os.path.expanduser("~/odd-search/machet18_data")
MOTIONAL_ROOT = os.path.join(DATA_ROOT, "motional")
ES_URL = "http://localhost:9200"
ES_INDEX = "odd-frames-v2"
ES_AUTH = ("elastic", "changeme_odd_2026")
WINDOW_HALF_S = 0.05  

TASK_NAMES = ["AAA1", "LSD"]

def extract_timestamp_s(image_path):
    fname = os.path.basename(image_path)
    for p in fname.split("_"):
        if p.isdigit() and len(p) >= 15:
            return int(p) / 1_000_000
    return None

_motional_cache = {}

def load_motional_events(recording_id):
    if recording_id in _motional_cache:
        return _motional_cache[recording_id]
    path = os.path.join(MOTIONAL_ROOT, f"{recording_id}_odd_selection_tags_v2.json")
    if not os.path.exists(path):
        _motional_cache[recording_id] = None
        return None
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    events = data["tags"]["odd_selection_scenario_events"]
    flat = []
    for scenario, ev_list in events.items():
        for ev in ev_list:
            flat.append((ev["start_timestamp_s"], ev["end_timestamp_s"], scenario))
    flat.sort(key=lambda x: x[0])
    _motional_cache[recording_id] = flat
    return flat

def match_motional_tags(ts, motional_events):
    if not motional_events:
        return []
    lo, hi = ts - WINDOW_HALF_S, ts + WINDOW_HALF_S
    matched = set()
    for start, end, scenario in motional_events:
        if start > hi:
            break
        if end >= lo:
            matched.add(scenario)
    return sorted(matched)

def index_document(doc):
    doc_id = doc["uuid"]
    res = requests.put(f"{ES_URL}/{ES_INDEX}/_doc/{doc_id}", auth=ES_AUTH, json=doc)
    return res.status_code in (200, 201)

def main():
    total_ok, total_fail, total_with_motional = 0, 0, 0
    per_task_count = {}

    for task_name in TASK_NAMES:
        task_root = os.path.join(DATA_ROOT, task_name)
        if not os.path.isdir(task_root):
            print(f"⚠ {task_name} 폴더 없음, 건너뜀: {task_root}")
            continue

        recording_ids = [os.path.basename(p) for p in glob.glob(os.path.join(task_root, "*")) if os.path.isdir(p)]
        print(f"\n[{task_name}] 대상 recording 수: {len(recording_ids)}")

        for rec_id in recording_ids:
            motional_events = load_motional_events(rec_id)
            if motional_events is None:
                print(f"  ⚠ {rec_id}: Motional 데이터 없음 (해당 프레임은 motional_scenarios 빈 배열)")

            files = glob.glob(os.path.join(task_root, rec_id, "*", "*_tag.json"))
            ok_count = 0
            for fp in files:
                try:
                    with open(fp, "r", encoding="utf-8") as f:
                        doc = json.load(f)
                except json.JSONDecodeError:
                    total_fail += 1
                    continue

                ts = extract_timestamp_s(doc.get("imagePath", ""))
                if ts is None:
                    total_fail += 1
                    continue

                motional_tags = match_motional_tags(ts, motional_events)
                merged = {
                    "uuid": doc["uuid"],
                    "taskName": doc["taskName"],
                    "recording_id": rec_id,
                    "timestamp_s": ts,
                    "imagePath": doc["imagePath"],
                    "tags": doc["tags"],
                    "motional_scenarios": motional_tags,
                }
                if index_document(merged):
                    total_ok += 1
                    ok_count += 1
                    if motional_tags:
                        total_with_motional += 1
                else:
                    total_fail += 1

            print(f"  ✓ {rec_id}: {ok_count}건 색인")
            per_task_count[task_name] = per_task_count.get(task_name, 0) + ok_count

    print(f"\n=== 최종 결과 ===")
    for t, c in per_task_count.items():
        print(f"{t}: {c}건")
    print(f"총 색인 성공: {total_ok}, 실패: {total_fail}")
    print(f"Motional 태그가 붙은 문서 수: {total_with_motional}")

if __name__ == "__main__":
    main()
