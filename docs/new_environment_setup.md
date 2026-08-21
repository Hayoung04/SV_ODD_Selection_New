# 신규 독립 환경 구축 가이드

> 작성자: 김하영(인턴) · 작성일: 2026-08-21
> 대상: strad35를 전혀 거치지 않고, 다른 서버(또는 상시구동 가능한 기기)에 Kafka Consumer
> 포함 전체 파이프라인을 새로 구축하고 싶은 경우. **당장 필요한 작업은 아니며, 나중에 필요할
> 경우를 대비해 미리 준비해둔 문서입니다.**

⚠️ Kafka Consumer/cron은 24시간 상시구동이 전제인 서비스입니다. 노트북처럼 껐다 켰다 하는
기기보다는 항상 켜져 있는 서버에 두는 것을 권장합니다. (검색/검수 UI만 원할 경우
[remote_connection_guide.md](./remote_connection_guide.md) 참고 — 이 문서보다 훨씬 간단합니다)

## 0. 필요한 것 (본인이 직접 준비)

- [ ] Qumulo 마운트 접근 권한
- [ ] AWS IAM 키 신규 발급 (`odd-msk`, `odd-indexer` profile)
- [ ] Docker 실행 가능한 서버/기기

## 1. Elasticsearch/Kibana 구축

strad35의 `elk/docker-compose.yml`, `elk/.env`를 그대로 참고해 동일한 구성으로 새 서버에
Docker Compose를 띄웁니다. Confluence "Step 1" 절차(vm.max_map_count 설정 등 OS 레벨
사전 준비 포함)를 그대로 따르세요. `elk/.env`의 `ES_PORT`, `KIBANA_PORT`, `ES_MEM`,
`ES_DATA_PATH`는 새 서버 환경에 맞게 값을 새로 정하면 됩니다 (strad35와 같은 값일 필요 없음).

## 2. 인덱스 생성

`odd-frames-v2`와 **동일한 매핑(dynamic_templates 포함)**으로 신규 인덱스를 생성합니다.
strad35의 클러스터에서 `GET odd-frames-v2/_mapping`으로 매핑을 확인한 뒤 그대로 새 인덱스에
적용하면 됩니다. **인덱스 이름은 strad35와 겹치지 않게 구분**하세요 (예:
`odd-frames-v2-<본인이름>` 등 — 같은 이름을 써도 물리적으로 다른 클러스터라 충돌은 없지만,
나중에 두 환경을 혼동하지 않도록 이름부터 구분해두는 것을 권장합니다).

검수 결과용 `odd-reviews` 인덱스는 별도로 만들 필요 없습니다 — `ui/server.py`가 최초 실행 시
`ensure_review_index()`로 자동 생성합니다 (매핑은
[pipeline_interfaces.md](./pipeline_interfaces.md) 1-3 참고).

## 3. Kafka 연동 — 같은 토픽, 다른 Consumer Group

- **토픽**: `dev.shared.odd-tagging.input` (기존과 동일, 변경 불필요 — 모든 서비스가 같은
  토픽을 공유해서 구독하는 구조)
- **Consumer Group**: 본인만의 고유 `group.id`를 사용해야 합니다. strad35의
  `odd-selection-consumer`와 겹치면 안 됩니다 (같은 그룹 ID를 쓰면 오프셋을 공유하게 되어
  strad35 파이프라인의 처리 상태에 영향을 줄 수 있음).
- 고유한 `group.id`로 독립적으로 토픽을 구독하면, strad35 파이프라인에 전혀 영향을 주지 않고
  토픽 전체를 처음부터(`auto.offset.reset: earliest`) 새로 읽어올 수 있습니다.
- Consumer Group의 전체 구조(다른 서비스들이 어떤 그룹으로 이 토픽을 나눠 쓰는지)는 Confluence
  "Step 4" 11번 섹션을 참고하세요.

## 4. 파이프라인 스크립트 실행

`unified_pipeline.py`(폴링 경로) / `kafka_consumer.py`(Kafka 경로)를 본인 서버용 `.env`로
실행합니다. 이미 `ES_URL`, `ES_INDEX`는 환경변수로 오버라이드 가능하도록 파라미터화되어
있습니다 ([pipeline_interfaces.md](./pipeline_interfaces.md) 및 코드 참고). `KAFKA_GROUP_ID`는
현재 `kafka_consumer.py`에 하드코딩(`group.id: "odd-selection-consumer"`)되어 있으므로, 새
환경에서는 이 값을 3번에서 정한 고유 group.id로 직접 수정해서 실행하세요 (strad35 쪽
`kafka_consumer.py`는 절대 건드리지 말 것 — 새 서버에 복사한 사본만 수정).

```bash
# 새 서버의 .env 예시 (scripts/.env, kafka/.env)
ES_URL=http://localhost:9200
ES_INDEX=odd-frames-v2-<본인이름>
ES_USER=elastic
ES_PASS=<새 클러스터 비밀번호>

KAFKA_BROKER=<MSK 브로커 주소, strad35와 동일>
KAFKA_REGION=<strad35와 동일>
AWS_PROFILE=<새로 발급받은 IAM profile 이름>
```

`DATA_ROOT`(원본 ODD/Motional 데이터 경로)는 `unified_pipeline.py`/`odd_producer.py`/
`motional_producer.py`에 하드코딩되어 있습니다. 새 서버에서 Qumulo를 마운트한 경로가 strad35와
다르다면(대부분 다를 것입니다) 각 스크립트의 `DATA_ROOT`/`MOTIONAL_DIR` 값을 새 경로로 직접
수정하세요.

## 5. 이미지 마운트, UI 실행, 검증

이미지 스토리지 마운트와 UI(`ui/server.py`) 실행 방식은
[remote_connection_guide.md](./remote_connection_guide.md)의 2~4번과 동일한 방식을 그대로
재사용하면 됩니다 (단, 이 환경에서는 `ES_URL`을 SSH 터널이 아니라 로컬 `localhost:9200`으로
설정 — 같은 서버에서 ES와 UI가 함께 돌기 때문). 데이터가 정상 유입되는지는
`scripts/audit_pipeline.py`로 전수 검증하는 것을 권장합니다.
