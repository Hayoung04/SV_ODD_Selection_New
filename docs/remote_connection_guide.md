# 원격 연결 가이드 — 검색/검수 UI만 본인 PC에서 실행하기

> 작성자: 김하영(인턴) · 작성일: 2026-08-21
> 대상: strad35(10.50.32.140)의 Elasticsearch/이미지 데이터에 **원격으로 연결**해서
> 검색/검수 UI(`ui/server.py`)만 본인 PC에서 실행하고 싶은 경우. Kafka Consumer(상시구동)와
> cron(자동 발행)은 이 가이드의 범위가 아니며, **계속 strad35에서만 돌아가야 합니다.**

## 0. 전제 조건

- strad35 SSH 접속 권한 (기존 인수인계 자격증명 참고: [인수인계_자격증명관리.md](../handOver/인수인계_자격증명관리.md))
- 본인 PC에 이 레포 클론 완료 (`ui/`, `query_proto/` 디렉토리 포함)
- 본인 PC에서 회사 네트워크(Qumulo)에 접근 가능한 상태 (VPN 등, 인프라 담당자 문의)

## 1. Elasticsearch 원격 접속 — SSH 터널

strad35의 Elasticsearch는 `localhost:9200`에만 바인딩되어 있어 외부에서 직접 접속할 수
없습니다. SSH 로컬 포트포워딩으로 본인 PC의 로컬 포트를 strad35의 9200으로 연결합니다.

```bash
ssh -L 9200:127.0.0.1:9200 <계정>@10.50.32.140
```

- 이 터미널 창은 UI를 쓰는 동안 계속 열어둬야 합니다 (닫으면 터널도 끊김).
- 접속 확인: 새 터미널에서 `curl -u <ES_USER>:<ES_PASS> http://localhost:9200` 실행 시 클러스터
  정보가 응답하면 정상.

## 2. Qumulo 이미지 스토리지 마운트

이미지 원본은 회사 네트워크 공유 스토리지(Qumulo)에 있으며, strad35 전용이 아니라 회사
네트워크에 접속 가능한 PC라면 직접 마운트할 수 있습니다. **구체적인 마운트 명령/자격증명은
인프라 담당자에게 문의하세요** (마운트 방식이 SMB/NFS인지, 사내 VPN이 필요한지 등은 이
프로젝트 범위 밖의 인프라 설정입니다).

마운트가 끝나면 본인 PC에서 이미지 파일들이 접근 가능한 로컬 경로(예: `/Volumes/data-center`,
`Z:\data-center` 등, OS에 따라 다름)를 확인해두세요. 이 경로를 3번에서 `IMAGE_ALLOWED_ROOTS`에
씁니다.

## 3. `.env` 설정

이 레포에는 `.env` 파일이 3곳(`ui/.env`, `scripts/.env`, `kafka/.env`)에 있는데, **검색/검수
UI만 실행하는 이 가이드에서 수정해야 하는 건 `ui/.env` 하나뿐입니다.**

| `.env` 파일 | 이 가이드에서 손댈 대상? | 이유 |
| --- | --- | --- |
| `ui/.env` | ✅ 본인 PC용으로 새로 작성 | `server.py`(검색/검수 UI)가 읽는 설정 — 이 문서의 대상 |
| `scripts/.env` | ❌ 손댈 필요 없음 | `unified_pipeline.py`/`audit_pipeline.py`(폴링 파이프라인·감사 스크립트) 전용 — strad35에서만 실행 |
| `kafka/.env` | ❌ 손댈 필요 없음, **본인 PC에 만들지도 말 것** | `kafka_consumer.py`/`motional_producer.py`(Kafka 인증) 전용 — 아래 경고 참고, 이 두 스크립트 자체를 본인 PC에서 실행하면 안 됨 |

`ui/.env`를 본인 PC 기준으로 새로 작성합니다(strad35의 `ui/.env`를 그대로 복사하면 안 됨 —
경로가 다름). `server.py`는 아래 값들을 전부 `os.environ.get(키, 기본값)` 형태로 읽으므로,
설정하지 않으면 strad35 로컬 실행 기준 기본값(`localhost:9200`, `/mnt/data-center/scenes`)으로
동작합니다 — **strad35 기존 운영에는 영향 없습니다.**

```bash
# ui/.env (본인 PC용)
ES_USER=elastic
ES_PASS=<비밀번호>

# 1번에서 연 SSH 터널 포트를 그대로 사용
ES_URL=http://localhost:9200

# 2번에서 마운트한 본인 PC의 실제 경로로 교체 (여러 개면 콜론으로 구분)
IMAGE_ALLOWED_ROOTS=/Volumes/data-center/scenes

# AI 자연어 검색까지 쓰려면 (선택) — vLLM 서버 접속 정보는 기존 문서 참고
VLLM_BASE_URL=http://127.0.0.1:8001/v1
VLLM_MODEL_NAME=Qwen/Qwen3-14B-FP8
VLLM_API_KEY=dummy
```

> `ES_INDEX`, `REVIEW_INDEX`, `UI_PORT`도 같은 방식으로 환경변수 지원되며, 생략하면 각각
> `odd-frames-v2`, `odd-reviews`, `8080` 기본값을 사용합니다.

## 4. 실행

```bash
cd ui
python3 server.py
```

브라우저에서 `http://localhost:8080` 접속 → 태그 검색/이미지 로드가 정상 동작하면 완료입니다.

## ⚠️ 절대 본인 PC에서 실행하면 안 되는 것

- **`kafka/kafka_consumer.py`** (Kafka Consumer, 상시구동)
- **`kafka/motional_producer.py`** (Motional 자동 발행, cron 등록 대상)

이 두 스크립트는 strad35에서 **중앙집중으로만** 실행되어야 합니다. 본인 PC에서 동시에
실행하면 같은 Kafka 토픽/Consumer Group을 두 프로세스가 동시에 처리하게 되어 데이터 처리
충돌(중복 색인, 오프셋 커밋 경합 등)이 발생합니다. 검색/검수 UI만 실행하는 이 가이드의
범위에서는 두 스크립트를 건드릴 필요가 전혀 없습니다.

완전히 독립된 별도 파이프라인(Consumer 포함)을 새로 구축하고 싶은 경우는 이 문서가 아니라
[new_environment_setup.md](./new_environment_setup.md)를 참고하세요.
