import os
import json
import time
import requests

WATCH_DIR = "/mnt/qumulo3/datagroup/SYN/Intern/hayoung/odd-search-polling-test"
POLL_INTERVAL = 5

ES_URL = "http://localhost:9200"
ES_INDEX = "odd-frames-v2"
ES_AUTH = ("elastic", "changeme_odd_2026")


def parse_tag_file(filepath):
    with open(filepath, "r", encoding="utf-8") as f:
        data = json.load(f)

    return {
        "uuid": data.get("uuid"),
        "taskName": data.get("taskName"),
        "promptVersion": data.get("promptVersion"),
        "imagePath": data.get("imagePath"),
        "tags": data.get("tags", {}),
    }


def index_document(doc):
    doc_id = doc["uuid"]
    res = requests.put(
        f"{ES_URL}/{ES_INDEX}/_doc/{doc_id}",
        auth=ES_AUTH,
        json=doc,
    )
    if res.status_code not in (200, 201):
        print(f"  ✗ 색인 실패: {doc_id} - {res.text}")
    else:
        print(f"  ✓ 색인 완료: {doc_id} (task={doc['taskName']})")


def process_file(filepath):
    try:
        doc = parse_tag_file(filepath)
        index_document(doc)
    except json.JSONDecodeError:
        print(f"  ✗ JSON 파싱 실패 (쓰다 만 파일일 수 있음): {filepath}")
    except Exception as e:
        print(f"  ✗ 처리 중 에러: {filepath} - {e}")


def main():
    print(f"폴링 시작: {WATCH_DIR}")
    seen_files = set()

    while True:
        try:
            current_files = {
                f for f in os.listdir(WATCH_DIR) if f.endswith(".json")
            }
        except FileNotFoundError:
            print("감시 폴더를 찾을 수 없습니다. 경로를 확인하세요.")
            time.sleep(POLL_INTERVAL)
            continue

        new_files = current_files - seen_files
        for fname in new_files:
            print(f"새 파일 발견: {fname}")
            process_file(os.path.join(WATCH_DIR, fname))

        seen_files = current_files
        time.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    main()
