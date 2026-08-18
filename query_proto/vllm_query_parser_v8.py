# -*- coding: utf-8 -*-
"""
8단계 프로토타입: vLLM(Qwen) 쿼리 파서 v8
v7 대비 변경점:
  - Rule 8(구체성 유지)이 changing_lane 계열 외에는 잘 안 지켜지는 문제 발견
    (behind_long_vehicle, following_lane_with_slow_lead, behind_bike 등 more specific
    scenario를 놔두고 자꾸 following_lane_with_lead로 뭉뚱그림)
    -> 규칙을 일반화하고 following/behind 계열 예시 추가
  - unmapped_terms 사유에 스키마에 없는 근거를 지어내는(confabulation) 문제 발견
    -> "존재하지 않는 분류 규칙을 지어내지 마라" 규칙 추가

사용법:
    export VLLM_BASE_URL="http://127.0.0.1:8001/v1"
    export VLLM_MODEL_NAME="Qwen/Qwen3-VL-8B-Instruct"
    export VLLM_API_KEY="dummy"
    python3 vllm_query_parser_v8.py "대형 트럭을 뒤따라가는 상황"
"""

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


def build_motional_taxonomy() -> str:
    lines = []
    for key, desc in FIELD_DESCRIPTIONS.items():
        if ":" in key or key in AAA1_TARGET_NAMES or key in LSD_TARGET_NAMES:
            continue
        lines.append(f"- {key} : {desc}")
    return "\n".join(lines)


def build_aaa1_taxonomy() -> str:
    lines = []
    lines.append("[AAA1 target tags] (value: present|absent|unknown)")
    for key, desc in FIELD_DESCRIPTIONS.items():
        if key in AAA1_TARGET_NAMES:
            lines.append(f"- {key} : {desc}")

    lines.append("")
    lines.append("[lsd target tags] (also used with field \"aaa1_tag\", value: present|absent|unknown)")
    for key, desc in FIELD_DESCRIPTIONS.items():
        if key in LSD_TARGET_NAMES:
            lines.append(f"- {key} : {desc}")

    context_options = {"time": [], "da_road_type": [], "weather": []}
    for key, desc in FIELD_DESCRIPTIONS.items():
        if ":" in key:
            field_name, value = key.split(":", 1)
            context_options[field_name].append(f"    - {value} : {desc}")

    lines.append("")
    lines.append("[context fields] (used with field \"time\" / \"da_road_type\" / \"weather\")")
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

Boolean groups:
- AND group: {{"all":[<condition-or-group>, ...]}}
- OR group: {{"any":[<condition-or-group>, ...]}}
- Groups may be nested to preserve the user's intended logic.

Rules:
1. Never invent field names, scenario names, AAA1/LSD tag names, or values. Every "value" and "key"
   you output must appear verbatim in the taxonomy below.
2. Preserve Boolean grouping, not just individual AND/OR words.
3. Korean cues: 그리고/면서/하고 are commonly AND; 또는/이나/나/거나 are commonly OR, but interpret
   scope from the whole sentence, not just the connector word.
4. For AAA1/LSD feature requests such as "보행자가 많은", use the corresponding tag with value `present`.
5. Do not output `absent` merely because the user did not mention something. Only use absent/unknown
   when explicitly requested by the user.
6. Match a condition to a taxonomy entry ONLY if its stated meaning genuinely and fully covers that
   condition — including any sub-condition embedded in the meaning (e.g. "at a traffic light", "with
   a lead vehicle", "at a crosswalk"). Superficial keyword overlap is not enough: if the entity differs
   (e.g. animal vs pedestrian vs vehicle) or a required sub-condition is missing/contradicted by the
   sentence, do NOT substitute the closest-sounding tag — put a concise phrase in `unmapped_terms` instead.
7. Do not infer extra conditions that are merely plausible but not stated or implied.
8. Preserve specificity in EVERY taxonomy group, not just lane changes: whenever multiple entries could
   apply, always choose the MOST SPECIFIC one that fully matches the sentence, never a more generic
   sibling. This applies broadly — e.g. among following/behind-style scenarios, an entry naming a
   specific vehicle type (long vehicle, bike, motorcycle) or a specific lead-vehicle condition (slow
   lead) must be preferred over a generic one (e.g. "following_lane_with_lead") whenever the sentence's
   wording matches that specific entry. Before finalizing a match, scan the ENTIRE taxonomy group for
   a more specific entry that also fits — do not stop at the first plausible generic match.
9. When something has no matching taxonomy entry, the unmapped_terms explanation must state only that
   no matching field exists (optionally naming the closest field and why its meaning doesn't fit).
   Never invent a classification rule, category system, or justification that is not written in the
   taxonomy itself (e.g. do not claim "bicycles are classified as vehicles, not bikes" unless the
   taxonomy actually says so) — an honest "no match" is required, a fabricated reason is not.
10. Do not infer extra conditions that are merely plausible but not stated or implied.
11. The input may be Korean or English.
12. Return JSON only, exactly with keys: query, unmapped_terms.
13. `query` may be one atomic condition, one Boolean group, or null if nothing is searchable.

Supported Motional Scenario taxonomy:
{motional_taxonomy}

Supported AAA1 / LSD / context taxonomy:
{aaa1_taxonomy}

Examples:
User: "왼쪽으로 차선 변경하는 장면"
Output: {{"query":{{"field":"motional_scenario","value":"changing_lane_to_left"}},"unmapped_terms":[]}}

User: "밤에 비 오는 시내에서 차선 변경하는 장면"
Output: {{"query":{{"all":[{{"field":"time","value":"night"}},{{"field":"weather","value":"rainy"}},{{"field":"da_road_type","value":"public_road_city"}},{{"field":"motional_scenario","value":"changing_lane"}}]}},"unmapped_terms":[]}}

User: "왼쪽이나 오른쪽으로 차선 변경하는 장면"
Output: {{"query":{{"any":[{{"field":"motional_scenario","value":"changing_lane_to_left"}},{{"field":"motional_scenario","value":"changing_lane_to_right"}}]}},"unmapped_terms":[]}}

User: "밤이나 비 오는 상황에서 시내에 보행자가 많고 차선 변경하거나 우회전 또는 좌회전하는 장면"
Output: {{"query":{{"all":[{{"any":[{{"field":"time","value":"night"}},{{"field":"weather","value":"rainy"}}]}},{{"field":"da_road_type","value":"public_road_city"}},{{"field":"aaa1_tag","key":"crowded_pedestrian","value":"present"}},{{"any":[{{"field":"motional_scenario","value":"changing_lane"}},{{"field":"motional_scenario","value":"starting_right_turn"}},{{"field":"motional_scenario","value":"starting_left_turn"}}]}}]}},"unmapped_terms":[]}}

User: "멈춰있으면서 앞에 보행자나 자전거가 지나가는 장면"
Output: {{"query":{{"all":[{{"field":"motional_scenario","value":"stationary"}},{{"field":"motional_scenario","value":"crossed_by_bike"}}]}},"unmapped_terms":["보행자가 지나가는"]}}

User: "위험한 장면"
Output: {{"query":null,"unmapped_terms":["위험한"]}}

User: "야간에 소가 ego 차량 앞으로 지나가는 시나리오"
Output: {{"query":{{"field":"time","value":"night"}},"unmapped_terms":["소(동물)가 ego 차량 앞을 가로질러 지나가는 동작 - animal_sitting_or_standing_cow는 정적 상태만 의미하여 매칭 불가"]}}

User: "비 오는 날 실내 주차장에서 급가속하는 상황"
Output: {{"query":{{"all":[{{"field":"weather","value":"rainy"}},{{"field":"da_road_type","value":"parking_lot_indoor"}}]}},"unmapped_terms":["실내 주차장에서의 급가속 - accelerating_at_traffic_light/crosswalk는 신호등·횡단보도 전제조건이 문맥과 모순됨"]}}

User: "터널 안에서 오토바이 헤드램프가 보이는 상황"
Output: {{"query":{{"field":"aaa1_tag","key":"motorbike_headlamp","value":"present"}},"unmapped_terms":["터널이라는 도로 유형(맥락) - da_road_type 허용값 목록에 터널 옵션 없음"]}}

User: "대형 트럭을 뒤따라가는 상황"
Reasoning: "following_lane_with_lead" is generic (any lead vehicle), but "behind_long_vehicle" is a
more specific entry whose meaning explicitly covers trucks/buses. Prefer the specific one.
Output: {{"query":{{"field":"motional_scenario","value":"behind_long_vehicle"}},"unmapped_terms":[]}}

User: "느리게 가는 트럭을 뒤에서 따라가는 상황"
Reasoning: Two specific aspects are both present: the lead vehicle is slow (-> following_lane_with_slow_lead)
and it's a truck/long vehicle (-> behind_long_vehicle). Neither generic "following_lane_with_lead" nor
picking only one specific entry fully captures the sentence, so express both as an AND.
Output: {{"query":{{"all":[{{"field":"motional_scenario","value":"following_lane_with_slow_lead"}},{{"field":"motional_scenario","value":"behind_long_vehicle"}}]}},"unmapped_terms":[]}}

User: "자전거를 뒤따라가는 상황"
Reasoning: "behind_bike" specifically covers following a bike/cyclist. Prefer it over the generic
"following_lane_with_lead".
Output: {{"query":{{"field":"motional_scenario","value":"behind_bike"}},"unmapped_terms":[]}}
"""


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
        print("환경변수 VLLM_MODEL_NAME이 설정 안 됨. 시은이 띄운 실제 모델명 필요.")
        sys.exit(1)

    system_prompt = SYSTEM_PROMPT_TEMPLATE.format(
        motional_taxonomy=build_motional_taxonomy(),
        aaa1_taxonomy=build_aaa1_taxonomy(),
    )

    response = client.chat.completions.create(
        model=model_name,
        max_tokens=1500,
        temperature=0,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_query},
        ],
    )

    text = response.choices[0].message.content.strip()

    if text.startswith("```"):
        text = text.strip("`")
        if text.startswith("json"):
            text = text[4:]
        text = text.strip()

    try:
        return json.loads(text)
    except json.JSONDecodeError:
        print("[WARN] JSON 파싱 실패. 원본 응답:")
        print(text)
        raise


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print('사용법: python3 vllm_query_parser_v8.py "검색하고 싶은 문장"')
        sys.exit(1)

    query = sys.argv[1]
    result = parse_query(query)
    print(json.dumps(result, ensure_ascii=False, indent=2))