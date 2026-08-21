"""
audit_pipeline.py
===================
unified_pipeline.py가 실제로 색인한 결과가 맞는지 독립적으로 재검증하는
감사(audit) 스크립트.

원본 ODD/Motional 파일들을 파이프라인과 "같은 로직이지만 별도로 다시
구현한 코드"로 처음부터 다시 계산해서 '정답'을 만든 뒤, 그 정답을
Elasticsearch에 실제로 저장되어 있는 문서와 하나하나 전수 대조한다.
파이프라인 코드를 그대로 재사용해 정답을 만들면 파이프라인 자체의 버그를
잡아낼 수 없기 때문에, 일부러 별도로 재구현함 (extract_timestamp_s,
load_motional_events, match_motional 로직을 이 파일 안에 독립적으로
다시 작성해둔 이유).

사용법:
    python3 audit_pipeline.py

[동작 순서]
  1) build_expected() — ODD 태스크 폴더(AAA1, LSD)를 처음부터 다시 스캔해,
     (taskName, recording_id, 정수초)별로 가장 가까운 프레임을 골라
     '기대값'(uuid, tags, motional_scenarios/exact/events)을 재계산
  2) 각 기대값에 대해 실제 Elasticsearch 문서(doc_id 규칙: taskName-
     recording_id-정수초)를 조회해, 아래 5가지 항목을 모두 비교:
       ① uuid 일치 (1fps 다운샘플링에서 실제로 가장 가까운 프레임이 뽑혔는지)
       ② ODD 태그(tags) 내용 일치
       ③ motional_scenarios(태그명 목록) 일치
       ④ motional_exact(정확 매칭 목록) 일치
       ⑤ motional_events(구간 상세 정보) 일치 — 부동소수점 오차 방지를 위해
          normalize_events()로 반올림 후 비교
  3) 전체 검사 대상 수, 완전 일치 수, 불일치/누락 수를 출력하고
     불일치 건은 최대 20개까지 사유와 함께 상세 출력

[검증 이력]
  976건 전수 대조 결과 불일치 0건 확인 (폴링 경로 초기 검증 시).
  Kafka로 유입된 데이터에 대해서도 동일한 방식의 전수 검증이 필요하며,
  현재는 샘플 2건만 수동 확인된 상태 — 정식 audit 실행은 아직 미실시.

[환경 변수 — .env]
  ES_USER, ES_PASS : Elasticsearch 인증 정보
"""
import json
import os
import glob
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

DATA_ROOT = "/home/odd-selection/machet18_data"
ODD_TASK_NAMES = ["AAA1", "LSD"]
MOTIONAL_ROOT = os.path.join(DATA_ROOT, "motional")

ES_URL = os.environ.get("ES_URL", "http://localhost:9200")
ES_INDEX = os.environ.get("ES_INDEX", "odd-frames-v2")
ES_AUTH = (os.environ.get("ES_USER"), os.environ.get("ES_PASS"))

if not ES_AUTH[0] or not ES_AUTH[1]:
    print("✗ .env에 ES_USER, ES_PASS가 필요합니다.")
    exit(1)
    
WINDOW_HALF_S = 0.5


def extract_timestamp_s(image_path):
    fname = os.path.basename(image_path)
    for p in fname.split("_"):
        if p.isdigit() and len(p) >= 15:
            return int(p) / 1_000_000
    return None


def load_motional_events(recording_id, cache={}):
    if recording_id in cache:
        return cache[recording_id]
    path = os.path.join(MOTIONAL_ROOT, f"{recording_id}_odd_selection_tags_v2.json")
    if not os.path.exists(path):
        cache[recording_id] = None
        return None
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    events = data["tags"]["odd_selection_scenario_events"]
    flat = []
    for scenario, ev_list in events.items():
        for ev in ev_list:
            flat.append((ev["start_timestamp_s"], ev["end_timestamp_s"], scenario))
    flat.sort(key=lambda x: x[0])
    cache[recording_id] = flat
    return flat


def match_motional(ts, motional_events):
    """(tags, exact, events) 반환 — 파이프라인과 동일 로직을 독립 구현"""
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

    events.sort(key=lambda e: (-e["duration"], e["scenario"]))
    return sorted(tags), sorted(exact), events


def build_expected():
    """원본 파일들을 다시 읽어서, 각 (task, recording, 정수초)마다
    '정답이어야 할' 프레임(가장 가까운 것)과 예상 motional 정보를 계산"""
    expected = {}

    for task_name in ODD_TASK_NAMES:
        task_root = os.path.join(DATA_ROOT, task_name)
        if not os.path.isdir(task_root):
            continue
        for recording_id in os.listdir(task_root):
            rec_dir = os.path.join(task_root, recording_id)
            if not os.path.isdir(rec_dir):
                continue
            motional_events_all = load_motional_events(recording_id)
            files = glob.glob(os.path.join(rec_dir, "*", "*_tag.json"))
            for fp in files:
                try:
                    with open(fp, "r", encoding="utf-8") as f:
                        doc = json.load(f)
                except json.JSONDecodeError:
                    continue
                ts = extract_timestamp_s(doc.get("imagePath", ""))
                if ts is None:
                    continue
                whole_second = round(ts)
                diff = abs(ts - whole_second)
                key = (task_name, recording_id, whole_second)

                if key in expected and expected[key]["diff"] <= diff:
                    continue

                tags, exact, events = match_motional(ts, motional_events_all)
                expected[key] = {
                    "diff": diff,
                    "uuid": doc["uuid"],
                    "timestamp_s": ts,
                    "tags": doc["tags"],
                    "motional_scenarios": tags,
                    "motional_exact": exact,
                    "motional_events": events,
                }
    return expected


def fetch_es_doc(doc_id):
    res = requests.get(f"{ES_URL}/{ES_INDEX}/_doc/{doc_id}", auth=ES_AUTH)
    if res.status_code != 200:
        return None
    return res.json().get("_source")


def normalize_events(events):
    """비교용 정규화 (부동소수점 오차 방지)"""
    return [
        (e.get("scenario"), round(e.get("start", 0), 4),
         round(e.get("end", 0), 4), bool(e.get("exact")))
        for e in (events or [])
    ]


def main():
    print("1) 원본 파일 기준 '정답' 재계산 중...")
    expected = build_expected()
    print(f"   → 예상 문서 수: {len(expected)}개\n")

    print("2) Elasticsearch와 전수 대조 중...")
    total = len(expected)
    ok = 0
    mismatches = []

    for (task_name, recording_id, whole_second), exp in expected.items():
        doc_id = f"{task_name.lower()}-{recording_id}-{whole_second}"
        actual = fetch_es_doc(doc_id)

        if actual is None:
            mismatches.append((doc_id, "ES에 문서 자체가 없음"))
            continue

        problems = []

        # ① UUID 일치 (가장 가까운 프레임이 실제로 들어갔는지)
        if actual.get("uuid") != exp["uuid"]:
            problems.append(f"uuid 다름 (기대:{exp['uuid'][:8]}, 실제:{actual.get('uuid','')[:8]})")

        # ② ODD 태그 내용 일치
        if actual.get("tags") != exp["tags"]:
            problems.append("tags 내용 불일치")

        # ③ motional_scenarios 일치
        actual_motional = sorted(actual.get("motional_scenarios", []))
        expected_motional = sorted(exp["motional_scenarios"])
        if actual_motional != expected_motional:
            problems.append(f"motional_scenarios 다름 (기대:{expected_motional}, 실제:{actual_motional})")

        # ④ motional_exact 일치
        actual_exact = sorted(actual.get("motional_exact", []))
        expected_exact = sorted(exp["motional_exact"])
        if actual_exact != expected_exact:
            problems.append(f"motional_exact 다름 (기대:{expected_exact}, 실제:{actual_exact})")

        # ⑤ motional_events(구간 정보) 일치
        actual_events = normalize_events(actual.get("motional_events"))
        expected_events = normalize_events(exp["motional_events"])
        if actual_events != expected_events:
            problems.append(
                f"motional_events 다름 (기대 {len(expected_events)}건, 실제 {len(actual_events)}건)")

        # ⑥ 모든 검사 후 최종 판정
        if problems:
            mismatches.append((doc_id, "; ".join(problems)))
        else:
            ok += 1

    print(f"\n=== 감사 결과 ===")
    print(f"전체 검사 대상: {total}개")
    print(f"완전 일치: {ok}개")
    print(f"불일치/누락: {len(mismatches)}개")

    if mismatches:
        print(f"\n=== 불일치 상세 (최대 20개) ===")
        for doc_id, reason in mismatches[:20]:
            print(f"  ✗ {doc_id}: {reason}")
    else:
        print("\n✅ 모든 문서가 원본과 완벽히 일치합니다.")


if __name__ == "__main__":
    main()