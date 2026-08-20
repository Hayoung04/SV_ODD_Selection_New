# SV ODD Selection

Query-based SV Data Selection with ODD — ODD(AAA1/LSD) 태깅 데이터와 Motional 시나리오 데이터를
Kafka로 실시간 수신해 Elasticsearch에 통합 색인하고, 태그 클릭 및 자연어 문장으로 검색 →
CSV 추출 · 검수 UI(Accept/Reject)로 실행까지 이어지는 웹 시스템.

- 기간: 2026-06-22 ~ 2026-08-21 (인턴십)
- 작성자: 김하영
- 프로젝트: Motional 협업, ODD Data Selection

<p float="left">
    <img width="330" src="https://github.com/user-attachments/assets/d11981ce-4296-4f92-801f-bb074ab3db17" />
    <img width="330" src="https://github.com/user-attachments/assets/22ea27d8-f022-4f48-bbc5-3c700e7890bc" />
    <img width="330" src="https://github.com/user-attachments/assets/dd63a818-6d19-4bbb-9795-15f2db98e1ba" />
  </p>

---

## ⚠️ 새 환경에 클론하지 말고, strad35 서버를 그대로 이어받으세요

이 레포를 **다른 서버에 새로 클론해서 돌리는 건 권장하지 않습니다.** `.env` 파일만 채운다고
바로 동작하지 않습니다 — 아래처럼 서버 자체에 묶여있는 것들이 많기 때문입니다:

- Elasticsearch/Kibana는 Docker로 이미 strad35에 떠 있고, 색인된 실 데이터(976건)도 그 서버
  안에만 있음 (레포에는 설정 파일만 있고 데이터 자체는 없음, `.gitignore`로 제외됨)
- 프레임 이미지는 Qumulo 네트워크 스토리지(`/mnt/data-center`, `/mnt/qumulo3`)에 있고, 이건
  strad35에만 마운트되어 있음
- systemd 서비스(`odd-kafka-consumer`, `odd-search-ui`)는 `/etc/systemd/system/`에 있고
  레포(Git) 밖에 있어서 클론해도 안 따라옴
- AWS/Kafka 접근 권한, vLLM 서버 접속(SSH 터널)도 전부 strad35 환경 기준으로 설정되어 있음

**즉, 서버(`strad35`, 10.50.32.140)를 그대로 이어받아 접속하는 것을 전제로 설계되어 있습니다.**
새 서버로 완전히 이관하려면 위 항목을 전부 처음부터 다시 세팅해야 하므로(사실상 별도
프로젝트), 특별한 이유가 없다면 기존 서버 계정을 이어받는 방식을 권장합니다.

서버 접속 정보와 처음 접속 시 확인 순서는 [인수인계_상세본.md](./handOver/인수인계_상세본.md)
0-1 섹션 참고.

---

## 📌 시작하기 전에 (다음 담당자용)

1. **[인수인계_압축본.md](./handOver/인수인계_압축본.md)** 부터 읽고 전체 그림 파악
2. 처음 서버 접속 시 세팅 순서는 **[인수인계_상세본.md](./handOver/인수인계_상세본.md) 0-1 섹션** 참고
3. 자격증명(비밀번호/키) 재발급 및 관리는 **[인수인계_자격증명관리.md](./handOver/인수인계_자격증명관리.md)** 참고
4. 더 자세한 배경이나 특정 이슈의 원인이 궁금하면 아래 작업 로그에서 해당 Step 문서 참고

---

## 📄 인수인계 문서 (핵심 — 먼저 읽을 것)

| 문서 | 설명 |
|---|---|
| [인수인계_압축본.md](./handOver/인수인계_압축본.md) | 전체 프로젝트를 한눈에 파악하기 위한 요약본. 아키텍처, 접근 정보, 현재 상태, 다음 담당자 할 일, 자주 겪는 이슈를 압축해서 정리. **가장 먼저 읽어야 할 문서.** |
| [인수인계_상세본.md](./handOver/인수인계_상세본.md) | 압축본의 상세 버전. 용어 사전, 처음 접속 시 세팅 체크리스트, 시스템 아키텍처, 인프라 접근 정보, 설계 결정과 그 이유, 트러블슈팅 노하우, 코드/파일 맵, 자연어 쿼리 파서 섹션까지 포함된 완전판. |
| [인수인계_자격증명관리.md](./handOver/인수인계_자격증명관리.md) | 비밀번호/API 키 등 자격증명 파일의 위치와 재발급 절차 정리 (실제 값은 미포함). Elasticsearch, AWS/Kafka, vLLM 서버, GitHub 인증까지 포함. vLLM은 임시 개인 서버 의존 구조라 별도로 신규 환경 구축 가이드 포함. |

---

## 📚 자세한 작업 로그 (Confluence)

시행착오, 원인 분석, 코드 스니펫까지 담긴 상세 기록. 특정 기능이 왜 이렇게 만들어졌는지,
특정 에러를 어떻게 해결했는지 궁금할 때 참고.

| 문서 | 설명 |
|---|---|
| [Step 1: 로컬 검색 엔진 환경 구축](https://stradvision.atlassian.net/wiki/spaces/Internship/pages/50060820647) | Docker 기반 Elasticsearch + Kibana 환경을 처음부터 구축한 기록. sudo 권한, Docker Compose 설치, 커널 설정, VSCode Remote-SSH 연결 등 초기 서버 세팅 시행착오 전반. |
| [Step 2: 초기 데이터 연동 (Polling 방식)](https://stradvision.atlassian.net/wiki/spaces/Internship/pages/50066882564) | ODD/Motional 실 데이터 스키마 확인부터, 폴링 기반 파이프라인 구축, 1fps 다운샘플링 정책 확정, Motional 지연 도착 자동 재매칭, Bulk API 전환, 전수 감사 스크립트 구축까지의 전체 과정. |
| [Step 3: 인덱싱 및 검색 기능 검증](https://stradvision.atlassian.net/wiki/spaces/Internship/pages/50064687484) | 인덱스 매핑 설계, 테스트 케이스 기반 검색 정확도 검증(AAA1/LSD 전체 필드), CSV export 기능 검증, UI 레벨까지 4개 층위 전수 검증 기록. |
| [Step 4: 데이터 파이프라인 고도화](https://stradvision.atlassian.net/wiki/spaces/Internship/pages/50077007889) | 폴링 → Kafka(MSK) 전환 과정. IAM/OAUTHBEARER 인증, Consumer Group 구조, Motional/ODD 발행-소비 파이프라인 구축, systemd 상시 서비스 등록까지. |
| [Step 5: 시각화 및 포트폴리오 래핑 (UI/서버화)](https://stradvision.atlassian.net/wiki/spaces/Internship/pages/50063475212) | 검색 UI(index.html)와 검수 UI(review.html) 구축 전 과정. 보안 강화(비밀번호 분리, 경로 우회 차단), 자동 갱신, 검수 UX 개편(기본 Accept 방식) 등. |
| [Query Parsing 자연어 → 구조화 쿼리 변환 개발 기록](https://stradvision.atlassian.net/wiki/x/oAFLqgs) | LLM(vLLM) 기반 자연어 검색 파서 개발 전 과정(v1~v18). 임베딩 vs LLM 비교, 프롬프트 개선 방법론, hallucination 방어, held-out 테스트, 코드단 결정론적 보장 전환까지. |

---

## 🗒️ 관련 미팅 로그

프로젝트 스코프와 요구사항이 어떻게 결정되었는지 확인하려면 참고.

| 문서 |
|---|
| [ODD Selection Meeting Log #1](https://stradvision.atlassian.net/wiki/spaces/Internship/pages/50035688087) |
| [ODD Selection Meeting Log #2](https://stradvision.atlassian.net/wiki/spaces/Internship/pages/50052015406) |
| [ODD Selection Meeting Log #3](https://stradvision.atlassian.net/wiki/spaces/Internship/pages/50060307819) |
| [ODD Selection Meeting Log #4](https://stradvision.atlassian.net/wiki/spaces/Internship/pages/50102075393) |

---

## 💻 코드 저장소

[GitHub - Hayoung04/SV_ODD_Selection_New](https://github.com/Hayoung04/SV_ODD_Selection_New.git)

> ⚠️ 현재 개인 계정 소유 상태 — 회사 조직 계정으로 이전 또는 관리자 collaborator 추가 필요
> (자격증명관리 문서 참고)

---

## 🎤 최종 발표 자료

[hayoung_intern_final_presentation.pdf](./handOver/hayoung_intern_final_presentation.pdf)

> 이 자료는 8/13에 발표한 자료로 업데이트되지 않은 내용이 있을 수 있습니다. 
---

## 🛠 배포/의존성 파일

새 서버 세팅이나 서비스 재등록이 필요할 때 참고 (평소엔 이미 strad35에 다 구성되어 있어 불필요).

- `kafka/requirements.txt`, `scripts/requirements.txt`, `ui/requirements.txt` — 환경별 Python 패키지 목록
- `deploy/` — systemd 서비스 파일 원본 + 사용법 (`deploy/README.md` 참고)

---

## 프로젝트 한눈에 보기

```
Qumulo(원본) → Kafka(dev.shared.odd-tagging.input)
  → kafka_consumer.py(상시구동) → unified_pipeline.py 로직 재사용
    (10fps→1fps 다운샘플링, Motional ±0.5초 매칭, Bulk 색인)
  → Elasticsearch(odd-frames-v2, 976건)
  → 검색 UI(index.html: 체크박스 필터 + AI 자연어 검색) / 검수 UI(review.html) ← server.py
                                                              ↑
                                              vLLM(AI 자연어 검색 전용, 인수인계_상세본.md 9번 섹션 참고)
```

**완료된 것**
- ES 파이프라인 (976건 전수 감사 100% 일치)
- Kafka 연동 (IAM 인증, 대량 1198건 처리 검증)
- 검색/검수 UI (보안 조치 포함)
- AI 자연어 검색 프로토타입 (v1~v18, held-out 정확도 약 75%)

**다음 담당자 할 일 우선순위**는 [인수인계_압축본.md](./handOver/인수인계_압축본.md)의 "다음 담당자 할 일" 섹션 참고.
