"""
ODD 검색/검수 UI 백엔드 프록시

보안 관련 정책
  1) Elasticsearch 인증 정보는 환경변수(.env)로만 주입. 코드에 기본 비밀번호 없음.
  2) /api/image 는 허용된 경로 아래 파일만 서빙 (realpath 기준 검사로 ../ 우회 차단).
  3) 검수 결과는 별도 인덱스(odd-reviews)에 영구 저장 — 여러 명이 나눠 검수 가능.
"""
import http.server
import socketserver
import json
import os
import sys
import urllib.request
import urllib.error
import urllib.parse
import mimetypes
import base64
from datetime import datetime, timezone

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "query_proto"))
from vllm_query_parser_v18 import parse_query as nlp_parse_query

PORT = int(os.environ.get("UI_PORT", "8080"))

ES_URL = os.environ.get("ES_URL", "http://localhost:9200")
ES_INDEX = os.environ.get("ES_INDEX", "odd-frames-v2")
REVIEW_INDEX = os.environ.get("REVIEW_INDEX", "odd-reviews")

# ── ① 인증 정보: 환경변수에서만 읽음 (기본값 없음) ──
ES_USER = os.environ.get("ES_USER")
ES_PASS = os.environ.get("ES_PASS")

# ── ② 이미지 서빙 허용 경로 (여러 개면 콜론으로 구분) ──
#    예: IMAGE_ALLOWED_ROOTS=/mnt/data-center/scenes:/mnt/qumulo3/datagroup
IMAGE_ALLOWED_ROOTS = [
    os.path.realpath(p)
    for p in os.environ.get(
        "IMAGE_ALLOWED_ROOTS", "/mnt/data-center/scenes"
    ).split(":")
    if p.strip()
]
ALLOWED_IMAGE_EXT = {".jpg", ".jpeg", ".png", ".webp", ".bmp"}


def load_dotenv(path):
    """의존성 없이 .env 파일을 읽어 환경변수로 주입 (이미 설정된 값은 유지)"""
    if not os.path.exists(path):
        return
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key, value = key.strip(), value.strip().strip('"').strip("'")
            if key and key not in os.environ:
                os.environ[key] = value


def es_request(path, payload=None, method="POST"):
    """Elasticsearch에 인증 포함 요청. 인증 정보는 이 서버 안에만 존재."""
    url = f"{ES_URL}{path}"
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    token = base64.b64encode(f"{ES_USER}:{ES_PASS}".encode()).decode()
    req.add_header("Authorization", f"Basic {token}")
    with urllib.request.urlopen(req, timeout=30) as res:
        body = res.read().decode("utf-8")
        return json.loads(body) if body else {}


def is_allowed_image(path):
    """② 경로 검사: realpath로 심볼릭 링크·상대경로를 모두 해석한 뒤
       허용 루트 아래인지, 허용 확장자인지 확인"""
    if not path:
        return False
    real = os.path.realpath(path)
    if os.path.splitext(real)[1].lower() not in ALLOWED_IMAGE_EXT:
        return False
    for root in IMAGE_ALLOWED_ROOTS:
        # os.sep을 붙여 /mnt/data-center-secret 같은 접두어 우회 차단
        if real == root or real.startswith(root + os.sep):
            return True
    return False


def ensure_review_index():
    """③ 검수 결과 인덱스가 없으면 생성"""
    try:
        es_request(f"/{REVIEW_INDEX}", method="HEAD")
        return
    except urllib.error.HTTPError as e:
        if e.code != 404:
            print(f"  ⚠ 검수 인덱스 확인 실패: {e}")
            return
    except Exception as e:
        print(f"  ⚠ ES 연결 실패: {e}")
        return

    mapping = {
        "mappings": {
            "properties": {
                "frame_uuid":   {"type": "keyword"},
                "doc_id":       {"type": "keyword"},
                "taskName":     {"type": "keyword"},
                "recording_id": {"type": "keyword"},
                "whole_second": {"type": "long"},
                "status":       {"type": "keyword"},   # accept | reject | pending
                "note":         {"type": "text"},
                "updated_at":   {"type": "date"},
            }
        }
    }
    try:
        es_request(f"/{REVIEW_INDEX}", mapping, method="PUT")
        print(f"  ✓ 검수 인덱스 생성: {REVIEW_INDEX}")
    except Exception as e:
        print(f"  ⚠ 검수 인덱스 생성 실패: {e}")


class Handler(http.server.SimpleHTTPRequestHandler):
    def _send_json(self, obj, status=200):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    # ══════════════════════════════════════════
    #  GET
    # ══════════════════════════════════════════
    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        qs = urllib.parse.parse_qs(parsed.query)

        # 인덱스 문서 수 (자동 갱신 감지용)
        if parsed.path == "/api/count":
            try:
                result = es_request(f"/{ES_INDEX}/_count", method="GET")
                self._send_json({"count": result.get("count", 0)})
            except Exception as e:
                self._send_json({"error": str(e)}, status=500)
            return

        # ② 이미지 서빙 (허용 경로만)
        if parsed.path == "/api/image":
            image_path = qs.get("path", [None])[0]
            if not is_allowed_image(image_path):
                self.send_error(403, "Forbidden path")
                return
            real = os.path.realpath(image_path)
            if not os.path.isfile(real):
                self.send_error(404, "Image Not Found")
                return
            try:
                with open(real, "rb") as f:
                    img = f.read()
                ctype, _ = mimetypes.guess_type(real)
                self.send_response(200)
                self.send_header("Content-Type", ctype or "application/octet-stream")
                self.send_header("Content-Length", str(len(img)))
                self.send_header("Cache-Control", "public, max-age=3600")
                self.end_headers()
                self.wfile.write(img)
            except Exception as e:
                self.send_error(500, f"Error reading file: {e}")
            return

        # ③ 검수 결과 조회 (taskName 단위, 여러 명 결과 통합)
        if parsed.path == "/api/reviews":
            task = qs.get("task", [None])[0]
            query = {"match_all": {}} if not task else {"term": {"taskName": task}}
            try:
                result = es_request(
                    f"/{REVIEW_INDEX}/_search",
                    {"query": query, "size": 10000},
                )
                reviews = {}
                for h in result["hits"]["hits"]:
                    s = h["_source"]
                    reviews[s["frame_uuid"]] = {
                        "status": s.get("status"),
                        "updated_at": s.get("updated_at"),
                    }
                self._send_json({"reviews": reviews, "total": len(reviews)})
            except urllib.error.HTTPError as e:
                if e.code == 404:      # 인덱스가 아직 없으면 빈 결과
                    self._send_json({"reviews": {}, "total": 0})
                else:
                    self._send_json({"error": str(e)}, status=500)
            except Exception as e:
                self._send_json({"error": str(e)}, status=500)
            return

        super().do_GET()

    # ══════════════════════════════════════════
    #  POST
    # ══════════════════════════════════════════
    def do_POST(self):
        parsed = urllib.parse.urlparse(self.path)
        length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(length).decode("utf-8") if length else "{}"

        # 검색
        if parsed.path == "/api/search":
            try:
                query = json.loads(raw)
                result = es_request(f"/{ES_INDEX}/_search", {"query": query, "size": 10000})
                hits = [h["_source"] for h in result["hits"]["hits"]]
                self._send_json({"hits": hits, "total": result["hits"]["total"]["value"]})
            except urllib.error.HTTPError as e:
                self._send_json({"error": f"ES 오류 ({e.code}): {e.read().decode('utf-8')[:200]}"}, status=500)
            except Exception as e:
                self._send_json({"error": str(e)}, status=500)
            return

        # ③ 검수 결과 저장 (단건)
        if parsed.path == "/api/review":
            try:
                body = json.loads(raw)
                frame_uuid = body.get("frame_uuid")
                status = body.get("status")
                if not frame_uuid:
                    self._send_json({"error": "frame_uuid 필요"}, status=400)
                    return

                # status가 비어있으면 판정 취소 → 문서 삭제
                if not status:
                    try:
                        es_request(f"/{REVIEW_INDEX}/_doc/{frame_uuid}", method="DELETE")
                    except urllib.error.HTTPError as e:
                        if e.code != 404:
                            raise
                    self._send_json({"ok": True, "deleted": True})
                    return

                doc = {
                    "frame_uuid": frame_uuid,
                    "doc_id": body.get("doc_id"),
                    "taskName": body.get("taskName"),
                    "recording_id": body.get("recording_id"),
                    "whole_second": body.get("whole_second"),
                    "status": status,
                    "note": body.get("note", ""),
                    "updated_at": datetime.now(timezone.utc).isoformat(),
                }
                es_request(f"/{REVIEW_INDEX}/_doc/{frame_uuid}", doc, method="PUT")
                self._send_json({"ok": True, "saved": doc})
            except Exception as e:
                self._send_json({"error": str(e)}, status=500)
            return

        # AI 자연어 검색: 문장을 파싱해 Boolean 조건 트리로 변환
        if parsed.path == "/api/nlquery":
            try:
                body = json.loads(raw)
                user_text = (body.get("query") or "").strip()
                if not user_text:
                    self._send_json({"error": "검색어를 입력하세요"}, status=400)
                    return
                result = nlp_parse_query(user_text)
                self._send_json(result)
            except Exception as e:
                self._send_json({"error": f"AI 검색 파싱 실패: {e}"}, status=500)
            return

        self.send_error(404, "Not Found")

    def log_message(self, fmt, *args):
        msg = str(args[0]) if args else ""
        # 폴링성 요청은 로그 생략
        if "/api/count" in msg or "/api/image" in msg:
            return
        super().log_message(fmt, *args)


socketserver.ThreadingTCPServer.allow_reuse_address = True

if __name__ == "__main__":
    base_dir = os.path.dirname(os.path.abspath(__file__))
    os.chdir(base_dir)

    # .env 로드 후 인증 정보 확인
    load_dotenv(os.path.join(base_dir, ".env"))
    ES_USER = os.environ.get("ES_USER")
    ES_PASS = os.environ.get("ES_PASS")

    IMAGE_ALLOWED_ROOTS = [
        os.path.realpath(p)
        for p in os.environ.get("IMAGE_ALLOWED_ROOTS", "/mnt/data-center/scenes").split(":")
        if p.strip()
    ]

    if not ES_USER or not ES_PASS:
        print("✗ Elasticsearch 인증 정보가 없습니다.")
        print("  /home/odd-selection/ui/.env 파일을 만들고 아래 두 줄을 넣어주세요:")
        print("     ES_USER=elastic")
        print("     ES_PASS=<비밀번호>")
        print("  (또는 실행 시 ES_USER=... ES_PASS=... python3 server.py)")
        sys.exit(1)

    ensure_review_index()

    with socketserver.ThreadingTCPServer(("", PORT), Handler) as httpd:
        print(f"ODD 검색/검수 UI 서버 시작: http://localhost:{PORT}")
        print(f"  -> 프레임 인덱스 : {ES_INDEX} @ {ES_URL}")
        print(f"  -> 검수 인덱스   : {REVIEW_INDEX}")
        print(f"  -> 이미지 허용 경로: {', '.join(IMAGE_ALLOWED_ROOTS)}")
        httpd.serve_forever()