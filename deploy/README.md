# 배포 파일 사용법

## systemd 서비스 파일 (`deploy/`)

서버가 바뀌거나 서비스를 재등록해야 할 때, 아래처럼 그대로 복사해서 쓰면 됩니다.
(현재 strad35에서는 이미 `/etc/systemd/system/`에 등록되어 있어 이 작업이 필요 없습니다 —
이 폴더는 "서버를 새로 세팅해야 하는 상황"을 대비한 백업 및 참고용입니다.)

```bash
sudo cp deploy/odd-kafka-consumer.service /etc/systemd/system/
sudo cp deploy/odd-search-ui.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now odd-kafka-consumer
sudo systemctl enable --now odd-search-ui
```

- `odd-polling.service.DISABLED`는 참고용으로만 보존한 것입니다. 확장자를 보고 실수로
  활성화하지 않도록 `.DISABLED`를 붙여뒀습니다. 실제로 필요할 일은 없습니다(9-2 섹션,
  인수인계 상세본 참고).
- `User=hayoung.kim` 부분은 계정이 바뀌면 그에 맞게 수정해야 합니다.
- 경로(`WorkingDirectory`, `ExecStart`)도 서버 위치가 바뀌면 같이 수정해야 합니다.

## Python 패키지 설치 (`requirements.txt`)

이 프로젝트는 목적이 다른 3개의 Python 환경으로 나뉘어 있습니다:

| 환경 | 위치 | 용도 | 설치 방법 |
|---|---|---|---|
| kafka | `kafka/venv/` (가상환경) | Kafka Consumer/Producer | `cd kafka && source venv/bin/activate && pip install -r requirements.txt` |
| scripts | 시스템 기본 Python | Elasticsearch 파이프라인/감사 | `pip install -r scripts/requirements.txt` |
| ui | 시스템 기본 Python | 검색/검수 서버 + AI 자연어 검색 | `pip install -r ui/requirements.txt` |

**주의**: `kafka/venv`가 활성화된 상태에서 `scripts/`나 `ui/`의 스크립트를 실행하면
`ModuleNotFoundError`가 날 수 있습니다 (반대도 마찬가지). 터미널 프롬프트에 `(venv)`
표시가 있는지 확인하고, 시스템 기본 환경으로 돌아가려면 `deactivate`를 실행하세요.