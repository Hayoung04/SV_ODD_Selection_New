# -*- coding: utf-8 -*-
"""
11단계 프로토타입: vLLM(Qwen) 쿼리 파서 v11
v10 대비 변경점 - 회귀테스트에서 발견된 신규 버그 대응:
  버그4) 속도(고속/저속)와 방향(좌/우회전)이 한 문장에 같이 언급되면 방향 필드만 고르고
         속도 필드(starting_high_speed_turn/starting_low_speed_turn)를 버리는 경향.
         "회전 시작" taxonomy에서 방향 축과 속도 축이 서로 독립된 별도 필드라는 걸
         모델이 인지하지 못해서 발생 (sibling_groups에 명시 안 되어 있었음).

대응:
  - 프롬프트: build_sibling_groups()에 "회전 시작" 그룹(방향 축 + 속도 축, 둘 다 언급되면
    AND) 추가, few-shot 예시 추가.

v10 변경점 (참고, 그대로 유지):
  버그1) 존재하지 않는 필드명을 지어냄 (예: "crossed_by_pedestrian" - taxonomy에 없음)
  버그2) 일부 조건이 unmapped라고 해서 이미 매칭된 조건까지 통째로 query:null 처리
  버그3) motional_scenario 필드를 aaa1_tag로 잘못 분류
  - 코드: validate_and_sanitize() 함수로 모델 출력을 taxonomy 기준 사후 검증.
    존재하지 않는 field/value/group은 자동으로 걸러내고 unmapped_terms로 이동시킴.
    프롬프트만으로 100% 막기 어려운 hallucination에 대한 안전망.

사용법:
    export VLLM_BASE_URL="http://127.0.0.1:8001/v1"
    export VLLM_MODEL_NAME="Qwen/Qwen3-VL-8B-Instruct"
    export VLLM_API_KEY="dummy"
    python3 vllm_query_parser_v11.py "야간 우천 시 보행자 횡단 장면"
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


def build_sibling_groups() -> str:
    """
    entity(자전거/오토바이/대형차 등)만 다르고 나머지 의미가 같은 '형제 필드' 그룹을
    명시적으로 보여줌. few-shot 예시 하나만으로 다른 entity에 과일반화되는 문제를
    구조적으로 완화하기 위함.
    """
    groups = [
        (
            "'Ego가 X의 후방에서 따라간다' (behind_*)",
            {
                "bike/bicycle (자전거)": "behind_bike",
                "motorcycle (오토바이/이륜차)": "behind_motorcycle",
                "long vehicle - truck/bus (대형차/트럭/버스)": "behind_long_vehicle",
                "pedestrian (보행자)": "behind_pedestrian_on_driveable",
            },
        ),
        (
            "'X가 ego의 진행경로를 횡단한다' (crossed_by_*)",
            {
                "bike/bicycle (자전거)": "crossed_by_bike",
                "motorcycle (오토바이/이륜차)": "crossed_by_motorcycle",
                "vehicle (일반 차량)": "crossed_by_vehicle",
            },
        ),
        (
            "'X 여러 대/명이 근처에 있다' (near_multiple_*)",
            {
                "bike/bicycle (자전거)": "near_multiple_bikes",
                "motorcycle (오토바이/이륜차)": "near_multiple_motorcycle",
                "vehicle (일반 차량)": "near_multiple_vehicles",
                "pedestrian (보행자)": "near_multiple_pedestrians",
            },
        ),
        (
            "'선행 차량 유무/속도' 변형 (lead-vehicle variants)",
            {
                "선행차량 있음, 일반": "following_lane_with_lead",
                "선행차량 있음, 느림": "following_lane_with_slow_lead",
                "선행차량 없음": "following_lane_without_lead",
            },
        ),
        (
            "'차선 변경' 방향 변형 (changing_lane_*)",
            {
                "방향 불특정": "changing_lane",
                "좌측": "changing_lane_to_left",
                "우측": "changing_lane_to_right",
            },
        ),
        (
            "'회전을 시작한다' (starting_*_turn) — 방향과 속도는 서로 독립적인 축, 둘 다 언급되면 AND로 함께 매칭",
            {
                "방향: 왼쪽/좌회전": "starting_left_turn",
                "방향: 오른쪽/우회전": "starting_right_turn",
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
        "\nNOTE on the 'starting_*_turn' group above: unlike the other groups, direction "
        "(left/right) and speed (high/low) are two INDEPENDENT axes, not mutually-exclusive "
        "siblings. If the sentence states both a direction and a speed, output BOTH matching "
        "fields combined with AND (e.g. \"고속으로 좌회전\" -> starting_left_turn AND "
        "starting_high_speed_turn) — do not pick only one and drop the other."
    )
    lines.append(
        "\nFor all other groups: when the sentence names a specific entity/condition from one of "
        "these groups, you MUST pick the sibling field matching that exact entity/condition — never "
        "substitute a different sibling from the same group (e.g. a motorcycle must map to the "
        "motorcycle sibling, not the bike sibling, "
        "even if only the bike sibling appeared in an earlier example)."
    )
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
   you output must appear verbatim, character-for-character, in the taxonomy below. If you are not
   certain a name appears verbatim in the taxonomy, do NOT output it — treat that condition as unmatched
   instead. This applies to context values too: never add a time/weather/da_road_type value the
   sentence does not state, even if it seems plausible or atmospheric (e.g. do not add weather:clear
   or a road type just because the scene "sounds like" that setting).
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
10. Partial matches are expected and normal — a single sentence commonly has SOME conditions that map
    cleanly and OTHERS that don't. Never discard already-matched conditions just because one part of
    the sentence is unmatched. Build `query` from whatever conditions DID match (using AND/OR as
    appropriate), and list the unmatched part(s) separately in `unmapped_terms`. Only use `query: null`
    when there is truly nothing in the entire sentence that maps to any taxonomy entry — a sentence
    with at least one matched condition must never produce `query: null`.
11. Each atomic condition's "field" value must exactly match where that name actually lives: names
    listed under "Supported Motional Scenario taxonomy" always use `"field":"motional_scenario"`; names
    listed under "[AAA1 target tags]" or "[lsd target tags]" always use `"field":"aaa1_tag","key":"<name>"`.
    Never put a motional scenario name under aaa1_tag or vice versa — check which taxonomy list the
    exact name appears in before writing the field/key.
12. Do not infer extra conditions that are merely plausible but not stated or implied.
13. The input may be Korean or English.
14. Return JSON only, exactly with keys: query, unmapped_terms.
15. `query` may be one atomic condition, one Boolean group, or null if nothing is searchable.

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

User: "인도에서 손수레 끌고 가는 사람"
Reasoning: "hand_pulled_carts" (사람이 손으로 끄는 수레) matches the cart-pulling person cleanly. "인도"
(sidewalk) has no matching da_road_type value, so it is unmatched — but that must NOT cause the whole
query to become null. Keep the matched condition in `query` and list only the unmatched part separately.
Output: {{"query":{{"field":"aaa1_tag","key":"hand_pulled_carts","value":"present"}},"unmapped_terms":["인도(보도) - da_road_type 허용값 목록에 해당 도로 유형 없음"]}}
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
        """field가 잘못 분류됐어도(예: motional 이름인데 aaa1_tag로 옴) 실제 존재하는 이름이면 바로잡음."""
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
                removed_notes.append(
                    f"[검증 실패로 제거됨] {json.dumps(node, ensure_ascii=False)} - taxonomy에 존재하지 않는 field/key/value"
                )
                return None

    sanitized_query = walk(result.get("query"))
    unmapped = list(result.get("unmapped_terms", []))
    unmapped.extend(removed_notes)

    return {"query": sanitized_query, "unmapped_terms": unmapped}


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
    )

    text = response.choices[0].message.content.strip()

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

    return validate_and_sanitize(raw_result)


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print('사용법: python3 vllm_query_parser_v11.py "검색하고 싶은 문장"')
        sys.exit(1)

    query = sys.argv[1]
    result = parse_query(query)
    print(json.dumps(result, ensure_ascii=False, indent=2))