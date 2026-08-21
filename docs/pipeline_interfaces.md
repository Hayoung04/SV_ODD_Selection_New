# 파이프라인 입출력 인터페이스 (Pipeline I/O Reference)

> 작성자: 김하영(인턴) · 작성일: 2026-08-21
> 목적: 다음 담당자가 코드를 안 읽고도 "각 컴포넌트에 뭘 넣으면 뭐가 나오는지" 파악할 수
> 있도록, 실제 코드에서 확인한 필드명 그대로 정리한 인터페이스 레퍼런스입니다. 배경/설계
> 이유는 [인수인계_상세본.md](../handOver/인수인계_상세본.md)를 참고하고, 여기는 스키마만
> 다룹니다.

---

## 1-1. Kafka 입력 메시지 (`dev.shared.odd-tagging.input` 토픽)

`kafka/kafka_message_handler.py`가 이 토픽의 메시지 1건을 받아 `extract_message_type()`으로
먼저 타입을 판별한 뒤, 타입별 `extract_*_fields()`로 필드를 뽑아냅니다.

### 타입 판별 로직 (`extract_message_type()`)

| 우선순위 | 조건 | 판별 타입 |
| --- | --- | --- |
| 1 | 메시지가 `{"jobId": ...}` 단 하나의 키만 가짐 | `test` (연결 테스트, 무시) |
| 2 | `aaa1_tags` 또는 `lsd_tags` 키가 있음, 또는 `taskName`이 `"AAA1"`/`"LSD"` | `odd` |
| 3 | `odd_selection_scenario_events` 키가 있음, 또는 `type` 값에 `"motional"` 문자열 포함 | `motional` |
| 4 | 위 어느 것도 아님 | `unknown` (원본 로그만 남기고 처리 안 함) |

판별은 이 순서대로 순차 검사되므로, 예를 들어 `aaa1_tags`와 `odd_selection_scenario_events`가
동시에 있으면 `odd`로 분류됩니다(2번이 3번보다 먼저 체크됨).

### 연결 테스트 메시지

```json
{ "jobId": "test-001" }
```

### ODD 메시지 — `inline` 모드 (`extract_odd_fields()`)

메시지 안에 `imagePath` 키가 있으면 `inline` 모드로 판단하고, 메시지 전체를 그대로
`doc`(색인 대상 원본 문서)으로 사용합니다.

```json
{
  "uuid": "3f67eb6c-4961-4273-a900-e86cb25e9517",
  "taskName": "AAA1",
  "imagePath": "/mnt/data-center/scenes/GER/MACHET18/260423/Rec_Drv_GER_MACHET18_20260423_084825/images/2160p_h120_front/GER_MACHET18_20260423_084825_1776926908999056_2160p_h120_front.jpg",
  "tags": {
    "aaa1_tags": {
      "contaminated_lane": "absent",
      "road_boundary_flat": "present",
      "time": "day",
      "da_road_type": "public_road_city",
      "weather": "clear"
    }
  }
}
```

### ODD 메시지 — `file_ref` 모드 (`extract_odd_fields()`)

`imagePath`는 없고 `path` 또는 `file_path` 키가 있으면 `file_ref` 모드로 판단합니다. 메시지에는
경로만 담겨 오고, 핸들러가 그 경로의 파일을 직접 읽어 실제 데이터(위 inline 모드와 동일한
구조)를 얻습니다. `taskName`이 없으면 기본값 `"AAA1"`을 사용합니다.

```json
{
  "jobId": "odd-verify-001",
  "taskName": "AAA1",
  "path": "/home/odd-selection/machet18_data/AAA1/Rec_Drv_GER_MACHET18_20260423_084825/20260807/3f67eb6c-4961-4273-a900-e86cb25e9517_tag.json"
}
```

### Motional 메시지 (`extract_motional_fields()`)

Motional 메시지는 `inline` 모드가 없고 항상 `file_ref` 방식만 지원합니다. `path` 또는
`file_path` 키로 이벤트 파일 경로만 전달됩니다.

```json
{
  "jobId": "motional-verify-001",
  "type": "motional",
  "recording_id": "Rec_Drv_GER_MACHET18_20260423_084825",
  "path": "/home/odd-selection/machet18_data/motional/Rec_Drv_GER_MACHET18_20260423_084825_odd_selection_tags_v2.json"
}
```

이 경로의 파일은 `{"tags": {"odd_selection_scenario_events": {<scenario>: [{"start_timestamp_s":
..., "end_timestamp_s": ...}, ...]}}}` 구조를 가지며, `pipeline.parse_motional_file()`이 이를
`(start, end, scenario)` 튜플 리스트로 변환합니다.

⚠ 실제 운영 스키마가 확정되면 `kafka_message_handler.py`의 `extract_message_type`,
`extract_odd_fields`, `extract_motional_fields` 3개 함수만 수정하면 됩니다(파일 상단 주석에도
동일하게 명시되어 있음).

---

## 1-2. Elasticsearch 저장 문서 스키마 (`odd-frames-v2` 인덱스)

`scripts/unified_pipeline.py`의 `process_odd_tag_file()` / `kafka_message_handler.py`의
`_index_odd_doc()` 두 경로(폴링/Kafka) 모두 동일한 구조의 문서를 만들어 `queue_document()` →
`flush_bulk()`로 색인합니다.

| 필드 | 타입 | 설명 |
| --- | --- | --- |
| `uuid` | string | 원본 ODD 태깅 파일의 uuid |
| `taskName` | string | `"AAA1"` 또는 `"LSD"` |
| `recording_id` | string | 주행 레코딩 ID (예: `Rec_Drv_GER_MACHET18_20260423_084825`), `imagePath`의 `Rec_Drv_` 접두 경로 조각에서 추출 |
| `timestamp_s` | float | `imagePath` 파일명의 마이크로초 epoch를 초 단위로 변환한 촬영 시각 |
| `whole_second` | int | `timestamp_s`를 반올림한 정수초 (1fps 다운샘플링 키) |
| `imagePath` | string | 원본 이미지 파일 경로 |
| `tags` | object | 원본 ODD 태그 전체 (`{"aaa1_tags": {...}}` 형태, taskName에 따라 필드 구성이 다름) |
| `motional_scenarios` | string[] | 매칭된 Motional 시나리오 이름 목록 (검색용, 정렬·중복제거됨) |
| `motional_exact` | string[] | 그중 프레임 시각이 실제로 구간 안에 포함되는 시나리오 이름만 |
| `motional_events` | object[] | 매칭된 개별 이벤트 상세: `{"scenario": str, "start": float, "end": float, "duration": float, "exact": bool}` (구간 길이 내림차순 정렬) |

문서 ID(`_id`)는 `f"{task_name.lower()}-{recording_id}-{whole_second}"` 형식으로 고정되어,
같은 정수초에 더 정확한 프레임이 나중에 들어오면 자동으로 덮어써집니다.

```json
{
  "uuid": "3f67eb6c-4961-4273-a900-e86cb25e9517",
  "taskName": "AAA1",
  "recording_id": "Rec_Drv_GER_MACHET18_20260423_084825",
  "timestamp_s": 1776926908.999056,
  "whole_second": 1776926909,
  "imagePath": "/mnt/data-center/scenes/GER/MACHET18/260423/Rec_Drv_GER_MACHET18_20260423_084825/images/2160p_h120_front/GER_MACHET18_20260423_084825_1776926908999056_2160p_h120_front.jpg",
  "tags": {
    "aaa1_tags": {
      "contaminated_lane": "absent",
      "road_boundary_flat": "present",
      "time": "day",
      "da_road_type": "public_road_city",
      "weather": "clear"
    }
  },
  "motional_scenarios": ["stationary", "traversing_crosswalk"],
  "motional_exact": ["stationary"],
  "motional_events": [
    {"scenario": "stationary", "start": 1776926908.5, "end": 1776926910.2, "duration": 1.7, "exact": true},
    {"scenario": "traversing_crosswalk", "start": 1776926909.3, "end": 1776926909.6, "duration": 0.3, "exact": false}
  ]
}
```

doc_id 예시: `aaa1-Rec_Drv_GER_MACHET18_20260423_084825-1776926909`

---

## 1-3. `odd-reviews` 인덱스 스키마

검수 UI(`review.html`)에서 Accept/Reject 판정을 저장하는 인덱스. `ui/server.py`의
`ensure_review_index()`가 아래 매핑으로 인덱스가 없으면 자동 생성하고, `POST /api/review`가
문서를 쓰기/삭제합니다. 문서 ID는 `frame_uuid`로 고정(1 프레임당 최신 판정 1건만 유지).

| 필드 | 타입 | 설명 |
| --- | --- | --- |
| `frame_uuid` | keyword | 판정 대상 프레임의 uuid (문서 ID와 동일 값) |
| `doc_id` | keyword | 해당 프레임의 `odd-frames-v2` 문서 ID |
| `taskName` | keyword | `"AAA1"` 또는 `"LSD"` |
| `recording_id` | keyword | 레코딩 ID |
| `whole_second` | long | 정수초 |
| `status` | keyword | `accept` \| `reject` \| `pending` |
| `note` | text | 검수자 메모 (선택) |
| `updated_at` | date | 판정 시각 (UTC ISO 8601) |

```json
{
  "frame_uuid": "3f67eb6c-4961-4273-a900-e86cb25e9517",
  "doc_id": "aaa1-Rec_Drv_GER_MACHET18_20260423_084825-1776926909",
  "taskName": "AAA1",
  "recording_id": "Rec_Drv_GER_MACHET18_20260423_084825",
  "whole_second": 1776926909,
  "status": "accept",
  "note": "",
  "updated_at": "2026-08-21T05:12:33.120450+00:00"
}
```

`status`가 빈 값으로 `POST /api/review`에 오면(판정 취소) 문서를 삭제합니다.

---

## 1-4. `ui/server.py` HTTP API 엔드포인트

`server.py`는 프론트(`index.html`/`review.html`)와 Elasticsearch 사이의 인증 프록시입니다.
아래 6개 엔드포인트만 제공합니다.

### `POST /api/search`

- 요청 바디: Elasticsearch bool 쿼리 JSON(그대로 `{"query": <body>, "size": 10000}`로 감싸서
  `_search`에 전달됨)
  ```json
  {"bool": {"filter": [{"term": {"taskName": "AAA1"}}]}}
  ```
- 응답:
  ```json
  {"hits": [ { /* odd-frames-v2 문서(_source) */ } ], "total": 976}
  ```

### `GET /api/count`

- 요청: 파라미터 없음
- 응답: `{"count": 976}` — `odd-frames-v2`의 `_count` 결과. 프론트가 폴링해서 자동 갱신 감지용으로 사용.

### `GET /api/image?path=<이미지 절대경로>`

- `IMAGE_ALLOWED_ROOTS` 아래 + 허용 확장자(`.jpg`/`.jpeg`/`.png`/`.webp`/`.bmp`)일 때만 이미지
  바이너리를 그대로 응답. `realpath` 검사로 `../` 우회 차단.
- 허용 경로 밖이면 `403 Forbidden`, 파일 없으면 `404`.

### `POST /api/nlquery`

- 요청: `{"query": "야간 우천 시 보행자 횡단 장면"}` (엔드포인트 코드상 키는 `"query"`이며, 문서
  전체 표기 통일성을 위해 배경 섹션에서 `text`로 언급됐던 것과 달리 실제 구현은 `query` 키를
  읽음)
- 응답: 1-5의 `parse_query()` 반환값 그대로.

### `GET /api/reviews?task=<taskName>`

- `task` 생략 시 전체 조회.
- 응답:
  ```json
  {
    "reviews": {
      "3f67eb6c-4961-4273-a900-e86cb25e9517": {"status": "accept", "updated_at": "2026-08-21T05:12:33.120450+00:00"}
    },
    "total": 1
  }
  ```

### `POST /api/review`

- 요청: 1-3의 `odd-reviews` 문서 필드와 동일 (`frame_uuid` 필수, 나머지는 `body.get(...)`으로
  선택적으로 읽음)
  ```json
  {
    "frame_uuid": "3f67eb6c-4961-4273-a900-e86cb25e9517",
    "doc_id": "aaa1-Rec_Drv_GER_MACHET18_20260423_084825-1776926909",
    "taskName": "AAA1",
    "recording_id": "Rec_Drv_GER_MACHET18_20260423_084825",
    "whole_second": 1776926909,
    "status": "accept",
    "note": ""
  }
  ```
- 응답: `{"ok": true, "saved": {...}}` (저장) 또는 `{"ok": true, "deleted": true}` (`status`가 빈 값일 때)

---

## 1-5. vLLM 자연어 파서 (`query_proto/vllm_query_parser_v20.py`)

`parse_query(user_query: str) -> dict` 함수 하나가 인터페이스 전체입니다. `ui/server.py`가
`/api/nlquery`에서 이 함수를 그대로 호출합니다.

- **입력**: 자연어 문장 1개 (한국어/영어 모두 가능), `str`
- **출력**: `{"query": <조건 트리 또는 null>, "unmapped_terms": [<str>, ...]}`

`query`는 아래 두 형태 중 하나가 재귀적으로 중첩된 트리입니다.

**atomic condition** (5종):
| field | 형태 |
| --- | --- |
| Motional 시나리오 | `{"field": "motional_scenario", "value": "<시나리오명>"}` |
| AAA1/LSD 태그 | `{"field": "aaa1_tag", "key": "<태그명>", "value": "present\|absent\|unknown"}` |
| 시간대 | `{"field": "time", "value": "day\|dawn_evening\|night"}` (LSD 태그로부터 자동 추론된 경우 `"auto": true` 추가) |
| 도로 유형 | `{"field": "da_road_type", "value": "<도로유형>"}` |
| 날씨 | `{"field": "weather", "value": "clear\|rainy\|heavy_rainy\|snow\|fog"}` |

**Boolean 그룹**:
- AND: `{"all": [<condition-or-group>, ...]}`
- OR: `{"any": [<condition-or-group>, ...]}`

`query`가 `null`이면 매칭되는 조건이 하나도 없다는 뜻이며, 이때는 `unmapped_terms`만 채워집니다.

### 예시 1 — 단순 조건

입력: `"왼쪽으로 차선 변경하는 장면"`

```json
{"query": {"field": "motional_scenario", "value": "changing_lane_to_left"}, "unmapped_terms": []}
```

### 예시 2 — 중첩 all/any 구조 + 일부 미매칭

입력: `"밤이나 비 오는 상황에서 시내에 보행자가 많고 차선 변경하거나 우회전 또는 좌회전하는 장면"`

```json
{
  "query": {
    "all": [
      {"any": [
        {"field": "time", "value": "night"},
        {"field": "weather", "value": "rainy"}
      ]},
      {"field": "da_road_type", "value": "public_road_city"},
      {"field": "aaa1_tag", "key": "crowded_pedestrian", "value": "present"},
      {"any": [
        {"field": "motional_scenario", "value": "changing_lane"},
        {"field": "motional_scenario", "value": "starting_right_turn"},
        {"field": "motional_scenario", "value": "starting_left_turn"}
      ]}
    ]
  },
  "unmapped_terms": []
}
```

### 예시 3 — 부분 매칭 + unmapped_terms

입력: `"위험한 장면"`

```json
{"query": null, "unmapped_terms": ["위험한"]}
```

내부적으로 `parse_query()`는 vLLM 모델 원본 응답을 받은 뒤 `validate_and_sanitize()`(taxonomy에
없는 field/key/value 제거 → `unmapped_terms`로 이동)와 `enforce_lsd_implies_night()`(LSD 태그가
있는데 명시적 night 언급이 없으면 `time=night, auto:true`를 결정론적으로 추가)를 거쳐 최종
결과를 만듭니다. 필요 환경변수: `VLLM_BASE_URL`, `VLLM_MODEL_NAME`, `VLLM_API_KEY`.

---

## 1-6. Kafka Producer 스크립트 (`kafka/motional_producer.py`, `kafka/odd_producer.py`)

두 스크립트 모두 검증/재발행용 도구로, ODD 서비스가 반응하지 않도록 전용 jobId 접두사를
사용합니다(`odd-verify-`, `motional-verify-`). `--all`(전체 재발행) / `--one`(1건만) /
`--dry-run`(발행 안 함, 대상만 출력) 옵션 공통 지원.

### `motional_producer.py`

- **입력**: `machet18_data/motional/*_odd_selection_tags_v2.json` — 이미 발행한 파일은
  `kafka/motional_published_state.json`(`{recording_id: jobId}`)에 기록해 신규 파일만 자동 감지
- **출력**: 토픽 `dev.shared.odd-tagging.input`에 발행하는 메시지

  ```json
  {
    "jobId": "motional-verify-001",
    "type": "motional",
    "recording_id": "Rec_Drv_GER_MACHET18_20260423_084825",
    "path": "/home/odd-selection/machet18_data/motional/Rec_Drv_GER_MACHET18_20260423_084825_odd_selection_tags_v2.json"
  }
  ```

  jobId 패턴: `motional-verify-{next_seq:03d}` (예: `motional-verify-001`, `motional-verify-002`, ...)

### `odd_producer.py`

- **입력**: `machet18_data/{AAA1,LSD}/{recording_id}/*/*_tag.json` — 이미 발행한 파일 경로는
  `kafka/odd_published_state.json`(`{file_path: jobId}`)에 기록. `--recording=<recording_id>`로
  특정 레코딩만 대상 지정 가능
- **출력**: `file_ref` 방식(파일 경로만 전달) 메시지

  ```json
  {
    "jobId": "odd-verify-001",
    "taskName": "AAA1",
    "path": "/home/odd-selection/machet18_data/AAA1/Rec_Drv_GER_MACHET18_20260423_084825/20260807/3f67eb6c-4961-4273-a900-e86cb25e9517_tag.json"
  }
  ```

  jobId 패턴: `odd-verify-{next_seq:03d}`
