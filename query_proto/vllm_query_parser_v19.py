# -*- coding: utf-8 -*-
"""
19단계 프로토타입: vLLM(Qwen) 쿼리 파서 v19
v18 대비 변경점 - 시스템 프롬프트 토큰 압축 (8,278 -> 5,498 토큰):
  새로 전환한 vLLM 서버(Qwen3-14B-FP8)의 max_model_len이 8,192 토큰인데, v18 시스템
  프롬프트 자체가 이미 8,278 토큰이라 유저 질문을 넣을 자리조차 없었음. 섹션별 토큰
  실측(전체 8,278 = motional_taxonomy 1,829 + aaa1_taxonomy 1,074 + sibling_groups 649 +
  rules/examples 4,725) 결과 rules/examples가 가장 컸고, 그 다음이 motional_taxonomy 순.

대응 (의미는 유지하고 문체/중복만 압축, 규칙 번호와 순서는 그대로 유지):
  - motional_taxonomy: FIELD_DESCRIPTIONS 원본은 그대로 두고, build_motional_taxonomy()
    안에서만 "Ego 차량이" 같은 반복 주어를 헤더 한 줄로 통합하고 어미를 명사형으로
    압축하는 _compress_motional_desc() 추가. (1,829 -> 1,393 토큰)
  - aaa1_taxonomy: 마찬가지로 field_descriptions.py 원본은 보존, build_aaa1_taxonomy()
    안에서 반복 패턴("역광(낮은 태양) 상황에서의 X", "X로 이루어진 도로 경계" 등)이
    있는 일부 항목만 짧은 override 문구로 교체하는 _compress_aaa1_desc() 추가. 매칭에
    쓰이는 context(time/da_road_type/weather) 설명문은 유의어 나열이 매칭 정확도에
    직결되므로 그대로 유지. (1,074 -> 978 토큰)
  - sibling_groups: 두 NOTE 문단과 그룹 타이틀 문구를 축약. (649 -> 476 토큰)
  - Rules 1~19: 중복 규칙(12번이 7번과 완전히 동일한 문장이었음 - "Do not infer extra
    conditions that are merely plausible but not stated or implied") 발견, 12번을
    삭제하고 7번으로 통합. 나머지 규칙은 번호/순서 유지한 채 문장만 압축 (예시로 이미
    전달되는 내용은 규칙 본문에서 축약).
  - Few-shot 예시: 개수보다 길이 축소를 우선했고(각 예시의 Reasoning 설명을 핵심만
    남기고 압축), 아래 4개만 "동일한 규칙을 다른 예시가 이미 충분히 커버"하는 경우로
    판단해 제거함 (형제그룹은 전부 최소 1개 예시가 남아있는지 확인 완료):
      1) "motorcycle headlamp visible in a tunnel" (영어) - "터널 안에서 오토바이
         헤드램프가 보이는 상황"(한국어) 예시와 완전히 동일한 규칙 조합(LSD auto:true +
         unmapped 도로유형)을 언어만 바꿔 반복하고 있었음. 한국어만 유지.
      2) "가로등 적은 야간 도로에서 오토바이 헤드램프 하나만 보이는 상황" - lsd_tag vs
         da_road_type 카테고리 구분은 규칙 9-1 본문에 이미 명시되어 있고, 다른 예시들이
         이미 unmapped_terms 관련 패턴을 여러 번 보여주고 있어 제거.
      3) "자전거를 뒤따라가는 상황" - behind_* 형제그룹은 "느리게 가는 트럭을 뒤에서
         따라가는 상황" 예시(behind_long_vehicle)로 여전히 최소 1개 커버됨.
      4) "인도에서 손수레 끌고 가는 사람" / "야간에 소가 ego 차량 앞으로 지나가는 시나리오"
         - "부분 매칭 + unmapped_terms 사유 설명" 패턴은 "터널", "비 오는 날 실내
         주차장", "비가 오지 않는 흐린 날" 예시로 이미 충분히 반복 학습됨.
    나머지 예시는 그대로 유지 (특히 "야간에 가드레일" vs "가드레일에" auto-flag 대조쌍,
    "맑은 날" vs "흐린 날" 부정표현 대조쌍은 회귀 방지 핵심이라 손대지 않음).

주의: v18 파일은 그대로 두고 이 파일로 새로 저장 (버전 관리 원칙 유지). server.py의
import는 이번엔 변경하지 않음 - 회귀 테스트 통과 확인 후 별도로 전환 예정.

사용법:
    export VLLM_BASE_URL="http://127.0.0.1:8001/v1"
    export VLLM_MODEL_NAME="Qwen/Qwen3-14B-FP8"
    export VLLM_API_KEY="dummy"
    python3 vllm_query_parser_v19.py "야간 우천 시 보행자 횡단 장면"
"""

import re
import sys
import os
import json

from field_descriptions import FIELD_DESCRIPTIONS

try:
    from openai import OpenAI
except ImportError:
    print("먼저 설치하세요: pip install openai --break-system-packages")
    sys.exit(1)


AAA1_TARGET_NAMES = {
    "contaminated_lane", "invisible_lanes_city_3_5_lanes_many_vehicles_ahead", "no_lane_mark_road",
    "two_three_wheelers_carrying_loads", "vehicles_carrying_loads_or_protruding_cargo", "bullock_carts",
    "hand_pulled_carts", "animal_sitting_or_standing_cow", "animal_large_dog", "barricades",
    "crowded_pedestrian", "yellow_line_near_curb", "yellow_line_low_sun", "white_line_low_sun",
    "road_boundary_low_sun", "road_boundary_guardrail_or_wall", "road_boundary_lane_separator",
    "road_boundary_flat", "road_boundary_parked_car",
}
LSD_TARGET_NAMES = {
    "low_streetlight_road", "oncoming_vehicle_far_60m", "oncoming_vehicle_taillight_cluster",
    "motorbike_headlamp", "reflector_guardrail", "reflector_lane_divider",
    "reflector_tunnel_lowmid_light", "reflector_white_all", "sign_strong_light_reflection",
    "sign_white_heavy_reflection", "camera_torn_frame",
}

# build_motional_taxonomy()에서만 쓰는 문체 압축 - FIELD_DESCRIPTIONS 원본은 건드리지 않음
# (held-out 테스트 등 다른 곳에서 원본 설명문을 참조할 수 있으므로).
_MOTIONAL_COMPRESS_REPLACEMENTS = [
    ("상태이거나 이에 준하는 상태를 유지한다", "상태 유지"),
    ("내에 위치한다", "내"),
    ("내에 위치한", "내"),
    ("내부 또는 그에 인접한 장벽 및 경계 구조물 부근에 위치한다", "내부/인접 장벽·경계 구조물 부근"),
    ("내부 또는 그 직전 인접 구역에 위치한다", "내부/인접 구역에 위치"),
    ("신호등에 의해 제어되는 교차로에 진입해 있거나 그 내부에 위치한다", "신호등 제어 교차로 진입/내부 위치"),
    ("인근에 위치한다", "인근에 위치"),
    ("부근에 위치한다", "부근에 위치"),
    ("위치한다", "위치"),
    ("수행 중이다", "수행 중"),
    ("통과하는 중이다", "통과 중"),
    ("개시하여 주행 방향을 반전시킨다", "개시(방향 반전)"),
    ("개시한다", "개시"),
    ("고속 주행 차량 또는 상대 속도가 높은 타 차량의 인근에 위치", "고속 차량/상대속도 높은 차량 인근"),
    ("해당 시나리오의 예상 속도 범위 대비 현저히 높은 속도로 주행한다", "예상 속도 대비 현저히 높은 속도로 주행"),
    ("정상 또는 중간 수준의 속도 범위 내에서 주행한다", "정상/중간 속도로 주행"),
    ("주행 중이나 완전한 정지 상태는 아니다", "주행 중, 완전 정지 아님"),
    ("주행 중이다", "주행 중"),
    ("주행한다", "주행"),
    ("급격한 가속 또는 감속을 겪으며, 이는 유의미한 저크(Jerk) 변화를 수반한다", "급격한 가속/감속(유의미한 Jerk 변화 수반)"),
    ("겪는다", "겪음"),
    ("겪으며,", "겪고,"),
    ("가속한다", "가속"),
    ("감속 및 정지한다", "감속·정지"),
    ("정지한다", "정지"),
    ("중이거나 횡단을 시도할 때", "중/시도 시"),
    ("대기한다", "대기"),
    ("존재한다", "존재"),
    ("아니다", "아님"),
    ("Ego 차량의 예상 주행 경로를 횡단한다", "예상 경로 횡단"),
    ("Ego 차량의 진행 방향을 횡단한다", "진행 방향 횡단"),
    ("Ego 차량과 보행자가 동일한 횡단보도 부근 또는 그 위에 공존한다", "보행자와 동일 횡단보도 부근/위에 공존"),
    (" 또는 ", "/"),
]


def _compress_motional_desc(desc: str) -> str:
    d = re.sub(r"^Ego 차량[이은]\s*", "", desc)
    for old, new in _MOTIONAL_COMPRESS_REPLACEMENTS:
        d = d.replace(old, new)
    return d.strip()


def build_motional_taxonomy() -> str:
    lines = ['(아래 각 항목은 모두 "Ego 차량이" 주어로 시작하는 것을 전제로 함, 이하 생략)']
    for key, desc in FIELD_DESCRIPTIONS.items():
        if ":" in key or key in AAA1_TARGET_NAMES or key in LSD_TARGET_NAMES:
            continue
        lines.append(f"- {key} : {_compress_motional_desc(desc)}")
    return "\n".join(lines)


def build_sibling_groups() -> str:
    """
    entity(자전거/오토바이/대형차 등)만 다르고 나머지 의미가 같은 '형제 필드' 그룹을
    명시적으로 보여줌. few-shot 예시 하나만으로 다른 entity에 과일반화되는 문제를
    구조적으로 완화하기 위함.
    """
    groups = [
        (
            "'Ego가 X 후방에서 따라간다' (behind_*)",
            {
                "bike/bicycle": "behind_bike",
                "motorcycle": "behind_motorcycle",
                "long vehicle (truck/bus)": "behind_long_vehicle",
                "pedestrian": "behind_pedestrian_on_driveable",
            },
        ),
        (
            "'X가 ego 경로를 횡단한다' (crossed_by_*)",
            {
                "bike/bicycle": "crossed_by_bike",
                "motorcycle": "crossed_by_motorcycle",
                "vehicle": "crossed_by_vehicle",
            },
        ),
        (
            "'X 여러 대/명이 근처에 있다' (near_multiple_*)",
            {
                "bike/bicycle": "near_multiple_bikes",
                "motorcycle": "near_multiple_motorcycle",
                "vehicle": "near_multiple_vehicles",
                "pedestrian": "near_multiple_pedestrians",
            },
        ),
        (
            "'선행 차량 유무/속도' (lead-vehicle variants)",
            {
                "있음, 일반": "following_lane_with_lead",
                "있음, 느림": "following_lane_with_slow_lead",
                "없음": "following_lane_without_lead",
            },
        ),
        (
            "'차선 변경' 방향 (changing_lane_*)",
            {
                "방향 불특정": "changing_lane",
                "좌측": "changing_lane_to_left",
                "우측": "changing_lane_to_right",
            },
        ),
        (
            "'회전 시작' (starting_*_turn) — 방향/속도는 독립 축, 둘 다 있으면 AND",
            {
                "방향: 좌회전": "starting_left_turn",
                "방향: 우회전": "starting_right_turn",
                "속도: 고속": "starting_high_speed_turn",
                "속도: 저속": "starting_low_speed_turn",
            },
        ),
    ]

    lines = ["IMPORTANT — sibling field groups (same meaning template, entity/condition differs):"]
    for title, mapping in groups:
        lines.append(f"\n{title}")
        for entity, field in mapping.items():
            lines.append(f"  - {entity} -> {field}")
    lines.append(
        "\nNOTE (starting_*_turn only): direction and speed are INDEPENDENT axes, not mutually "
        "exclusive siblings. If both are stated, output BOTH fields with AND (e.g. \"고속으로 좌회전\" "
        "-> starting_left_turn AND starting_high_speed_turn) — never pick only one."
    )
    lines.append(
        "\nFor all other groups: pick the sibling matching the exact entity/condition named in the "
        "sentence — never substitute a different sibling from the same group (e.g. motorcycle must "
        "map to the motorcycle sibling, not bike, even if only bike appeared in an earlier example)."
    )
    return "\n".join(lines)


# build_aaa1_taxonomy()에서만 쓰는 문체 압축 override - field_descriptions.py 원본은
# 건드리지 않음 (매칭 정확도용 원본 설명문 보존).
_AAA1_DESC_OVERRIDES = {
    "contaminated_lane": "진흙/기름/잔해/웅덩이 등으로 오염된 차선(인식 어려움)",
    "barricades": "공사장/통제구역 바리케이드",
    "yellow_line_low_sun": "노란색 차선 (역광/낮은 태양)",
    "white_line_low_sun": "흰색 차선 (역광/낮은 태양)",
    "road_boundary_low_sun": "도로 경계 (역광/낮은 태양)",
    "road_boundary_guardrail_or_wall": "도로 경계 - 가드레일/벽",
    "road_boundary_lane_separator": "도로 경계 - 차선 분리대",
    "road_boundary_flat": "도로 경계 - 평평한 갓길(턱/장벽 없음)",
    "invisible_lanes_city_3_5_lanes_many_vehicles_ahead": "도시 3~5차선, 앞차 많아 차선 안 보임",
    "two_three_wheelers_carrying_loads": "짐 실은 이륜/삼륜차",
    "vehicles_carrying_loads_or_protruding_cargo": "짐/화물 돌출 차량",
    "animal_sitting_or_standing_cow": "앉거나 서있는 소",
    "crowded_pedestrian": "보행자 밀집 장면",
    "yellow_line_near_curb": "연석 근처 노란색 차선",
    "road_boundary_parked_car": "주차 차량들이 도로 경계 형성",
    "low_streetlight_road": "가로등 적은 어두운 야간 도로",
    "oncoming_vehicle_far_60m": "약 60m+ 밖 마주오는 차량 헤드램프",
    "oncoming_vehicle_taillight_cluster": "마주오는 헤드램프+앞차 테일램프 겹침",
    "reflector_tunnel_lowmid_light": "터널 내부 중하단 조명/반사광",
    "reflector_white_all": "흰색/밝은 반사판·표지판 반사광",
    "sign_strong_light_reflection": "표지판에 강하게 반사되는 전조등",
    "sign_white_heavy_reflection": "흰색 표지판 강한 반사광",
    "camera_torn_frame": "카메라 센서 오류로 찢긴/깨진 프레임(이미지 손상)",
}


def _compress_aaa1_desc(key: str, desc: str) -> str:
    return _AAA1_DESC_OVERRIDES.get(key, desc)


def build_aaa1_taxonomy() -> str:
    lines = []
    lines.append("[AAA1 target tags] (value: present|absent|unknown)")
    for key, desc in FIELD_DESCRIPTIONS.items():
        if key in AAA1_TARGET_NAMES:
            lines.append(f"- {key} : {_compress_aaa1_desc(key, desc)}")

    lines.append("")
    lines.append("[lsd target tags] (also field \"aaa1_tag\", value: present|absent|unknown)")
    for key, desc in FIELD_DESCRIPTIONS.items():
        if key in LSD_TARGET_NAMES:
            lines.append(f"- {key} : {_compress_aaa1_desc(key, desc)}")

    # context(time/da_road_type/weather) 설명문은 매칭 정확도에 직결되는 유의어
    # 나열이므로 압축하지 않고 원본 그대로 사용.
    context_options = {"time": [], "da_road_type": [], "weather": []}
    for key, desc in FIELD_DESCRIPTIONS.items():
        if ":" in key:
            field_name, value = key.split(":", 1)
            context_options[field_name].append(f"    - {value} : {desc}")

    lines.append("")
    lines.append("[context fields] (field \"time\"/\"da_road_type\"/\"weather\")")
    for field_name in ["time", "da_road_type", "weather"]:
        lines.append(f"- {field_name} allowed values:")
        lines.extend(context_options[field_name])

    return "\n".join(lines)


SYSTEM_PROMPT_TEMPLATE = """You are a constrained natural-language query parser for autonomous-driving data search.

Translate the user's request into a nested Boolean query using ONLY the supported fields and values below.

Atomic conditions:
- Motional Scenario: {{"field":"motional_scenario","value":"<supported scenario>"}}
- AAA1/LSD target tag: {{"field":"aaa1_tag","key":"<supported tag>","value":"present|absent|unknown"}}
- Time: {{"field":"time","value":"day|dawn_evening|night"}}
- Road type: {{"field":"da_road_type","value":"<supported road type>"}}
- Weather: {{"field":"weather","value":"clear|rainy|heavy_rainy|snow|fog"}}

Boolean groups (may be nested to preserve intended logic):
- AND group: {{"all":[<condition-or-group>, ...]}}
- OR group: {{"any":[<condition-or-group>, ...]}}

Rules:
1. Never invent field/scenario/tag/value names — every "value"/"key" you output must appear verbatim
   in the taxonomy below; if unsure, treat it as unmatched. Never add a context value (time/weather/
   da_road_type) the sentence doesn't state just because it "sounds" plausible. Watch sibling-group
   pattern-invention: crossed_by_bike/vehicle/motorcycle existing does NOT license inventing
   crossed_by_pedestrian — if the exact name isn't in the taxonomy, it doesn't exist.
2. Preserve Boolean grouping, not just individual AND/OR words.
3. Korean cues: 그리고/면서/하고 usually = AND; 또는/이나/나/거나 usually = OR — read scope from the
   whole sentence, not just the connector.
4. For AAA1/LSD feature mentions (e.g. "보행자가 많은"), use the matching tag with value `present`.
5. Never output `absent`/`unknown` unless the user explicitly requests it.
6. Match an entry only if its meaning FULLY covers the condition, including embedded sub-conditions
   (e.g. "with a lead vehicle"). Keyword overlap alone isn't enough: if the entity differs or a
   sub-condition is missing/contradicted, put it in `unmapped_terms` instead of the closest-sounding tag.
7. Do not infer extra conditions that are merely plausible but not stated or implied.
8. Preserve specificity: when multiple entries could apply, choose the MOST SPECIFIC match, never a
   generic sibling (e.g. a named vehicle type beats generic "following_lane_with_lead"). Scan the whole
   group before settling on generic.
9. When nothing matches, unmapped_terms must state only that no matching field exists — never invent a
   classification rule not written in the taxonomy itself.
9-1. Before claiming "no value covers X", double-check the taxonomy list — never say a value/field is
   absent when it's present (including checking the right category, e.g. lsd_tag vs da_road_type).
10. Partial matches are normal. Never discard matched conditions because part is unmatched: build
    `query` from what DID match, list the rest in `unmapped_terms`. `query: null` only when nothing maps.
11. "field" must match where the name lives: Motional Scenario names -> `"field":"motional_scenario"`;
    "[AAA1 target tags]"/"[lsd target tags]" names -> `"field":"aaa1_tag","key":"<name>"`.
13. The input may be Korean or English.
14. Return JSON only, exactly with keys: query, unmapped_terms.
15. `query` may be one atomic condition, one Boolean group, or null if nothing is searchable.
16. A negative phrase (Korean "~않는"/"~아닌", English "not"/"without") that merely reinforces an
    already-matched condition in the SAME field is NOT a separate unmapped term (e.g. "맑은 날" already
    matches weather=clear; "비가 오지 않는" adds nothing new). But if it demands a DIFFERENT value with
    no taxonomy entry (e.g. "비가 오지 않는 흐린 날" implies "cloudy"), that IS a genuine unmatched term.
18. A condition shared across ALL branches of an OR must be repeated inside EVERY branch of the
    resulting "any" group — never omitted just because it's shared rather than literally repeated.
19. Add {{"field":"time","value":"night","auto":true}} ONLY when night is added purely via the
    LSD-implies-night rule (no night/야간/밤 wording in the sentence). If night is stated explicitly,
    omit "auto" — even if an LSD tag also matched (coincidence, not a trigger). Rule of thumb: lsd_tag
    present + no explicit night wording => added time=night MUST carry auto:true.

Supported Motional Scenario taxonomy:
{motional_taxonomy}

Supported AAA1 / LSD / context taxonomy:
{aaa1_taxonomy}

{sibling_groups}

Examples:
User: "왼쪽으로 차선 변경하는 장면"
Output: {{"query":{{"field":"motional_scenario","value":"changing_lane_to_left"}},"unmapped_terms":[]}}

User: "밤에 비 오는 시내에서 차선 변경하는 장면"
Output: {{"query":{{"all":[{{"field":"time","value":"night"}},{{"field":"weather","value":"rainy"}},{{"field":"da_road_type","value":"public_road_city"}},{{"field":"motional_scenario","value":"changing_lane"}}]}},"unmapped_terms":[]}}

User: "왼쪽이나 오른쪽으로 차선 변경하는 장면"
Output: {{"query":{{"any":[{{"field":"motional_scenario","value":"changing_lane_to_left"}},{{"field":"motional_scenario","value":"changing_lane_to_right"}}]}},"unmapped_terms":[]}}

User: "고속으로 좌회전을 시작하는 상황"
Output: {{"query":{{"all":[{{"field":"motional_scenario","value":"starting_left_turn"}},{{"field":"motional_scenario","value":"starting_high_speed_turn"}}]}},"unmapped_terms":[]}}

User: "밤이나 비 오는 상황에서 시내에 보행자가 많고 차선 변경하거나 우회전 또는 좌회전하는 장면"
Output: {{"query":{{"all":[{{"any":[{{"field":"time","value":"night"}},{{"field":"weather","value":"rainy"}}]}},{{"field":"da_road_type","value":"public_road_city"}},{{"field":"aaa1_tag","key":"crowded_pedestrian","value":"present"}},{{"any":[{{"field":"motional_scenario","value":"changing_lane"}},{{"field":"motional_scenario","value":"starting_right_turn"}},{{"field":"motional_scenario","value":"starting_left_turn"}}]}}]}},"unmapped_terms":[]}}

User: "눈길을 달리는 트럭"
Reasoning: "눈길" implies snow; weather:snow IS in the allowed list, so this must NOT be marked unmapped.
Output: {{"query":{{"field":"weather","value":"snow"}},"unmapped_terms":[]}}

User: "안개 낀 국도이거나 눈 오는 고속도로에서 대형차를 뒤따라가는 상황"
Reasoning: behind_long_vehicle is shared by both OR branches — repeat it in EACH branch, don't drop it.
Output: {{"query":{{"any":[{{"all":[{{"field":"weather","value":"fog"}},{{"field":"da_road_type","value":"public_road_rural"}},{{"field":"motional_scenario","value":"behind_long_vehicle"}}]}},{{"all":[{{"field":"weather","value":"snow"}},{{"field":"da_road_type","value":"public_road_highway"}},{{"field":"motional_scenario","value":"behind_long_vehicle"}}]}}]}},"unmapped_terms":[]}}

User: "멈춰있으면서 앞에 보행자나 자전거가 지나가는 장면"
Output: {{"query":{{"all":[{{"field":"motional_scenario","value":"stationary"}},{{"field":"motional_scenario","value":"crossed_by_bike"}}]}},"unmapped_terms":["보행자가 지나가는"]}}

User: "위험한 장면"
Output: {{"query":null,"unmapped_terms":["위험한"]}}

User: "비 오는 날 실내 주차장에서 급가속하는 상황"
Output: {{"query":{{"all":[{{"field":"weather","value":"rainy"}},{{"field":"da_road_type","value":"parking_lot_indoor"}}]}},"unmapped_terms":["실내 주차장 급가속 - accelerating_at_traffic_light/crosswalk 전제조건(신호등/횡단보도)과 모순"]}}

User: "터널 안에서 오토바이 헤드램프가 보이는 상황"
Reasoning: motorbike_headlamp is an LSD tag, so time=night is auto-added though "야간"/night is never
said — mark "auto":true (pure inference, not a direct statement).
Output: {{"query":{{"all":[{{"field":"time","value":"night","auto":true}},{{"field":"aaa1_tag","key":"motorbike_headlamp","value":"present"}}]}},"unmapped_terms":["터널 도로 유형 - da_road_type 목록에 없음"]}}

User: "야간에 가드레일에 반사되는 불빛이 보이는 도로"
Reasoning: "야간" is stated explicitly, so this is a DIRECT match, not an LSD-implies-night inference —
do NOT add "auto":true even though reflector_guardrail (an LSD tag) also matched.
Output: {{"query":{{"all":[{{"field":"time","value":"night"}},{{"field":"aaa1_tag","key":"reflector_guardrail","value":"present"}}]}},"unmapped_terms":[]}}

User: "가드레일에 반사되는 불빛이 보이는 도로"
Reasoning: no night wording; reflector_guardrail is an lsd_tag, so time=night is added and MUST carry
auto:true (purely inferred).
Output: {{"query":{{"all":[{{"field":"time","value":"night","auto":true}},{{"field":"aaa1_tag","key":"reflector_guardrail","value":"present"}}]}},"unmapped_terms":[]}}

User: "비가 오지 않는 맑은 날 고속도로"
Reasoning: "맑은 날"->weather=clear; "비가 오지 않는" is redundant with clear — don't also list it in
unmapped_terms.
Output: {{"query":{{"all":[{{"field":"weather","value":"clear"}},{{"field":"da_road_type","value":"public_road_highway"}}]}},"unmapped_terms":[]}}

User: "비가 오지 않는 흐린 날 고속도로"
Reasoning: unlike the "맑은" case, "흐린"(cloudy) has no matching weather value — genuinely unmatched.
Output: {{"query":{{"field":"da_road_type","value":"public_road_highway"}},"unmapped_terms":["흐린(cloudy) 날씨 - weather 허용값(clear/rainy/heavy_rainy/snow/fog)에 없음"]}}

User: "느리게 가는 트럭을 뒤에서 따라가는 상황"
Reasoning: slow lead + truck are both specific (following_lane_with_slow_lead + behind_long_vehicle) —
AND both, don't pick just one or fall back to a generic field.
Output: {{"query":{{"all":[{{"field":"motional_scenario","value":"following_lane_with_slow_lead"}},{{"field":"motional_scenario","value":"behind_long_vehicle"}}]}},"unmapped_terms":[]}}

User: "pedestrian crossing at night in the rain"
Reasoning: no field literally named "crossed_by_pedestrian" exists (only crossed_by_bike/vehicle/
motorcycle) — don't invent one by sibling-group pattern-matching. The real match is
"waiting_for_pedestrian_to_cross".
Output: {{"query":{{"all":[{{"field":"time","value":"night"}},{{"field":"weather","value":"rainy"}},{{"field":"motional_scenario","value":"waiting_for_pedestrian_to_cross"}}]}},"unmapped_terms":[]}}

User: "starting a left turn at high speed"
Reasoning: direction and speed are independent axes in starting_*_turn — both stated, so AND both.
Output: {{"query":{{"all":[{{"field":"motional_scenario","value":"starting_left_turn"}},{{"field":"motional_scenario","value":"starting_high_speed_turn"}}]}},"unmapped_terms":[]}}
"""


def _collect_valid_names():
    """taxonomy에 실제로 존재하는 이름들을 집합으로 정리 (사후 검증용)."""
    motional_names = set()
    aaa1_lsd_names = set()
    for key in FIELD_DESCRIPTIONS:
        if ":" in key:
            continue
        elif key in AAA1_TARGET_NAMES or key in LSD_TARGET_NAMES:
            aaa1_lsd_names.add(key)
        else:
            motional_names.add(key)

    context_values = {"time": set(), "da_road_type": set(), "weather": set()}
    for key in FIELD_DESCRIPTIONS:
        if ":" in key:
            field_name, value = key.split(":", 1)
            if field_name in context_values:
                context_values[field_name].add(value)

    return motional_names, aaa1_lsd_names, context_values


_MOTIONAL_NAMES, _AAA1_LSD_NAMES, _CONTEXT_VALUES = _collect_valid_names()


def validate_and_sanitize(result: dict) -> dict:
    """
    모델 출력을 taxonomy 기준으로 사후 검증.
    - 존재하지 않는 field/key/value는 제거하고 unmapped_terms로 이동 (hallucination 방어)
    - field/group이 잘못 분류된 경우 실제 이름 기준으로 바로잡음 (group 오분류 방어)
    - 제거로 인해 all/any 그룹이 비면 상위에서도 같이 제거 (트리 정리)

    주의: 이건 "필드가 실제로 존재하는지"만 검증함. 의미(semantic)가 맞는지는 검증 못 함
    (예: 신호등 조건 무시하고 붙이는 것 같은 문제는 여기서 못 잡음 - 그건 프롬프트 레벨 문제).
    """
    removed_notes = []

    def is_valid_condition(cond: dict) -> bool:
        field = cond.get("field")
        if field == "motional_scenario":
            return cond.get("value") in _MOTIONAL_NAMES
        elif field == "aaa1_tag":
            return cond.get("key") in _AAA1_LSD_NAMES and cond.get("value") in {"present", "absent", "unknown"}
        elif field in ("time", "da_road_type", "weather"):
            return cond.get("value") in _CONTEXT_VALUES.get(field, set())
        return False

    def fix_group_if_misclassified(cond: dict) -> dict:
        """field가 잘못 분류됐어도(예: motional 이름인데 aaa1_tag로 옴) 실제 존재하는 이름이면 바로잡음.
        주의: "auto" 같은 부가 메타데이터 키는 field/key/value만으로 검증하는 이 파이프라인에서
        건드릴 이유가 없음 - 아래 두 misclassified 분기만 새 dict를 만들고(원래 misclassification
        자체가 드문 edge case이며 time=night에는 해당 안 됨), 정상 케이스(마지막 return cond)는
        원본 dict를 그대로 반환하므로 auto 키가 자동으로 보존됨. 별도 코드 수정 불필요."""
        field = cond.get("field")
        if field == "aaa1_tag" and cond.get("key") in _MOTIONAL_NAMES:
            return {"field": "motional_scenario", "value": cond["key"]}
        if field == "motional_scenario" and cond.get("value") in _AAA1_LSD_NAMES:
            return {"field": "aaa1_tag", "key": cond["value"], "value": cond.get("value_hint", "present")}
        return cond

    def walk(node):
        """None 리턴 = 이 노드는 완전히 제거됨."""
        if node is None:
            return None
        if "all" in node or "any" in node:
            key = "all" if "all" in node else "any"
            children = [walk(child) for child in node[key]]
            children = [c for c in children if c is not None]
            if not children:
                return None
            if len(children) == 1:
                return children[0]
            return {key: children}
        else:
            fixed = fix_group_if_misclassified(node)
            if is_valid_condition(fixed):
                return fixed
            else:
                # 상세 디버그 정보(원본 조건 JSON)는 서버 로그에만 남기고,
                # 사용자에게 보내는 unmapped_terms에는 사용자 친화적 문구만 넣는다.
                print(
                    f"[검증 실패로 제거됨] {json.dumps(node, ensure_ascii=False)} - taxonomy에 존재하지 않는 field/key/value"
                )
                removed_notes.append("AI가 제안한 조건 중 하나가 태깅 리스트에 없어 제외되었습니다")
                return None

    sanitized_query = walk(result.get("query"))
    unmapped = list(result.get("unmapped_terms", []))
    unmapped.extend(removed_notes)

    return {"query": sanitized_query, "unmapped_terms": unmapped}


def enforce_lsd_implies_night(result: dict) -> dict:
    """
    LSD 태그(aaa1_tag의 key가 LSD_TARGET_NAMES에 속함)는 정의상 야간 전용 데이터이므로,
    쿼리 트리 안에서 LSD 태그를 포함하는 모든 AND 레벨에 time=night이 결정론적으로
    포함되도록 보장한다. 프롬프트로 모델에게 계속 맡기지 않고 여기서 확정 처리
    (validate_and_sanitize() 이후, parse_query() 리턴 직전에 호출).

    - 해당 레벨에 이미 time 조건이 있으면(값 무관) 건드리지 않음 - night이면 그대로 두고,
      night이 아닌 값(day/dawn_evening)이면 모순이므로 경고만 로그로 남기고 건드리지 않음.
    - time 조건이 아예 없으면 {"field":"time","value":"night","auto":true}를 새로 추가.
    - any(OR) 그룹은 분기마다 독립적으로 재귀 처리하므로, LSD 태그가 있는 분기에만
      night이 붙고 다른 분기는 영향받지 않는다.
    """

    def contains_lsd_tag(node):
        if node is None:
            return False
        if "all" in node:
            return any(contains_lsd_tag(c) for c in node["all"])
        if "any" in node:
            return any(contains_lsd_tag(c) for c in node["any"])
        return node.get("field") == "aaa1_tag" and node.get("key") in LSD_TARGET_NAMES

    def existing_time_value(node):
        if node is None:
            return None
        if "all" in node:
            for c in node["all"]:
                value = existing_time_value(c)
                if value is not None:
                    return value
            return None
        if "any" in node:
            for c in node["any"]:
                value = existing_time_value(c)
                if value is not None:
                    return value
            return None
        if node.get("field") == "time":
            return node.get("value")
        return None

    def walk(node):
        if node is None:
            return None
        if "any" in node:
            node["any"] = [walk(c) for c in node["any"]]
            return node
        if "all" in node:
            # Only recurse into nested OR groups (each branch needs its own AND-level
            # treatment). Atomic/nested-AND children stay as-is here — this "all" node
            # itself is the AND-level that gets a single night condition appended below,
            # so an atomic LSD child must NOT be individually wrapped into its own
            # {"all": [...]} (that would double-nest and duplicate the night condition).
            node["all"] = [walk(c) if c is not None and "any" in c else c for c in node["all"]]
            if contains_lsd_tag(node):
                existing = existing_time_value(node)
                if existing is None:
                    node["all"].append({"field": "time", "value": "night", "auto": True})
                elif existing != "night":
                    print(
                        f"[경고] LSD 태그와 모순되는 time={existing} 조건이 있어 night 강제 "
                        f"추가를 건너뜀: {json.dumps(node, ensure_ascii=False)}"
                    )
            return node
        # atomic condition 하나뿐인 경우
        if node.get("field") == "aaa1_tag" and node.get("key") in LSD_TARGET_NAMES:
            return {"all": [node, {"field": "time", "value": "night", "auto": True}]}
        return node

    result["query"] = walk(result.get("query"))
    return result


def get_client() -> OpenAI:
    base_url = os.environ.get("VLLM_BASE_URL")
    api_key = os.environ.get("VLLM_API_KEY", "dummy")
    if not base_url:
        print("환경변수 VLLM_BASE_URL이 설정 안 됨. 예: http://127.0.0.1:8001/v1")
        sys.exit(1)
    return OpenAI(base_url=base_url, api_key=api_key)


def parse_query(user_query: str) -> dict:
    client = get_client()
    model_name = os.environ.get("VLLM_MODEL_NAME")
    if not model_name:
        print("환경변수 VLLM_MODEL_NAME이 설정 안 됨. 실제 서버에 로드된 모델명 필요.")
        sys.exit(1)

    system_prompt = SYSTEM_PROMPT_TEMPLATE.format(
        motional_taxonomy=build_motional_taxonomy(),
        aaa1_taxonomy=build_aaa1_taxonomy(),
        sibling_groups=build_sibling_groups(),
    )

    response = client.chat.completions.create(
        model=model_name,
        max_tokens=1500,
        temperature=0,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_query},
        ],
        # Qwen3 계열은 기본적으로 <think>...</think> 리즈닝 블록을 앞에 출력함 (JSON 파싱을
        # 깨뜨림). vLLM chat_template_kwargs로 리즈닝 모드를 끔 - 프롬프트 압축과는 무관한
        # 별개 이슈지만, 새 모델(Qwen3-14B-FP8)로 바뀌며 처음 드러난 문제라 여기서 같이 수정.
        extra_body={"chat_template_kwargs": {"enable_thinking": False}},
    )

    text = response.choices[0].message.content.strip()

    # 서버가 enable_thinking 힌트를 무시하는 경우를 대비한 방어적 처리 - <think> 블록이
    # 남아있으면 그 뒤 내용만 취한다.
    if "<think>" in text:
        text = text.split("</think>")[-1].strip()

    if text.startswith("```"):
        text = text.strip("`")
        if text.startswith("json"):
            text = text[4:]
        text = text.strip()

    try:
        raw_result = json.loads(text)
    except json.JSONDecodeError:
        print("[WARN] JSON 파싱 실패. 원본 응답:")
        print(text)
        raise

    sanitized = validate_and_sanitize(raw_result)
    sanitized = enforce_lsd_implies_night(sanitized)
    return sanitized


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print('사용법: python3 vllm_query_parser_v19.py "검색하고 싶은 문장"')
        sys.exit(1)

    query = sys.argv[1]
    result = parse_query(query)
    print(json.dumps(result, ensure_ascii=False, indent=2))
