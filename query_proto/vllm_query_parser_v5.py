# -*- coding: utf-8 -*-
"""
6단계 프로토타입: vLLM(Qwen) 쿼리 파서 v5
v4 대비 변경점:
  - "부분 조건 불일치" 처리 규칙 추가: 필드 의미에 명시된 하위조건(신호등 유무, 선행차량 유무 등) 중
    문장에서 확인 안 되거나 모순되는 게 있으면, 키워드가 겹치더라도 그 필드는 매칭하지 않고 unmatched로.
  - 이 케이스를 보여주는 부정 예시 추가 (실내주차장 급가속 - 신호등 언급 없음 -> accelerating_at_traffic_light 매칭 금지)

사용법은 이전과 동일:
    export VLLM_BASE_URL="http://127.0.0.1:8001/v1"
    export VLLM_MODEL_NAME="Qwen/Qwen3-VL-8B-Instruct"
    export VLLM_API_KEY="dummy"
    python3 vllm_query_parser_v5.py "야간에 소가 ego 차량 앞으로 지나가는 시나리오"
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


def build_schema_with_meaning() -> str:
    """필드명 + 뜻풀이를 함께 제공 (field_descriptions.py 재사용)."""
    groups = {
        "aaa1_tags": [],
        "aaa1_context": [],
        "lsd_tags": [],
        "motional_tags": [],
    }

    aaa1_target_names = {
        "contaminated_lane", "invisible_lanes_city_3_5_lanes_many_vehicles_ahead", "no_lane_mark_road",
        "two_three_wheelers_carrying_loads", "vehicles_carrying_loads_or_protruding_cargo", "bullock_carts",
        "hand_pulled_carts", "animal_sitting_or_standing_cow", "animal_large_dog", "barricades",
        "crowded_pedestrian", "yellow_line_near_curb", "yellow_line_low_sun", "white_line_low_sun",
        "road_boundary_low_sun", "road_boundary_guardrail_or_wall", "road_boundary_lane_separator",
        "road_boundary_flat", "road_boundary_parked_car",
    }
    lsd_target_names = {
        "low_streetlight_road", "oncoming_vehicle_far_60m", "oncoming_vehicle_taillight_cluster",
        "motorbike_headlamp", "reflector_guardrail", "reflector_lane_divider",
        "reflector_tunnel_lowmid_light", "reflector_white_all", "sign_strong_light_reflection",
        "sign_white_heavy_reflection", "camera_torn_frame",
    }

    for key, desc in FIELD_DESCRIPTIONS.items():
        if ":" in key:  # aaa1_context (e.g. "time:night")
            groups["aaa1_context"].append(f"- {key} : {desc}")
        elif key in aaa1_target_names:
            groups["aaa1_tags"].append(f"- {key} : {desc}")
        elif key in lsd_target_names:
            groups["lsd_tags"].append(f"- {key} : {desc}")
        else:
            groups["motional_tags"].append(f"- {key} : {desc}")

    lines = []
    lines.append("### aaa1_tags (value: present|absent)")
    lines.extend(groups["aaa1_tags"])
    lines.append("")
    lines.append("### aaa1_context (value must be one of the field's own allowed options)")
    lines.extend(groups["aaa1_context"])
    lines.append("")
    lines.append("### lsd_tags (value: present|absent)")
    lines.extend(groups["lsd_tags"])
    lines.append("")
    lines.append("### motional_tags (value: present)")
    lines.extend(groups["motional_tags"])
    return "\n".join(lines)


SYSTEM_PROMPT_TEMPLATE = """You are a query-to-filter parser for an autonomous-driving frame search engine.
You are given a list of available fields WITH their exact meaning, and a user's natural-language
search request in Korean or English.

Your job: extract ALL conditions from the user's request. For each condition, either map it to the
single best-matching field (only if the field's MEANING genuinely covers that condition), or mark it
as unmatched if no field's meaning is a good fit. Then combine matched conditions into a filter using
AND / OR logic.

Most single search sentences describe MULTIPLE conditions that must ALL hold at once (AND) — e.g. an
object, an action, and a context (time/weather/road type) together. Use OR only when the user's wording
explicitly offers alternatives ("or", "either...or", "이거나", "혹은", "또는").

Available fields (name : meaning):
{schema}

--- EXAMPLES ---

Example 1 (multi-condition, all matched)
User: "고속도로에서 대형 트럭을 뒤따라가는 상황"
Output:
{{
  "logic": "AND",
  "conditions": [
    {{"group": "aaa1_context", "field": "da_road_type", "value": "public_road_highway"}},
    {{"group": "motional_tags", "field": "behind_long_vehicle", "value": "present"}},
    {{"group": "motional_tags", "field": "following_lane_with_lead", "value": "present"}}
  ],
  "unmatched": []
}}

Example 2 (multi-condition, all matched)
User: "자전거 여러 대 근처에서 급하게 브레이크 밟는 상황"
Output:
{{
  "logic": "AND",
  "conditions": [
    {{"group": "motional_tags", "field": "near_multiple_bikes", "value": "present"}},
    {{"group": "motional_tags", "field": "high_magnitude_jerk", "value": "present"}}
  ],
  "unmatched": []
}}

Example 3 (partial match — one condition has NO good field, do not force it)
User: "야간에 소가 ego 차량 앞으로 지나가는 시나리오"
Reasoning: "night" matches aaa1_context time=night. "소" (cow) alone matches
animal_sitting_or_standing_cow by meaning ("sitting or standing cow") but that meaning is about a
STATIC cow, not one CROSSING in front of the vehicle. The specific combination "animal crossing in
front of ego" has no field whose meaning covers a moving/crossing animal. Do not attach it to an
unrelated field (e.g. a pedestrian or vehicle crossing field) just because the action "crossing" is
similar — the entity (animal) does not match those fields' meaning.
Output:
{{
  "logic": "AND",
  "conditions": [
    {{"group": "aaa1_context", "field": "time", "value": "night"}}
  ],
  "unmatched": ["소(동물)가 ego 차량 앞을 가로질러 지나가는 동작 - 해당하는 동적 동물 필드 없음, animal_sitting_or_standing_cow는 정적 상태만 의미함"]
}}

Example 4 (OR case)
User: "비가 오거나 눈이 오는 날"
Output:
{{
  "logic": "OR",
  "conditions": [
    {{"group": "aaa1_context", "field": "weather", "value": "rainy"}},
    {{"group": "aaa1_context", "field": "weather", "value": "snow"}}
  ],
  "unmatched": []
}}

Example 5 (partial sub-condition mismatch — do NOT match despite keyword overlap)
User: "비 오는 날 실내 주차장에서 급가속하는 상황"
Reasoning: weather=rainy and da_road_type=parking_lot_indoor match cleanly. For "급가속" (hard
acceleration), the only fields whose meaning includes acceleration are
"accelerating_at_traffic_light*" and "accelerating_at_crosswalk", but their meaning REQUIRES a
specific sub-condition (being at/near a traffic light, or at/near a crosswalk) that this sentence
does not mention and that is actually contradicted by the stated context (an indoor parking lot has
no traffic light or crosswalk). Sharing the word "accelerate" is not enough — the field's full
meaning must hold. So "급가속" itself has no matching field and goes to unmatched.
Output:
{{
  "logic": "AND",
  "conditions": [
    {{"group": "aaa1_context", "field": "weather", "value": "rainy"}},
    {{"group": "aaa1_context", "field": "da_road_type", "value": "parking_lot_indoor"}}
  ],
  "unmatched": ["실내 주차장에서의 급가속 - 해당 조건에 맞는 필드 없음 (accelerating_at_traffic_light/crosswalk는 신호등/횡단보도 조건이 전제되어 문맥과 모순됨)"]
}}

--- END EXAMPLES ---

Rules:
- Identify EVERY condition in the sentence (entities, actions, counts like "multiple/several", and
  context such as time/weather/road type). Sentences commonly contain 2-3 conditions.
- Match a condition to a field ONLY if the field's stated meaning genuinely covers that condition.
  Superficial keyword overlap (e.g. both mention "crossing" or "accelerating") is NOT enough if the
  entity differs (e.g. animal vs pedestrian vs vehicle), OR if the field's meaning requires a specific
  sub-condition (e.g. "at a traffic light", "with a lead vehicle", "at a crosswalk") that the sentence
  does not state or that contradicts the sentence's stated context. When in doubt whether a required
  sub-condition is satisfied, prefer unmatched over a forced match.
- If a condition has no genuinely matching field, put a short Korean description of that condition
  into "unmatched" instead of forcing it onto an unrelated field. This is important — an incorrect
  forced match is worse than an honest unmatched entry.
- Default logic is "AND" unless the user's wording explicitly signals alternatives (OR).
- "value" for present|absent fields (aaa1_tags, lsd_tags, motional_tags) must be "present" or "absent".
- "value" for aaa1_context fields must be one of that field's own allowed values (day/dawn_evening/night
  for time; public_road_highway/public_road_city/public_road_rural/parking_lot_outdoor/parking_lot_indoor
  for da_road_type; clear/rainy/heavy_rainy/snow/fog for weather).
- If nothing in the sentence maps to any field, return {{"logic": "AND", "conditions": [], "unmatched": [...]}}.
- Return ONLY a JSON object in the exact shape shown in the examples — no markdown, no explanation
  outside the JSON, no extra keys.
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

    system_prompt = SYSTEM_PROMPT_TEMPLATE.format(schema=build_schema_with_meaning())

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
        print('사용법: python3 vllm_query_parser_v5.py "검색하고 싶은 문장"')
        sys.exit(1)

    query = sys.argv[1]
    result = parse_query(query)
    print(json.dumps(result, ensure_ascii=False, indent=2))