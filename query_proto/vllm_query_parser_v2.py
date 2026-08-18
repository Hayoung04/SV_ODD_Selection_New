# -*- coding: utf-8 -*-
"""
3단계 프로토타입: vLLM(Qwen)으로 쿼리 파싱

vLLM은 OpenAI 호환 API(/v1/chat/completions)로 뜨기 때문에
openai 파이썬 SDK를 그대로 쓰되, base_url만 vLLM 서버로 돌리면 됨.
API 비용 없음 (내부 GPU 서버 사용).

사용법:
    pip install openai --break-system-packages
    export VLLM_BASE_URL="http://127.0.0.1:8001/v1"  
    export VLLM_MODEL_NAME="Qwen3-VL-8B-Instruct"         
    export VLLM_API_KEY="dummy"  
    
    python3 vllm_query_parser.py "야간 우천 시 보행자 횡단 장면"

주의:
- vLLM은 인증 없이 뜨는 경우가 많음. openai SDK는 api_key가 빈 문자열이면 에러날 수 있어서
  인증이 없어도 임의의 문자열("dummy" 등)을 넣어줘야 함 (아래 코드에 반영됨).
- 네트워크 접근 가능 여부(같은 사내망/VPN)는 시은 서버가 살아있을 때 별도 확인 필요.
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

Your job: decide which fields from the list are RELEVANT to what the user is looking for, and what
value each relevant field should be set to, so the result can be used as search filter criteria.

Step-by-step approach (do this reasoning internally, do not include it in the output):
1. Break the user's request into ALL separate conditions it contains — e.g. objects/entities
   (truck, bicycle, cow), actions/events (turning, braking, following, crossing), counts
   ("multiple", "several"), and context (time, weather, road type). A single sentence can and
   often does contain more than one condition — do not stop after finding the first match.
2. For EACH condition identified in step 1, search the full field list for the best matching field.
   Do not skip a condition just because you already matched one field for the sentence.
3. Only after checking every condition, assemble the final JSON with all matched fields included.

Rules:
- Only include fields that are clearly relevant to the user's request. Do NOT include irrelevant fields.
- Do NOT force a match: if a condition from step 1 has no reasonably close field, leave it unmapped
  rather than attaching it to a loosely related field.
- For present|absent|unknown fields: use "present" when the user wants that condition to appear in the
  scene, "absent" when the user explicitly wants it excluded. Never guess "unknown".
- For context fields (time, da_road_type, weather): use one of the listed allowed values only if the
  user's request implies that context; otherwise omit the field entirely.
- For motional_tags: only "present" is meaningful (these are event/scenario labels); omit if not relevant.
- If the user's request cannot be mapped to any field, return empty objects for all three groups.
- Return ONLY a JSON object, no markdown, no explanation, no extra keys.

Available fields:
{schema}

Output JSON shape (omit any field/key that isn't relevant — do not output null or "unknown" placeholders):
{{
  "aaa1_tags": {{"<field_name>": "present|absent"}},
  "aaa1_context": {{"<field_name>": "<one_of_allowed_values>"}},
  "lsd_tags": {{"<field_name>": "present|absent"}},
  "motional_tags": {{"<field_name>": "present"}}
}}
"""


def get_client() -> OpenAI:
    base_url = os.environ.get("VLLM_BASE_URL")
    api_key = os.environ.get("VLLM_API_KEY", "dummy")
    if not base_url:
        print("환경변수 VLLM_BASE_URL이 설정 안 됨. 예: http://<ip>:8000/v1")
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
        temperature=0,  # 파싱 태스크라 결정론적으로
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_query},
        ],
    )

    text = response.choices[0].message.content.strip()

    # 모델이 ```json 코드펜스를 붙이는 경우 제거
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
        print('사용법: python3 vllm_query_parser.py "검색하고 싶은 문장"')
        sys.exit(1)

    query = sys.argv[1]
    result = parse_query(query)
    print(json.dumps(result, ensure_ascii=False, indent=2))