# 데이터 소스 경로 교체 가이드

> 작성자: 김하영(인턴) · 원본: [인수인계_상세본.md](../handOver/인수인계_상세본.md) "5-3" 항목 (2026-08-21 분리)

실제 Qumulo 자동 적재 경로 확정 시, 현재 테스트 경로(`machet18_data`)를 대체할지 폐기할지 결정 필요.

## 실제 Qumulo 자동 적재 경로 확정 시 교체 방법

1. 새 경로 확인 (인프라/데이터팀에 실제 자동 적재 경로 문의)
2. 아래 4개 파일의 해당 변수명을 새 경로로 교체 (현재 값은 전부
   `/home/odd-selection/machet18_data` 하드코딩):
   - `scripts/unified_pipeline.py` → `DATA_ROOT`
   - `scripts/audit_pipeline.py` → `DATA_ROOT`
   - `kafka/odd_producer.py` → `DATA_ROOT`
   - `kafka/motional_producer.py` → `MOTIONAL_DIR` (Motional 파일 전용 경로이므로 새
     경로의 `motional` 하위 폴더까지 포함해서 지정)
3. `sudo systemctl restart odd-kafka-consumer`
4. 표준 3단계 검증 (건수확인 → `audit_pipeline.py` → UI 샘플 확인)
5. 기존 테스트 데이터(`machet18_data`)를 유지할지 삭제할지는 팀 상의 후 결정 (교체
   직후 곧바로 삭제하지 말 것 — 문제 발생 시 비교 기준으로 필요할 수 있음)
6. 인덱스(`odd-frames-v2`)와 Consumer Group(`odd-selection-consumer`)은 그대로 유지
   (이건 데이터 원본 경로만 바뀌는 것이지, 새 환경을 구축하는 것이 아님 — 완전히
   새로운 서버/파이프라인을 만드는 경우는
   [new_environment_setup.md](./new_environment_setup.md) 참고)
