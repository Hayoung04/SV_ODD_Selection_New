# -*- coding: utf-8 -*-
"""
4단계 프로토타입: vLLM(Qwen) 쿼리 파서 v3
- v1/v2 대비 변경점: few-shot 예시 추가, AND/OR 논리를 출력 구조에 명시적으로 반영

사용법은 v1/v2와 동일:
    export VLLM_BASE_URL="http://127.0.0.1:8001/v1"
    export VLLM_MODEL_NAME="Qwen/Qwen3-VL-8B-Instruct"
    export VLLM_API_KEY="dummy"
    python3 vllm_query_parser_v3.py "고속도로에서 대형 트럭을 뒤따라가는 상황"
"""

import sys
import os
import json

from field_schema import build_schema_prompt

try:
    from openai import OpenAI
except ImportError:
    print("먼저 설치하세요: pip install openai --break-system-packages")
    sys.exit(1)


SYSTEM_PROMPT_TEMPLATE = """You are a query-to-filter parser for an autonomous-driving frame search engine.
You are given a list of available fields (ODD static attributes, night light-source attributes,
and Motional scenario/event labels) and a user's natural-language search request in Korean or English.

Your job: extract ALL conditions from the user's request and map each one to the best matching field,
then combine them into a filter expression using AND / OR logic.

Most single search sentences describe MULTIPLE conditions that must ALL hold at once (AND) — for example
an object, an action, and a context (time/weather/road type) together. Use OR only when the user's
wording explicitly offers alternatives (e.g. "or", "either... or", "이거나", "혹은", "또는").

Available fields:
{schema}

--- EXAMPLES ---

Example 1
User: "고속도로에서 대형 트럭을 뒤따라가는 상황"
Reasoning (do not output this): conditions = [road=highway, vehicle=long/large truck ahead, action=following in same lane]
Output:
{{
  "logic": "AND",
  "conditions": [
    {{"group": "aaa1_context", "field": "da_road_type", "value": "public_road_highway"}},
    {{"group": "motional_tags", "field": "behind_long_vehicle", "value": "present"}},
    {{"group": "motional_tags", "field": "following_lane_with_lead", "value": "present"}}
  ]
}}

Example 2
User: "교차로에서 좌회전 시작하는 장면, 신호등 있는 곳"
Reasoning (do not output this): conditions = [action=starting a left turn, location=intersection, extra=traffic-light controlled]
Output:
{{
  "logic": "AND",
  "conditions": [
    {{"group": "motional_tags", "field": "starting_left_turn", "value": "present"}},
    {{"group": "motional_tags", "field": "on_traffic_light_intersection", "value": "present"}}
  ]
}}

Example 3
User: "자전거 여러 대 근처에서 급하게 브레이크 밟는 상황"
Reasoning (do not output this): conditions = [object_count=multiple bicycles nearby, action=hard braking/sudden jerk]
Output:
{{
  "logic": "AND",
  "conditions": [
    {{"group": "motional_tags", "field": "near_multiple_bikes", "value": "present"}},
    {{"group": "motional_tags", "field": "high_magnitude_jerk", "value": "present"}}
  ]
}}

Example 4 (OR case)
User: "비가 오거나 눈이 오는 날"
Reasoning (do not output this): user explicitly gives alternatives with "거나" -> OR
Output:
{{
  "logic": "OR",
  "conditions": [
    {{"group": "aaa1_context", "field": "weather", "value": "rainy"}},
    {{"group": "aaa1_context", "field": "weather", "value": "snow"}}
  ]
}}

--- END EXAMPLES ---

Rules:
- Identify EVERY condition in the sentence (entities, actions, counts like "multiple/several", and
  context such as time/weather/road type). Do not stop after the first match — sentences commonly
  contain 2-3 conditions that must all be captured.
- Default logic is "AND" unless the user's wording explicitly signals alternatives (OR).
- Do NOT force a match: if a condition has no reasonably close field in the list, leave it out rather
  than attaching it to a loosely related field.
- "value" for present|absent fields (aaa1_tags, lsd_tags, motional_tags) must be "present" or "absent"
  (never "unknown"). "value" for aaa1_context fields must be one of that field's listed allowed values.
- If nothing in the sentence maps to any field, return {{"logic": "AND", "conditions": []}}.
- Return ONLY a JSON object in the exact shape shown in the examples — no markdown, no explanation,
  no extra keys, and no reasoning text in the output.
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

    system_prompt = SYSTEM_PROMPT_TEMPLATE.format(schema=build_schema_prompt())

    response = client.chat.completions.create(
        model=model_name,
        max_tokens=1000,
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
        print('사용법: python3 vllm_query_parser_v3.py "검색하고 싶은 문장"')
        sys.exit(1)

    query = sys.argv[1]
    result = parse_query(query)
    print(json.dumps(result, ensure_ascii=False, indent=2))