import base64
import glob
import html
import json
import mimetypes
import os
import re
import time
from pathlib import Path
from urllib.parse import quote

import requests
import streamlit as st
import streamlit.components.v1 as components
from dotenv import load_dotenv

load_dotenv()

# === Gemini 모델 상수 ===
GEMINI_VISION_MODEL = "gemini-2.5-flash"
GEMINI_TEXT_MODEL = "gemini-2.5-flash"

# === 네이버 쇼핑 API ===
NAVER_CLIENT_ID = os.getenv("NAVER_CLIENT_ID")
NAVER_CLIENT_SECRET = os.getenv("NAVER_CLIENT_SECRET")


# ─── [OLD UI] 4단계 플로우로 교체됨 ───
# st.set_page_config(page_title="음식 사진 레시피 생성기", page_icon="🍽️", layout="centered")
# st.title("🍽️ 음식 사진 레시피 생성기")
# st.write("음식 사진을 업로드하고 **레시피 생성하기** 버튼을 눌러보세요.")

SOURCE_MD_PATH = "source.md"
FEEDBACK_MD_PATH = "feedback.md"
CART_JSON_DIR = "cart/json"
COMPLETED_JSON_DIR = "completed/json"


def load_source_md(path: str = SOURCE_MD_PATH) -> str:
    if not os.path.exists(path):
        return ""
    # 재료 섹션이 비어있으면 source 없음으로 처리
    parsed = parse_source_md_to_data(path)
    if not parsed.get("ingredients"):
        return ""
    with open(path, "r", encoding="utf-8") as f:
        return f.read().strip()


def load_feedback_md(path: str = FEEDBACK_MD_PATH) -> str:
    if not os.path.exists(path):
        return ""
    with open(path, "r", encoding="utf-8") as f:
        return f.read().strip()


# ─── [OLD UI] 단일 폼 입력. ui-developer가 4단계 session_state 플로우로 교체 예정 ───
# uploaded_file = st.file_uploader(
#     "음식 사진 업로드",
#     type=["jpg", "jpeg", "png", "webp"],
#     accept_multiple_files=False,
# )
#
# if uploaded_file is not None:
#     st.image(uploaded_file, caption="업로드한 이미지", use_container_width=True)
#
# st.subheader("사용자 설정")
# servings = st.number_input("몇 인분으로 만들까요?", min_value=1, max_value=20, value=1, step=1)
# user_preference_text = st.text_area(
#     "추가 선호사항 (선택)",
#     placeholder="예: 빨간 소스는 달지 않은 케첩 베이스, 사진에 없는 재료는 최대한 제외, 10분 내 조리 희망",
#     help="첨부 파일 외에 매번 텍스트 선호사항을 함께 전달할 수 있습니다.",
# )
#
# source_md_content = load_source_md()
# if source_md_content:
#     st.caption(f"source.md 로드 완료 ({len(source_md_content)}자)")
# else:
#     st.caption("source.md가 없어 기본 설정만 사용합니다.")
#
# feedback_md_content = load_feedback_md()
# if feedback_md_content:
#     st.caption(f"feedback.md 기억 로드 완료 ({len(feedback_md_content)}자)")

# ───────────────────────── 프롬프트 빌더 ─────────────────────────


def build_vision_prompt() -> str:
    """Call 1: 음식 판별 전용 프롬프트.

    사진을 분석해 음식 여부, 음식명, 보이는 재료, 시각적 특징을 추출한다.
    순수 JSON만 반환하도록 강제한다.
    """
    return """
너는 음식 사진을 분석하는 요리 보조 AI다.
주어진 사진을 분석하여 반드시 한국어로 응답하라.

━━━ 응답 규칙 ━━━

- 반드시 한국어로만 응답한다.
- 순수 JSON 문자열만 반환한다. 마크다운 코드블록(```)이나 설명 문장 절대 포함 금지.
- 사진이 음식이 아니면 is_food를 false로 설정하고 non_food_reason에 이유를 작성한다.
  · 이때 dish_name은 빈 문자열, ingredients는 빈 배열, characteristics는 빈 문자열로 반환한다.
- 음식이면 is_food를 true, non_food_reason은 빈 문자열로 둔다.
- ingredients에는 사진에서 시각적으로 확인 가능한 재료만 담는다.
  보이지 않는 양념·소스는 추측해 넣지 않는다.
- characteristics에는 색감·조리 형태·플레이팅 등 시각적 특징을 2~3문장으로 요약한다.

━━━ 정밀 시각 분석 지침 ━━━

[빵·베이커리·페이스트리류 정밀 판별]
- 겉면의 굽기 정도(연한 황금빛/진한 갈색/탄 정도)와 윤기(매트/글레이즈) 주의
- 층 구조가 보이면: 파이 도우·퍼프 페이스트리·크루아상·갈레트 중 구분
- 속 재료의 조리 상태 확인: 날것(생과일색)/졸인(짙고 윤기)/구운(캐러멜화/황변) 구별
- 과일 타르트·파이·갈레트·크럼블·크로스타타: 형태(원형/자유형), 가장자리 접힘, 충전물 색으로 구분
- 빵 겉면의 질감(매끄러운/거친/세몰리나 뿌림/egg wash 광택/슈거파우더)을 명시

[일반 음식 정밀 판별]
- 색감의 층·그러데이션을 명시적으로 설명 (예: "위층은 진한 갈색, 아래는 황금빛")
- 조리 방법을 시각 단서로 추론: 볶음(기름 윤기+볶음 자국), 찜(촉촉한 표면), 튀김(바삭한 외관+기름기), 구이(구운 자국·탄 부분)
- 그릇·플레이팅 스타일에서 국적(한식/일식/양식/중식 등)을 추론 단서로 활용
- 확신이 낮으면 dish_name을 "○○ 추정" 또는 "○○ 또는 ○○" 형식으로 작성하고 characteristics에 불확실 이유를 명시

[characteristics 필드 작성 기준]
- 반드시 색감, 조리 형태, 플레이팅, 추정 조리법을 포함한 3문장 이상 작성
- 빵·베이커리류면 겉면 질감·굽기·속 충전물 상태를 반드시 포함

━━━ JSON 스키마 ━━━

{
  "is_food": true,
  "non_food_reason": "",
  "dish_name": "음식명 (음식 아니면 빈 문자열)",
  "ingredients": ["사진에서 보이는 재료 목록"],
  "characteristics": "음식의 시각적 특징 요약 2~3문장"
}
""".strip()


def build_recipe_prompt(
    vision_result: dict,
    servings,
    extra_requests,
    source_md: str,
    feedback_md: str,
) -> str:
    """Call 2: 레시피 생성 전용 프롬프트.

    Call 1의 vision_result(음식명/재료/특징), 목표 인분, 추가 요청,
    source.md/feedback.md 기억을 조합한다. 순수 JSON만 반환하도록 강제한다.
    """
    vision_result = vision_result or {}
    dish_name = (vision_result.get("dish_name") or "").strip()
    vision_ingredients = vision_result.get("ingredients") or []
    characteristics = (vision_result.get("characteristics") or "").strip()

    # servings 기본값 처리
    if servings is None:
        servings = 2
        servings_note = "2인분 기준 (사용자 미지정 → 기본값)"
    else:
        servings_note = f"{servings}인분 기준"

    source_block = source_md.strip() if source_md and source_md.strip() else "(비어 있음)"
    feedback_block = feedback_md.strip() if feedback_md and feedback_md.strip() else "(비어 있음)"

    vision_ingredients_block = (
        ", ".join(str(i) for i in vision_ingredients) if vision_ingredients else "(없음)"
    )

    # 추가 요청 섹션은 값이 있을 때만 포함한다.
    extra_section = ""
    if extra_requests and str(extra_requests).strip():
        extra_section = (
            "\n[추가 요청사항]\n"
            f"{str(extra_requests).strip()}\n"
            "- source.md와 충돌하면 source.md를 우선한다.\n"
        )

    return f"""
너는 음식 사진 분석 결과를 바탕으로 레시피를 작성하는 요리 보조 AI다.
아래 분석 결과, 사용자 기억 파일, 현재 요청을 반영하여 반드시 한국어로 응답하라.

━━━ 사진 분석 결과 (Call 1) ━━━

- 음식명: {dish_name if dish_name else '(미상)'}
- 보이는 재료: {vision_ingredients_block}
- 시각적 특징: {characteristics if characteristics else '(없음)'}

━━━ 사용자 기억 파일 ━━━

[source.md] 우선순위 높음 — 사용자가 직접 관리하는 취향과 보유 재료 목록
{source_block}

[feedback.md] 자동 학습된 장기 취향 기억 — source.md와 충돌 시 source.md 우선 적용
{feedback_block}

━━━ 현재 요청 ━━━

- 목표 인분: {servings_note}
{extra_section}
━━━ 응답 규칙 ━━━

- 반드시 한국어로만 응답한다.
- 순수 JSON 문자열만 반환한다. 마크다운 코드블록(```)이나 설명 문장 절대 포함 금지.
- 모든 재료 양은 목표 인분({servings}인분) 기준으로 계산한다.
- ingredients는 문자열 배열이며 각 항목은 "재료명(단위)" 형식으로 작성한다. 예: "닭고기(200g)", "간장(2큰술)".
- 재료명은 요리 맥락을 반영해 구체적으로 작성한다. 예: "소고기" 대신 "소고기 다짐육", "돼지고기" 대신 "돼지고기 삼겹살", "패티" 대신 "햄버거 패티(소고기)", "면" 대신 "우동면". 쇼핑몰에서 바로 검색 가능한 수준의 명칭을 사용한다.
- steps는 조리 단계를 순서대로 담되 번호를 붙이지 말고 문장으로 작성한다.
- 취향 반영 우선순위: source.md > 추가 요청사항 > feedback.md.
- missing_ingredients: 레시피에 필요하지만 source.md 보유 재료에 없는 것들을 담는다.
  · source.md가 비어 있으면(보유 재료 정보 없음) 빈 배열로 둔다.
- difficulty는 easy, medium, hard 중 하나로만 작성한다.

━━━ JSON 스키마 ━━━

{{
  "dish_name": "음식 이름",
  "introduction": "음식 소개 2~3문장",
  "cooking_time": "예: 30분",
  "difficulty": "easy | medium | hard",
  "servings": {servings},
  "ingredients": ["닭고기(200g)", "간장(2큰술)"],
  "steps": ["조리 단계를 순서대로 작성 (번호 없이 문장으로)"],
  "missing_ingredients": ["레시피에 필요하지만 source.md 보유 재료에 없는 것들 (source.md가 비어있으면 빈 배열)"]
}}
""".strip()


# ───────────────────────── 인분 파싱 ─────────────────────────


def parse_servings(text):
    """자연어 인분 입력을 정수로 파싱한다.

    - None/공백/"알아서 해줘"/"상관없어"/"모르겠어" 등 → None
    - "2인분", "3명", "4명이서", "5명이 먹을 거야", "6인" → int
    - 아라비아 숫자(\\d+)만 신뢰. 한국어 수사("한", "두", "세" 등)는 확신 불가 → None
    """
    if text is None:
        return None

    stripped = text.strip()
    if not stripped:
        return None

    match = re.search(r"\d+", stripped)
    if match:
        return int(match.group())

    # 아라비아 숫자가 없으면(한국어 수사 또는 "알아서"류) 확신 불가 → None
    return None


# ───────────────────────── 이미지 헬퍼 ─────────────────────────


def build_image_data_url(image_bytes, mime_type):
    encoded_image = base64.b64encode(image_bytes).decode("utf-8")
    return f"data:{mime_type};base64,{encoded_image}"


def _gemini_generate(messages: list) -> str:
    """Gemini API 호출. messages를 Gemini 형식으로 변환 후 텍스트 응답 반환."""
    import google.generativeai as genai  # noqa: PLC0415

    has_image = any(
        isinstance(msg.get("content"), list)
        and any(item.get("type") == "image_url" for item in msg["content"])
        for msg in messages
    )
    model_name = GEMINI_VISION_MODEL if has_image else GEMINI_TEXT_MODEL
    model = genai.GenerativeModel(
        model_name,
        generation_config=genai.GenerationConfig(
            response_mime_type="application/json",
            temperature=0.2,
        ),
    )
    parts = []
    for msg in messages:
        content = msg.get("content", "")
        if isinstance(content, str):
            parts.append(content)
        elif isinstance(content, list):
            for item in content:
                if item.get("type") == "text":
                    parts.append(item["text"])
                elif item.get("type") == "image_url":
                    url = item["image_url"]["url"]
                    header, b64data = url.split(",", 1)
                    mime = header.split(":")[1].split(";")[0]
                    img_bytes = base64.b64decode(b64data)
                    parts.append(
                        {
                            "inline_data": {
                                "mime_type": mime,
                                "data": base64.b64encode(img_bytes).decode(),
                            }
                        }
                    )
    response = model.generate_content(parts)
    return response.text or ""


def generate_with_retry(messages: list, retries=3, base_delay=3) -> str:
    """Gemini API 호출 + 지수 백오프 재시도. 텍스트 응답 반환."""
    for attempt in range(1, retries + 1):
        try:
            return _gemini_generate(messages)
        except Exception as e:
            print(f"API 호출 실패 (시도 {attempt}/{retries}): {e}")
            err_text = str(e).lower()
            is_rate_limited = (
                "429" in err_text
                or "quota" in err_text
                or "resource_exhausted" in err_text
                or "rate limit" in err_text
                or "too many requests" in err_text
            )
            if not is_rate_limited or attempt == retries:
                raise
            wait_seconds = base_delay * (2 ** (attempt - 1))
            st.warning(
                f"요청이 많아 잠시 대기 후 재시도합니다. ({attempt}/{retries}, {wait_seconds}초 대기)"
            )
            time.sleep(wait_seconds)


# ───────────────────────── 파이프라인 오케스트레이션 ─────────────────────────
# 아래 두 함수가 UI(session_state 플로우)에서 호출하는 공개 진입점이다.
# 둘 다 (성공: dict, 실패: None) + 파싱 실패 시 raw_text를 함께 반환한다.


def analyze_food_image(image_bytes, mime_type):
    """Call 1: Vision 분석. (result_dict_or_None, raw_text) 반환.

    raw_text는 JSON 파싱 실패 시 UI에서 st.code()로 노출하기 위한 원문.
    """
    image_data_url = build_image_data_url(image_bytes, mime_type)
    messages = [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": build_vision_prompt()},
                {"type": "image_url", "image_url": {"url": image_data_url}},
            ],
        }
    ]
    raw_text = (generate_with_retry(messages) or "").strip()
    try:
        return json.loads(raw_text), raw_text
    except json.JSONDecodeError:
        return None, raw_text


def generate_recipe(vision_result, servings, extra_requests):
    """Call 2: 레시피 생성. (result_dict_or_None, raw_text) 반환.

    source.md/feedback.md는 매 호출 시점에 로드한다.
    servings는 parse_servings() 결과(int 또는 None)를 그대로 전달하면 된다.
    """
    source_md = load_source_md()
    feedback_md = load_feedback_md()
    prompt_text = build_recipe_prompt(
        vision_result=vision_result,
        servings=servings,
        extra_requests=extra_requests,
        source_md=source_md,
        feedback_md=feedback_md,
    )
    messages = [{"role": "user", "content": prompt_text}]
    raw_text = (generate_with_retry(messages) or "").strip()
    try:
        return json.loads(raw_text), raw_text
    except json.JSONDecodeError:
        return None, raw_text


def extract_and_save_feedback(extra_requests: str):
    """extra_requests에서 장기 취향을 LLM으로 추출해 feedback.md에 타임스탬프와 함께 append."""
    from datetime import date
    if not extra_requests or not str(extra_requests).strip():
        return
    prompt = (
        "사용자의 추가 요청사항에서 장기적인 음식 취향만 추출하라. "
        "장기 취향 예시: 알러지, 식단 제한, 선호/기피 재료, 맛 선호 등. "
        "일회성 요청 예시: 조리 시간 제한, 특정 도구 없음, 특정 레시피 조정 등 — 이것들은 제외한다. "
        "장기 취향이 없으면 빈 배열을 반환한다. 순수 JSON만 반환한다.\n\n"
        f"추가 요청사항: {extra_requests}\n\n"
        '{"preferences": ["장기 취향 1", "장기 취향 2"]}'
    )
    try:
        messages = [{"role": "user", "content": prompt}]
        raw_text = generate_with_retry(messages) or "{}"
        result = json.loads(raw_text)
        preferences = result.get("preferences", [])
        if not preferences:
            return
        today = date.today().isoformat()
        lines_to_add = "\n".join(f"[{today}] - {p}" for p in preferences)
        with open(FEEDBACK_MD_PATH, "a", encoding="utf-8") as f:
            f.write(("\n" if os.path.exists(FEEDBACK_MD_PATH) else "") + lines_to_add + "\n")
    except Exception:
        pass  # feedback 업데이트 실패는 무시


_NAVER_STOP_WORDS = [
    "틀", "모양틀", "조리기", "조리도구", "프라이팬", "플레이트",
    "메이커", "커터", "성형기", "기계", "기구", "스텐", "에그팬", "쉐이퍼",
    "몰드", "액세서리", "가젯", "쿠커", "논스틱", "주방용품", "매트",
    "뒤집개", "롤러", "스프레더", "커팅기", "슬라이서", "프레서", "전용팬",
    "원형", "사각", "타원형", "삼각형", "다용도", "조리세트", "조리도구세트",
    "향수", "향기", "방향제", "영양제", "보충제", "비타민",
    "화장품", "스킨케어", "뷰티", "세럼", "로션", "크림",
    "샴푸", "바디워시", "세제", "세정제",
    "의류", "패션", "가방", "신발", "악세서리", "귀걸이", "목걸이",
    "인테리어", "가구", "조명", "벽지", "침구",
    "전자", "가전", "핸드폰", "컴퓨터", "노트북",
    "다이어리", "문구", "팬시", "장난감", "완구",
]


def get_shopping_items(ingredient: str) -> list:
    """네이버 쇼핑 API로 재료명만 검색, 정확도순 상위 3개 반환.

    반환: [{"title": str, "lprice": str, "link": str}, ...]
    API 키 미설정 또는 오류 시 빈 리스트 반환.
    """
    if not NAVER_CLIENT_ID or not NAVER_CLIENT_SECRET:
        return []
    keyword = ingredient
    try:
        resp = requests.get(
            "https://openapi.naver.com/v1/search/shop.json",
            headers={
                "X-Naver-Client-Id": NAVER_CLIENT_ID,
                "X-Naver-Client-Secret": NAVER_CLIENT_SECRET,
            },
            params={
                "query": keyword,
                "display": 15,
                "sort": "sim",
                "exclude": "used:rental:cbshop",
            },
            timeout=5,
        )
        if resp.status_code != 200:
            return []
        products = []
        for item in resp.json().get("items", []):
            title = item["title"].replace("<b>", "").replace("</b>", "")
            if any(w in title for w in _NAVER_STOP_WORDS):
                continue
            products.append({"title": title, "lprice": item["lprice"], "link": item["link"]})
            if len(products) == 3:
                break
        return products
    except Exception:
        return []


def build_naver_shopping_url(ingredient: str) -> str:
    """네이버 쇼핑 검색 URL (API 키 없을 때 fallback)."""
    return f"https://search.shopping.naver.com/search/all?query={quote(ingredient.strip())}"


def build_shopping_html(missing_ingredients: list) -> str:
    """missing_ingredients에 대한 네이버 쇼핑 검색 결과 HTML 섹션을 반환한다.

    API 키 미설정 시에는 네이버 쇼핑 검색 링크만 표시한다.
    검색 결과가 없더라도 각 재료별 검색 링크는 항상 제공된다.
    """
    if not missing_ingredients:
        return ""
    blocks = ""
    miss_count = len(missing_ingredients)
    for item in missing_ingredients:
        ing_name = _parse_ingredient_string(str(item))["name"]
        products = []
        if NAVER_CLIENT_ID and NAVER_CLIENT_SECRET:
            products = get_shopping_items(ing_name)
        search_url = html.escape(build_naver_shopping_url(ing_name), quote=True)
        blocks += (
            f'<div class="shop-block">'
            f'<a class="shop-ing-label" href="{search_url}" target="_blank" rel="noopener">'
            f'<span class="tag tag-amber">구매 필요</span>'
            f'<span class="shop-ing-name">{html.escape(ing_name)}</span>'
            f'{icon("arrow_r", 14, color="var(--ink-4)")}</a>'
        )
        if products:
            for p in products:
                raw_price = str(p.get("lprice", ""))
                price_str = f"₩{int(raw_price):,}" if raw_price.isdigit() else raw_price
                p_link = html.escape(str(p["link"]), quote=True)
                p_title = html.escape(str(p["title"]))
                blocks += (
                    f'<a href="{p_link}" target="_blank" rel="noopener" class="shop-item">'
                    f'<span class="shop-title">{p_title}</span>'
                    f'<span class="shop-price">{price_str}</span>'
                    f'</a>'
                )
        else:
            blocks += (
                f'<div class="shop-no-results">'
                f'네이버 쇼핑에서 <a href="{search_url}" target="_blank" rel="noopener">{html.escape(ing_name)}</a>을(를) 찾아보세요.'
                f'</div>'
            )
        blocks += '</div>'
    head = (
        f'<div class="shop-head">'
        f'<b>{icon("cart", 16, color="var(--naver)")} 필요한 재료 구매하기</b>'
        f'<span class="shop-head-sub">네이버 쇼핑 검색 결과 · {miss_count}개 재료</span>'
        f'</div>'
    )
    return head + blocks


# ─────────────────────────── source.md 파싱/저장 ───────────────────────────

_AMOUNT_PATTERN_STR = re.compile(
    r"^(\d+(?:[./]\d+)?)(g|kg|ml|L|개|알|큰술|작은술|컵|줄기|묶음|장|쪽|톨|팩|봉|캔)$"
)
# 기준 단위로 변환: {"원래단위": ("기준단위", 배수)}
_UNIT_CONVERSIONS: dict = {"kg": ("g", 1000), "L": ("ml", 1000)}
_ING_PAREN_PATTERN = re.compile(r"^(.+?)\((.*?)\)$")


def _parse_ingredient_string(s: str) -> dict:
    """"재료명(단위)" 또는 "재료명" → {"name": "...", "amount": "..."}"""
    s = str(s).strip()
    m = _ING_PAREN_PATTERN.match(s)
    if m:
        return {"name": m.group(1).strip(), "amount": m.group(2).strip()}
    return {"name": s, "amount": ""}


def _parse_amount_val(amount: str):
    """지원 단위이면 (quantity, base_unit) 반환. kg→g, L→ml 자동 변환. 아니면 None."""
    amount = (amount or "").strip()
    m = _AMOUNT_PATTERN_STR.match(amount)
    if not m:
        return None
    qty_str = m.group(1)
    unit = m.group(2)
    if '/' in qty_str:
        _parts = qty_str.split('/')
        q = float(_parts[0]) / float(_parts[1])
    else:
        q = float(qty_str)
    if unit in _UNIT_CONVERSIONS:
        base_unit, factor = _UNIT_CONVERSIONS[unit]
        q = q * factor
        unit = base_unit
    return (int(q) if q.is_integer() else q), unit


def _format_amount_val(quantity, unit: str) -> str:
    if isinstance(quantity, float) and quantity.is_integer():
        quantity = int(quantity)
    return f"{quantity}{unit}"


def _is_zero_amount_val(amount: str) -> bool:
    parsed = _parse_amount_val(amount)
    return parsed is not None and parsed[0] == 0


def parse_source_md_to_data(path: str = SOURCE_MD_PATH) -> dict:
    """source.md → {"ingredients": [{"name", "amount"}], "user_preferences": [...]}"""
    if not os.path.exists(path):
        return {"ingredients": [], "user_preferences": []}
    with open(path, encoding="utf-8") as f:
        text = f.read()
    sections: dict = {}
    cur_title, cur_lines = None, []
    for line in text.splitlines():
        if line.startswith("## "):
            if cur_title:
                sections[cur_title] = "\n".join(cur_lines)
            cur_title = line.replace("##", "", 1).strip()
            cur_lines = []
        else:
            cur_lines.append(line)
    if cur_title:
        sections[cur_title] = "\n".join(cur_lines)

    def _get_items(section_text: str) -> list:
        items, in_input = [], False
        for line in section_text.splitlines():
            s = line.strip()
            if s.startswith("입력:"):
                in_input = True
                continue
            if in_input:
                if re.fullmatch(r"-{3,}", s):
                    continue
                if s.startswith("-"):
                    item = s[1:].strip()
                    if item:
                        items.append(item)
        return items

    ingredient_items, preference_items = [], []
    for title, body in sections.items():
        if "재료" in title:
            ingredient_items = _get_items(body)
        elif "사용자 특성" in title:
            preference_items = _get_items(body)
    return {
        "ingredients": [_parse_ingredient_string(item) for item in ingredient_items],
        "user_preferences": preference_items,
    }


def save_source_data_to_md(source_data: dict, path: str = SOURCE_MD_PATH):
    """Structured source data → source.md 파일로 저장."""
    from datetime import date
    ingredients = [
        item for item in source_data.get("ingredients", [])
        if not _is_zero_amount_val(item.get("amount", ""))
    ]
    prefs = source_data.get("user_preferences", [])

    def _ing_line(item):
        name = item.get("name", "").strip()
        amt = item.get("amount", "").strip()
        return f"- {name}({amt})" if amt else f"- {name}"

    lines = [
        "# 내 주방 정보",
        "",
        f"최종 갱신일: {date.today().isoformat()}",
        "",
        "---",
        "",
        "## 1. 재료",
        "",
        "가지고 계신 재료를 자유롭게 적어주세요.",
        "재료명(양) 형식에 맞게 적어주세요.",
        "양을 몰라도 괜찮습니다, 재료명만 적어주세요.",
        "계량 단위는 g, ml로 통일해 주세요.",
        "",
        "예시:",
        "- 양파(5개)",
        "- 설탕(500g)",
        "- 설탕",
        "- 간장",
        "- 계란(2알)",
        "",
        "입력:",
    ] + [_ing_line(item) for item in ingredients] + [
        "",
        "---",
        "",
        "## 2. 사용자 특성",
        "",
        "레시피 생성 과정에서 요청사항이 있다면 적어주세요.",
        "",
        "예시:",
        "- 짠 음식을 싫어해요",
        "- 토마토 알러지가 있어요",
        "- 과일보다는 채소를 좋아해요",
        "- 매운 음식을 좋아해요",
        "- 다이어트 중이에요",
        "- 비건 식단을 선호해요",
        "",
        "입력:",
    ] + [f"- {p}" for p in prefs] + [""]
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))


# ─────────────────────────── 장바구니 / 완료 처리 ───────────────────────────


def save_to_cart(recipe: dict) -> str:
    """레시피를 cart/json/ 폴더에 저장한다. 저장된 경로를 반환한다."""
    from datetime import datetime
    os.makedirs(CART_JSON_DIR, exist_ok=True)
    dish_name = str(recipe.get("dish_name") or "recipe")
    safe_name = re.sub(r'[\\/:*?"<>|]', "", dish_name).strip() or "recipe"
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = f"{CART_JSON_DIR}/{timestamp}_{safe_name}.json"
    with open(path, "w", encoding="utf-8") as f:
        json.dump(recipe, f, ensure_ascii=False, indent=2)
    return path


def list_cart_items() -> list:
    """cart/json/ 의 레시피 목록을 [{path, dish_name, data}] 형태로 반환한다."""
    if not os.path.exists(CART_JSON_DIR):
        return []
    items = []
    for p in sorted(glob.glob(f"{CART_JSON_DIR}/*.json"), reverse=True):
        try:
            with open(p, encoding="utf-8") as f:
                data = json.load(f)
            items.append({
                "path": p,
                "dish_name": data.get("dish_name", os.path.basename(p)),
                "data": data,
            })
        except Exception:
            pass
    return items


def calculate_source_update(recipe: dict, source_data: dict) -> dict:
    """
    새 API 형식 레시피 + source_data → 재료 차감 결과 계산.

    반환: {"used": [...], "blocked": [...], "missing": [...], "updated_source": {...}}
    - used    : 자동 차감 가능 (name, amount, before, after)
    - blocked : 자동 차감 불가 (name, amount, reason)
    - missing : source.md에 없는 재료 (name, amount) — API missing_ingredients 기준
    - updated_source: 차감이 반영된 source_data
    """
    source_by_name = {item["name"]: item for item in source_data.get("ingredients", [])}
    all_ings = [_parse_ingredient_string(s) for s in recipe.get("ingredients", [])]
    missing_strs = recipe.get("missing_ingredients", [])
    missing_ings = [_parse_ingredient_string(s) for s in missing_strs]
    missing_names = {ing["name"] for ing in missing_ings}

    available = [ing for ing in all_ings if ing["name"] not in missing_names]
    used, blocked = [], []
    updated_by_name = {
        item["name"]: {"name": item["name"], "amount": item.get("amount", "")}
        for item in source_data.get("ingredients", [])
    }

    for ing in available:
        name, req_amount = ing["name"], ing["amount"]
        source_item = source_by_name.get(name)
        if not source_item:
            blocked.append({"name": name, "amount": req_amount, "reason": "source.md에 없는 재료"})
            continue
        cur_amount = source_item.get("amount", "").strip()
        if not cur_amount:
            blocked.append({"name": name, "amount": req_amount, "reason": "현재 보유량이 비어 있어 자동 차감 불가"})
            continue
        cur = _parse_amount_val(cur_amount)
        req = _parse_amount_val(req_amount)
        if not req:
            blocked.append({"name": name, "amount": req_amount, "reason": "필요량 단위가 지원되지 않음"})
            continue
        if not cur:
            blocked.append({"name": name, "amount": cur_amount, "reason": "현재 보유량 단위가 지원되지 않음"})
            continue
        cur_qty, cur_unit = cur
        req_qty, req_unit = req
        if cur_unit != req_unit:
            blocked.append({"name": name, "amount": req_amount, "reason": f"단위가 다름: 보유 {cur_unit}, 필요 {req_unit}"})
            continue
        if cur_qty < req_qty:
            blocked.append({"name": name, "amount": req_amount, "reason": f"보유량 부족: 보유 {cur_amount}"})
            continue
        after_qty = cur_qty - req_qty
        after_amount = _format_amount_val(after_qty, cur_unit)
        used.append({
            "name": name, "amount": req_amount,
            "before": cur_amount, "after": "삭제됨" if after_qty == 0 else after_amount,
        })
        updated_by_name[name]["amount"] = after_amount

    updated_ingredients = [
        updated_by_name.get(item["name"], item)
        for item in source_data.get("ingredients", [])
        if not _is_zero_amount_val(updated_by_name.get(item["name"], item).get("amount", ""))
    ]
    return {
        "used": used,
        "blocked": blocked,
        "missing": missing_ings,
        "updated_source": {**source_data, "ingredients": updated_ingredients},
    }


def save_to_completed(cart_path: str, completion_result: dict) -> str:
    """완료된 레시피를 completed/json/에 저장하고 cart에서 삭제한다. 저장 경로를 반환한다."""
    from datetime import date
    os.makedirs(COMPLETED_JSON_DIR, exist_ok=True)
    with open(cart_path, encoding="utf-8") as f:
        data = json.load(f)
    data["completed_at"] = date.today().isoformat()
    data["completion_result"] = {
        "used": completion_result.get("used", []),
        "blocked": completion_result.get("blocked", []),
        "missing": completion_result.get("missing", []),
    }
    basename = os.path.basename(cart_path)
    completed_path = f"{COMPLETED_JSON_DIR}/{basename}"
    with open(completed_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.remove(cart_path)
    return completed_path


# ═══════════════════════════════════════════════════════════════════════════
# UI — 3-뷰 구조 (home / chat / kitchen)
# styles.css 기반 디자인 시스템 + Streamlit 렌더링 레이어
# 파이프라인 함수(상단)는 한 줄도 수정하지 않는다.
#   · view == "home"    → 홈 보드 (히어로 + 통계 + 레시피 그리드)
#   · view == "chat"    → 채팅 플로우 (Step 1~4)
#   · view == "kitchen" → 내 주방 (재료 칩 / 취향 / 완료 처리 대기)
# ═══════════════════════════════════════════════════════════════════════════


# ─────────────────────────── PAGE CONFIG ───────────────────────────
# set_page_config는 스크립트 전체에서 최초 1회만 호출되어야 한다.
st.set_page_config(
    page_title="쿸",
    page_icon="🍽️",
    layout="wide",
    initial_sidebar_state="expanded",
)


# ─────────────────────── CSS ───────────────────────
def load_css() -> None:
    """src/style.css를 읽어 Streamlit에 주입한다."""
    css_path = Path(__file__).parent / "style.css"
    with open(css_path, "r", encoding="utf-8") as f:
        st.markdown(f"<style>{f.read()}</style>", unsafe_allow_html=True)


load_css()


# ─────────────────────────── SVG 아이콘 시스템 (icons.jsx 이식) ───────────────────────────
# Material Symbols CDN을 제거하고 icons.jsx의 stroke 기반 SVG 경로를 인라인으로 렌더한다.


def _svg_icon(paths: list, size: int = 20, sw: float = 1.75) -> str:
    """icons.jsx의 SVG 아이콘을 HTML 문자열로 반환한다.

    paths 각 항목은 문자열(path d) 또는 {"tag":"circle"|"rect","attr":{...}} dict.
    """
    path_els = ""
    for p in paths:
        if isinstance(p, str):
            path_els += f'<path d="{p}"/>'
        elif isinstance(p, dict):
            tag = p.get("tag")
            a = p.get("attr", {})
            if tag == "circle":
                path_els += f'<circle cx="{a["cx"]}" cy="{a["cy"]}" r="{a["r"]}"/>'
            elif tag == "rect":
                path_els += (
                    f'<rect x="{a["x"]}" y="{a["y"]}" width="{a["width"]}" '
                    f'height="{a["height"]}" rx="{a.get("rx", 0)}"/>'
                )
            elif tag == "path":
                path_els += f'<path d="{a.get("d", "")}"/>'
    return (
        f'<svg width="{size}" height="{size}" viewBox="0 0 24 24" fill="none" '
        f'stroke="currentColor" stroke-width="{sw}" stroke-linecap="round" '
        f'stroke-linejoin="round" style="vertical-align:middle;flex-shrink:0">'
        f"{path_els}</svg>"
    )


# icons.jsx에서 그대로 가져온 경로 정의
_ICON_PATHS = {
    "spark": ['M12 3v3M12 18v3M3 12h3M18 12h3', 'M12 8.5a3.5 3.5 0 0 0 3.5 3.5A3.5 3.5 0 0 0 12 15.5 3.5 3.5 0 0 0 8.5 12 3.5 3.5 0 0 0 12 8.5Z'],
    "chef": [
        'M6 14h12v5a1 1 0 0 1-1 1H7a1 1 0 0 1-1-1v-5Z',
        'M7 14a4 4 0 0 1-1-7.9A3.5 3.5 0 0 1 12 4a3.5 3.5 0 0 1 6 2.1A4 4 0 0 1 17 14',
        'M9 17h.01M12 17h.01M15 17h.01',
    ],
    "home": ['M3 10.5 12 4l9 6.5', 'M5 9.5V20h14V9.5', 'M9.5 20v-5h5v5'],
    "chat": ['M4 5h16v11H8l-4 4V5Z', 'M8.5 10h7M8.5 13h4'],
    "fridge": ['M6 3h12v18H6zM6 9h12M9.5 5.5v1.5M9.5 12v3'],
    "bookmark": ['M6 4h12v16l-6-4-6 4V4Z'],
    "cart": ['M4 5h2l1.6 9.3a1 1 0 0 0 1 .7h7.8a1 1 0 0 0 1-.8L20 8H7', {"tag": "circle", "attr": {"cx": 9, "cy": 19, "r": 1.4}}, {"tag": "circle", "attr": {"cx": 17, "cy": 19, "r": 1.4}}],
    "camera": ['M4 8h3l1.5-2h7L17 8h3v11H4V8Z', {"tag": "circle", "attr": {"cx": 12, "cy": 13, "r": 3.2}}],
    "image": ['M4 5h16v14H4zM4 16l4.5-4.5 4 4L16 12l4 4', {"tag": "circle", "attr": {"cx": 9, "cy": 9, "r": 1.4}}],
    "upload": ['M12 16V5M8 9l4-4 4 4', 'M5 19h14'],
    "send": ['M5 12 20 5l-4 15-4-6-7-2Z'],
    "plus": ['M12 5v14M5 12h14'],
    "check": ['M5 12.5 10 17l9-10'],
    "check_circle": [{"tag": "circle", "attr": {"cx": 12, "cy": 12, "r": 9}}, 'M8.5 12.5 11 15l4.5-5.5'],
    "x": ['M6 6l12 12M18 6 6 18'],
    "edit": ['M5 19h14', 'M14.5 5.5l3 3M6 16l9-9 3 3-9 9H6v-3Z'],
    "refresh": ['M20 11a8 8 0 0 0-14-4.5L4 8M4 4v4h4', 'M4 13a8 8 0 0 0 14 4.5L20 16M20 20v-4h-4'],
    "clock": [{"tag": "circle", "attr": {"cx": 12, "cy": 12, "r": 8.5}}, 'M12 7.5V12l3 2'],
    "gauge": ['M5 17a8 8 0 1 1 14 0', 'M12 17l3.5-5'],
    "users": [{"tag": "circle", "attr": {"cx": 9, "cy": 8, "r": 3}}, 'M3.5 19a5.5 5.5 0 0 1 11 0', 'M16 5.3a3 3 0 0 1 0 5.4M16.5 13.2A5.5 5.5 0 0 1 20.5 18.5'],
    "leaf": ['M5 19c0-8 5-13 14-13 0 9-5 14-14 13Z', 'M9 15c2-2.5 4.5-4.5 7-5.5'],
    "flame": ['M12 3c1 3 4 4 4 8a4 4 0 0 1-8 0c0-1 .3-2 1-2.6C9 11 12 9 12 3Z'],
    "ban": [{"tag": "circle", "attr": {"cx": 12, "cy": 12, "r": 8.5}}, 'M6 6l12 12'],
    "heart": ['M12 20S4 14.5 4 9a4 4 0 0 1 8-1 4 4 0 0 1 8 1c0 5.5-8 11-8 11Z'],
    "arrow_r": ['M5 12h14M13 6l6 6-6 6'],
    "chev_r": ['M9 6l6 6-6 6'],
    "sparkle": ['M12 4l1.6 4.8L18 10l-4.4 1.2L12 16l-1.6-4.8L6 10l4.4-1.2L12 4Z'],
    "trash": ['M5 7h14M9 7V5h6v2M7 7l1 13h8l1-13'],
    "book": ['M5 4h11a2 2 0 0 1 2 2v14H7a2 2 0 0 1-2-2V4Z', 'M5 16h13'],
}


def icon(name: str, size: int = 20, sw: float = 1.75, color: str = None) -> str:
    """이름으로 SVG 아이콘 HTML을 반환한다. size/sw/color 오버라이드 가능."""
    paths = _ICON_PATHS.get(name)
    if not paths:
        return ""
    svg = _svg_icon(paths, size=size, sw=sw)
    if color:
        svg = svg.replace(
            'style="vertical-align:middle;flex-shrink:0"',
            f'style="color:{color};vertical-align:middle;flex-shrink:0"',
        )
    return svg


# ─────────────────────────── SESSION STATE ───────────────────────────
SESSION_DEFAULTS = {
    "step": 1,
    "image_bytes": None,
    "mime_type": None,
    "vision_result": None,
    "servings": None,
    "extra_requests": None,
    "recipe_result": None,
    "chat_history": [],  # [{"role": "ai"|"user", "content": str}]
    "recipe_confirmed": False,
    "awaiting_revision": False,
    "reanalyze_pending": False,
    "_pending_analyze": False,
    "_last_file_name": None,
    "_typing": False,
    "_pending_recipe": False,
    "viewing_recipe": None,
    # 장바구니 완료 처리 (채팅 내 — 하위 호환)
    "cart_selected_path": None,
    "cart_completion_result": None,
    "cart_source_edit_mode": False,
    "cart_source_edit_data": None,
    # 사이드바 저장 레시피 → 채팅 로드 트리거
    "load_recipe_pending": None,
    # ── 3-뷰 구조 (신규) ──
    "view": "home",
    "kitchen_ingredients": None,    # None = 미초기화 (init 시 source.md 로드)
    "kitchen_preferences": None,    # None = 미초기화
    "adding_pref_mode": False,
    "home_layout": "grid",
    "kitchen_complete_target": None,
    "kitchen_complete_result": None,
    "kitchen_complete_dialog_open": False,
    "kitchen_source_edit_mode": False,
    "kitchen_source_edit_data": None,
    "kitchen_complete_success_msg": None,
    "_recipe_loading": False,
    "_recipe_loading_data": None,
}


def init_session_state():
    for key, val in SESSION_DEFAULTS.items():
        if key not in st.session_state:
            st.session_state[key] = [] if isinstance(val, list) else val
    # kitchen 재료/취향이 미초기화(None)면 source.md에서 로드한다.
    if st.session_state.get("kitchen_ingredients") is None or st.session_state.get("kitchen_preferences") is None:
        _src = parse_source_md_to_data()
        if st.session_state.get("kitchen_ingredients") is None:
            st.session_state["kitchen_ingredients"] = list(_src.get("ingredients", []))
        if st.session_state.get("kitchen_preferences") is None:
            st.session_state["kitchen_preferences"] = list(_src.get("user_preferences", []))


init_session_state()


# ─────────────────────────── 공통 헬퍼 ───────────────────────────
DIFFICULTY_LABELS = {"easy": "쉬움", "medium": "보통", "hard": "어려움"}


def scroll_to_bottom():
    """리렌더링 후 채팅 최하단으로 스크롤한다."""
    components.html(
        "<script>"
        "var m=window.parent.document.querySelector('[data-testid=\"stMain\"]');"
        "if(m)m.scrollTo({top:m.scrollHeight,behavior:'smooth'});"
        "</script>",
        height=0,
        scrolling=False,
    )


def get_ai_client():
    """Gemini API 클라이언트를 초기화하고 None을 반환한다."""
    try:
        import google.generativeai as genai  # noqa: PLC0415
    except ImportError:
        st.error("Gemini 사용을 위해 'pip install google-generativeai'를 실행하세요.")
        st.stop()
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        st.error("GEMINI_API_KEY가 설정되지 않았습니다. .env 파일에 키를 추가해주세요.")
        st.stop()
    genai.configure(api_key=api_key)
    return None


def handle_api_error(e: Exception):
    """API 호출 예외를 분류해 사용자 메시지를 출력하고 흐름을 중단한다."""
    err_text = str(e).lower()
    is_rate_limited = (
        "429" in err_text
        or "quota" in err_text
        or "resource_exhausted" in err_text
        or "rate limit" in err_text
        or "too many requests" in err_text
    )
    if is_rate_limited:
        st.error("현재 Gemini API 요청 한도를 초과했습니다. 잠시 후 다시 시도해주세요.")
    else:
        st.error("요청 처리 중 오류가 발생했습니다.")
        st.exception(e)
    st.stop()


def reset_all():
    """채팅/완료 처리 관련 session_state를 초기화하고 홈으로 되돌린다.

    kitchen_ingredients/preferences는 None으로 두어 다음 init 시 source.md에서 재로드한다.
    """
    for key, default in {
        "step": 1,
        "image_bytes": None,
        "mime_type": None,
        "vision_result": None,
        "servings": None,
        "extra_requests": None,
        "recipe_result": None,
        "chat_history": [],
        "recipe_confirmed": False,
        "awaiting_revision": False,
        "reanalyze_pending": False,
        "_pending_analyze": False,
        "_pending_recipe": False,
        "_last_file_name": None,
        "_typing": False,
        "viewing_recipe": None,
        "cart_selected_path": None,
        "cart_completion_result": None,
        "cart_source_edit_mode": False,
        "cart_source_edit_data": None,
        "load_recipe_pending": None,
        "view": "home",
        "kitchen_ingredients": None,
        "kitchen_preferences": None,
        "adding_pref_mode": False,
        "home_layout": "grid",
        "kitchen_complete_target": None,
        "kitchen_complete_result": None,
        "kitchen_complete_dialog_open": False,
        "kitchen_source_edit_mode": False,
        "kitchen_source_edit_data": None,
        "kitchen_complete_success_msg": None,
        "_recipe_loading": False,
        "_recipe_loading_data": None,
    }.items():
        st.session_state[key] = [] if isinstance(default, list) else default


def fmt_ingredient(ing):
    """재료 객체({"name","amount"})를 "재료(단위)" 문자열로 변환한다."""
    if isinstance(ing, dict):
        name = ing.get("name", "")
        amount = ing.get("amount", "")
        return f"{name}({amount})" if amount else name
    return str(ing)


def add_ai_message(content: str):
    """AI 메시지를 chat_history에 추가한다. 직전 AI 메시지와 중복이면 추가하지 않는다."""
    history = st.session_state["chat_history"]
    if history and history[-1]["role"] == "ai" and history[-1]["content"] == content:
        return
    history.append({"role": "ai", "content": content})


def add_user_message(content: str):
    """사용자 메시지를 chat_history에 추가한다."""
    st.session_state["chat_history"].append({"role": "user", "content": content})


def render_chat_history():
    """chat_history 전체를 순서대로 렌더링한다.

    - AI 버블 content는 HTML(레시피 카드 등)을 포함할 수 있다.
    - USER 버블 content는 반드시 html.escape() 처리한다.
    """
    for msg in st.session_state["chat_history"]:
        if msg["role"] == "ai":
            st.markdown(
                f'''
            <div class="msg-ai">
                <div class="msg-ai-avatar">{icon("chef", 18)}</div>
                <div class="bubble-ai">{msg["content"]}</div>
            </div>''',
                unsafe_allow_html=True,
            )
        else:
            st.markdown(
                f'''
            <div class="msg-user">
                <div class="bubble-user">{html.escape(str(msg["content"]))}</div>
            </div>''',
                unsafe_allow_html=True,
            )

    # 타이핑 인디케이터
    if st.session_state.get("_typing"):
        st.markdown(
            """<div class="msg-ai">
          <div class="msg-ai-avatar" style="font-size:17px">🍽️</div>
          <div class="bubble-ai">
            <div class="typing"><i></i><i></i><i></i></div>
          </div>
        </div>""",
            unsafe_allow_html=True,
        )


def build_recipe_card_html(recipe: dict) -> str:
    """레시피 dict를 AI 버블 안에 들어갈 레시피 카드 HTML로 변환한다.

    missing_ingredients 기준으로 재료를 have(초록)/missing(앰버)으로 구분한다.
    모든 텍스트 필드는 html.escape() 처리한다.
    """
    dish_name = html.escape(str(recipe.get("dish_name") or "레시피"))
    introduction = html.escape(str(recipe.get("introduction") or ""))
    cooking_time = html.escape(str(recipe.get("cooking_time") or "-"))
    difficulty = recipe.get("difficulty") or ""
    difficulty_kor = html.escape(DIFFICULTY_LABELS.get(difficulty, difficulty or "-"))
    difficulty_color = {"easy": "var(--green)", "medium": "var(--amber)", "hard": "var(--rose)"}.get(difficulty, "var(--ink-3)")
    servings_val = recipe.get("servings")
    servings_label = f"{html.escape(str(servings_val))}인분" if servings_val else "-"

    missing_names: set = set()
    for m in (recipe.get("missing_ingredients") or []):
        missing_names.add(_parse_ingredient_string(str(m))["name"])

    ingredients = recipe.get("ingredients") or []
    if ingredients:
        pills_html = ""
        for ing in ingredients:
            parsed = _parse_ingredient_string(fmt_ingredient(ing))
            name = html.escape(parsed["name"])
            amount = html.escape(parsed["amount"])
            is_missing = parsed["name"] in missing_names
            pill_cls = "ing-pill missing" if is_missing else "ing-pill have"
            check_mark = icon("x", 12, sw=2.4) if is_missing else icon("check", 12, sw=2.4)
            amt_display = "구매 필요" if is_missing else amount
            pills_html += (
                f'<div class="{pill_cls}">'
                f'<div class="ing-check">{check_mark}</div>'
                f'<span class="ing-name">{name}</span>'
                f'<span class="ing-amt mono">{amt_display}</span>'
                f'</div>'
            )
        ingredients_html = f'<div class="ing-grid">{pills_html}</div>'
    else:
        ingredients_html = '<p style="color:var(--ink-3);font-size:0.88rem">재료 정보 없음</p>'

    steps = recipe.get("steps") or []
    if steps:
        steps_html = "".join(
            f'<div class="step-item">'
            f'<div class="step-num">{i}</div>'
            f'<div>{html.escape(str(step_text))}</div>'
            f'</div>'
            for i, step_text in enumerate(steps, start=1)
        )
    else:
        steps_html = '<div class="step-item">조리 단계 정보가 없습니다.</div>'

    intro_html = (
        f'<div class="rc-intro">{introduction}</div>'
        if introduction else ""
    )

    ing_n = len(ingredients)
    step_n = len(steps)

    return f"""<div class="recipe-card">
<div class="rc-head">
  <div class="rc-eyebrow">맞춤 레시피</div>
  <div class="rc-title">{dish_name}</div>
  {intro_html}
</div>
<div class="rc-meta">
  <div class="rc-meta-item">
    <span class="rc-meta-ic">{icon("clock", 16)}</span>
    <div><span class="rc-meta-k">조리 시간</span><span class="rc-meta-v mono">{cooking_time}</span></div>
  </div>
  <div class="rc-meta-item">
    <span class="rc-meta-ic">{icon("gauge", 16, color=difficulty_color)}</span>
    <div><span class="rc-meta-k">난이도</span><span class="rc-meta-v mono">{difficulty_kor}</span></div>
  </div>
  <div class="rc-meta-item">
    <span class="rc-meta-ic">{icon("users", 16)}</span>
    <div><span class="rc-meta-k">인분</span><span class="rc-meta-v mono">{servings_label}</span></div>
  </div>
</div>
<div class="rc-section">
  <div class="rc-section-title">{icon("leaf", 17, color="var(--green)")} 재료<span class="n">{ing_n}개</span></div>
  {ingredients_html}
</div>
<div class="rc-section">
  <div class="rc-section-title">{icon("chef", 17, color="var(--blue)")} 조리법<span class="n">{step_n}단계</span></div>
  {steps_html}
</div>
</div>"""


def _save_kitchen_to_source():
    """현재 session_state의 kitchen 재료/취향을 source.md에 저장한다."""
    save_source_data_to_md({
        "ingredients": st.session_state.get("kitchen_ingredients") or [],
        "user_preferences": st.session_state.get("kitchen_preferences") or [],
    })


# ═══════════════════════════════════════════════════════════════════════════
# 사이드바
# ═══════════════════════════════════════════════════════════════════════════
def render_sidebar():
    with st.sidebar:
        # 브랜드
        st.markdown(
            f"""
            <div class="sb-brand">
              <div class="sb-logo">{icon("chef", 21)}</div>
              <div>
                <div class="sb-brand-name">쿸</div>
                <div class="sb-brand-sub">사진 한 장으로 요리 시작</div>
              </div>
            </div>
            """,
            unsafe_allow_html=True,
        )

        # 새 레시피 버튼
        if st.button("＋  새 레시피 만들기", key="sb_new_recipe", use_container_width=True, type="primary"):
            st.session_state["view"] = "chat"
            st.session_state["step"] = 1
            st.session_state["chat_history"] = []
            st.session_state["image_bytes"] = None
            st.session_state["mime_type"] = None
            st.session_state["vision_result"] = None
            st.session_state["servings"] = None
            st.session_state["extra_requests"] = None
            st.session_state["recipe_result"] = None
            st.session_state["recipe_confirmed"] = False
            st.session_state["awaiting_revision"] = False
            st.session_state["reanalyze_pending"] = False
            st.session_state["viewing_recipe"] = None
            st.session_state["cart_selected_path"] = None
            st.session_state["cart_completion_result"] = None
            st.rerun()

        # ── 네비게이션 ──
        # 패턴 B: HTML div(data-nav 속성) + components.html JS → 숨겨진 st.button 클릭
        # CSS로 st.button을 height:0 invisible로 만들고, JS 리스너가 트리거
        _cur_view = st.session_state.get("view", "home")
        nav_items = [
            ("home",    "home",   "홈"),
            ("chat",    "chat",   "레시피 채팅"),
            ("kitchen", "fridge", "내 주방"),
        ]
        _nav_html = '<nav class="sb-nav">'
        for view_key, ic_name, label in nav_items:
            active_cls = " active" if _cur_view == view_key else ""
            _nav_html += (
                f'<div class="sb-nav-item{active_cls}" data-nav="{view_key}" style="cursor:pointer">'
                f'{icon(ic_name, 18)}<span>{label}</span></div>'
            )
        _nav_html += '</nav>'
        st.markdown(_nav_html, unsafe_allow_html=True)

        # 숨겨진 Streamlit 버튼들 (components.html JS 트리거 대상)
        for view_key, _, _ in nav_items:
            if st.button("_", key=f"nav_{view_key}", use_container_width=True):
                st.session_state["view"] = view_key
                st.rerun()

        st.markdown('<div class="sb-section-label">저장된 레시피</div>', unsafe_allow_html=True)

        # ── 저장된 레시피 목록 ──
        _cart_items = list_cart_items()
        _all_items = []
        for ci in _cart_items:
            _all_items.append({"path": ci["path"], "dish_name": ci["dish_name"], "data": ci["data"], "kind": "cart"})
        for rf in sorted(glob.glob(f"{COMPLETED_JSON_DIR}/*.json"), reverse=True):
            try:
                with open(rf, encoding="utf-8") as _f:
                    _rd = json.load(_f)
                _all_items.append({
                    "path": rf,
                    "dish_name": _rd.get("dish_name", os.path.basename(rf)),
                    "data": _rd,
                    "kind": "library",
                })
            except Exception:
                pass

        if not _all_items:
            st.markdown(
                '<div style="font-size:12.5px;color:var(--ink-3);padding:6px 10px">아직 저장된 레시피가 없어요.</div>',
                unsafe_allow_html=True,
            )
        else:
            for _item in _all_items[:10]:
                _is_cart = _item["kind"] == "cart"
                _saved_at = str((_item.get("data") or {}).get("saved_at") or "").strip()
                _meta = ("장바구니 · " + _saved_at) if (_is_cart and _saved_at) else (
                    "장바구니 · 대기" if _is_cart else "보관함 · 완료"
                )
                _name = str(_item["dish_name"])[:16]
                _safe_key = _tile_key(_item["path"])
                # 비주얼 HTML (디자인 유지)
                st.markdown(
                    f"""<div class="sb-recipe-wrap" style="pointer-events:none">
                      <div class="sb-recipe-thumb">🍽️</div>
                      <div style="min-width:0;flex:1">
                        <div class="sb-recipe-name">{html.escape(_name)}</div>
                        <div class="sb-recipe-meta">{html.escape(_meta)}</div>
                      </div>
                      <span class="sb-status-dot {'dot-cart' if _is_cart else 'dot-done'}"></span>
                    </div>""",
                    unsafe_allow_html=True,
                )
                # 투명 오버레이 버튼 (클릭 처리)
                if st.button(
                    "​",
                    key=f"sb_recipe_{_safe_key}",
                    use_container_width=True,
                ):
                    st.session_state["view"] = "chat"
                    st.session_state["load_recipe_pending"] = _item["data"]
                    st.session_state["viewing_recipe"] = _item["path"]
                    if _is_cart:
                        st.session_state["cart_selected_path"] = _item["path"]
                    st.rerun()

            if len(_all_items) > 10:
                st.markdown(
                    f'<div style="font-size:11.5px;color:var(--ink-3);padding:4px 10px 8px">+{len(_all_items)-10}개 더 있어요 · 내 주방에서 확인</div>',
                    unsafe_allow_html=True,
                )

        # ── 사이드바 하단 프로필 ──
        st.markdown(
            f"""
            <div class="sb-foot">
              <div class="sb-avatar">AI</div>
              <div style="min-width:0;flex:1">
                <div style="font-size:13px;font-weight:700;color:var(--ink)">내 주방</div>
                <div class="sb-brand-sub">{GEMINI_TEXT_MODEL}</div>
              </div>
            </div>
            """,
            unsafe_allow_html=True,
        )

        # ============================================================
        # DEV MODE — 브랜드 로고 5번 클릭으로 토글 (숨겨진 트리거)
        # ============================================================
        if st.button("_dev", key="_dev_toggle_btn"):
            st.session_state["_dev_mode"] = not st.session_state.get("_dev_mode", False)
            st.rerun()
        # ============================================================
        # DEV MODE END
        # ============================================================

        # nav data-nav 속성 → hidden nav 버튼 연결 (recipe는 이제 직접 st.button 사용)
        # + 브랜드 로고 5번 클릭 → dev mode 트리거
        components.html(
            """
<script>
(function() {
  var doc = window.parent.document;
  var clickCount = 0;
  var clickTimer = null;

  function attach() {
    // nav 연결
    doc.querySelectorAll('[data-nav]').forEach(function(el) {
      if (el.dataset.bound) return;
      el.dataset.bound = '1';
      el.addEventListener('click', function() {
        var v = el.getAttribute('data-nav');
        var btn = doc.querySelector('.st-key-nav_' + v + ' button');
        if (btn) btn.click();
      });
    });

    // 브랜드 로고 5번 클릭 → dev mode 트리거
    var brand = doc.querySelector('.sb-brand');
    if (brand && !brand.dataset.devBound) {
      brand.dataset.devBound = '1';
      brand.addEventListener('click', function() {
        clickCount++;
        if (clickTimer) clearTimeout(clickTimer);
        clickTimer = setTimeout(function() { clickCount = 0; }, 2000);
        if (clickCount >= 5) {
          clickCount = 0;
          var devBtn = doc.querySelector('.st-key-_dev_toggle_btn button');
          if (devBtn) devBtn.click();
        }
      });
    }

    // Bug 3: 사이드바 항상 표시 (JS 보강)
    var sb = doc.querySelector('[data-testid="stSidebar"]');
    if (sb) {
      sb.style.transform = 'none';
      sb.style.visibility = 'visible';
      var colBtn = doc.querySelector('[data-testid="stSidebarCollapseButton"]');
      if (colBtn) colBtn.style.display = 'none';
    }
  }
  setTimeout(attach, 100);
  setTimeout(attach, 500);
})();
</script>
""",
            height=0,
        )


# ═══════════════════════════════════════════════════════════════════════════
# 홈 보드
# ═══════════════════════════════════════════════════════════════════════════
def _tile_key(path: str) -> str:
    """레시피 타일/완료 버튼용 안전한 st.button key를 생성한다.

    os.path.basename에는 점(.)·하이픈 등 CSS selector에 부적합한 문자가 있으므로
    영숫자/언더스코어만 남기고 정규화한다.
    """
    bn = os.path.basename(path)
    return re.sub(r"[^a-zA-Z0-9_]", "_", bn)


def _build_recipe_tile_html(recipe: dict, path: str, kind: str) -> str:
    """홈 보드용 레시피 타일 HTML을 반환한다 (data-tile-key → components.html JS로 트리거)."""
    dish_name = html.escape(str(recipe.get("dish_name") or "레시피"))
    intro = html.escape(str(recipe.get("introduction") or ""))[:60]
    cooking_time = html.escape(str(recipe.get("cooking_time") or "-"))
    servings = html.escape(str(recipe.get("servings") or "-"))
    badge_cls = "badge-cart" if kind == "cart" else "badge-done"
    badge_icon = icon("cart", 13) if kind == "cart" else icon("check_circle", 13)
    badge_label = "장바구니" if kind == "cart" else "완료"
    safe_key = _tile_key(path)

    return f"""<div class="r-tile fade-up" data-tile-key="{safe_key}" style="cursor:pointer">
      <div class="r-tile-hero ph" style="display:flex;align-items:center;justify-content:center;">
        <span style="font-size:34px;filter:saturate(.9)">🍽️</span>
        <span class="r-tile-badge {badge_cls}">{badge_icon}{badge_label}</span>
      </div>
      <div class="r-tile-body">
        <div class="r-tile-name">{dish_name}</div>
        <div class="r-tile-intro">{intro}</div>
        <div class="r-tile-foot">
          <span>{icon("clock", 14)} {cooking_time}</span>
          <span>{icon("users", 14)} {servings}인분</span>
        </div>
      </div>
    </div>"""


def render_home_view():
    # topbar
    st.markdown(
        f"""
    <div class="topbar">
      <button class="sidebar-toggle" data-sidebar-toggle="1">{icon("menu", 20)}</button>
      <div class="k-panel-ic" style="width:34px;height:34px;background:var(--blue-soft);color:var(--blue);border-radius:10px;display:grid;place-items:center">{icon("home", 18)}</div>
      <div>
        <div class="topbar-title">홈</div>
        <div class="topbar-sub">레시피 대시보드</div>
      </div>
      <div class="topbar-spacer"></div>
    </div>
    """,
        unsafe_allow_html=True,
    )

    # 통계 계산
    _cart_items = list_cart_items()
    _library_files = sorted(glob.glob(f"{COMPLETED_JSON_DIR}/*.json"), reverse=True)
    _ing_count = len(st.session_state.get("kitchen_ingredients") or [])
    _cart_count = len(_cart_items)
    _done_count = len(_library_files)
    _total_count = _cart_count + _done_count

    # 전체 레시피 목록 구성 (cart 먼저, 그다음 보관함)
    _all_recipes = []
    for ci in _cart_items:
        _all_recipes.append({"data": ci["data"], "path": ci["path"], "kind": "cart"})
    for rf in _library_files:
        try:
            with open(rf, encoding="utf-8") as _f:
                _rd = json.load(_f)
            _all_recipes.append({"data": _rd, "path": rf, "kind": "library"})
        except Exception:
            pass

    # ── board 단일 렌더링 (hero + stats + cart + recent) ──
    # 통계 카드
    _stat_html = f"""
      <div class="stat-row">
        <div class="stat-card fade-up">
          <div class="stat-ic" style="background:var(--blue-soft);color:var(--blue)">{icon("bookmark", 18)}</div>
          <div class="stat-v mono">{_total_count}</div>
          <div class="stat-k">저장된 레시피</div>
        </div>
        <div class="stat-card fade-up" style="animation-delay:.04s">
          <div class="stat-ic" style="background:var(--amber-soft);color:var(--amber-ink)">{icon("cart", 18)}</div>
          <div class="stat-v mono">{_cart_count}</div>
          <div class="stat-k">장바구니 대기</div>
        </div>
        <div class="stat-card fade-up" style="animation-delay:.08s">
          <div class="stat-ic" style="background:var(--green-soft);color:var(--green-ink)">{icon("check_circle", 18)}</div>
          <div class="stat-v mono">{_done_count}</div>
          <div class="stat-k">완료한 요리</div>
        </div>
        <div class="stat-card fade-up" style="animation-delay:.12s">
          <div class="stat-ic" style="background:var(--surface-3);color:var(--ink-2)">{icon("fridge", 18)}</div>
          <div class="stat-v mono">{_ing_count}</div>
          <div class="stat-k">보유 재료</div>
        </div>
      </div>"""

    # 장바구니 섹션 (있을 때만)
    _cart_section = ""
    if _cart_items:
        _cart_tiles = "".join(
            _build_recipe_tile_html(ci["data"], ci["path"], "cart") for ci in _cart_items[:6]
        )
        _cart_section = f"""
      <div class="board-row">
        <h2>{icon("cart", 18, color="var(--amber-ink)")} 이어서 완료하기</h2>
        <button class="link" data-cta="link-kitchen" style="cursor:pointer">주방 재료 보기 {icon("chev_r", 15)}</button>
      </div>
      <div class="recipe-grid">{_cart_tiles}</div>"""

    # 최근 레시피 섹션
    if _all_recipes:
        _recent_tiles = "".join(
            _build_recipe_tile_html(r["data"], r["path"], r["kind"]) for r in _all_recipes[:6]
        )
        _recent_section = f"""
      <div class="board-row">
        <h2>최근 레시피 기록</h2>
        <span style="font-size:12.5px;color:var(--ink-3);font-weight:600">{len(_all_recipes)}개 · 음식 사진에서 생성됨</span>
      </div>
      <div class="recipe-grid">{_recent_tiles}</div>"""
    else:
        _recent_section = f"""
      <div class="empty">
        <div class="empty-ic">{icon("chef", 26)}</div>
        <div>아직 레시피가 없어요.<br>음식 사진을 올려서 첫 레시피를 만들어보세요!</div>
      </div>"""

    st.markdown(
        f"""
    <div class="board fade-up">
      <div class="board-hero">
        <div class="bh-eyebrow">오늘은 어떤 요리를 해볼까요?</div>
        <div class="bh-title">냉장고 속 음식 사진,<br>레시피로 바꿔드릴게요</div>
        <div class="bh-sub">음식 사진을 올리면 AI가 재료를 분석하고, 내 주방에 있는 재료에 맞춰 딱 맞는 한국어 레시피를 만들어 드려요.</div>
        <div class="bh-cta">
          <button class="bh-btn" data-cta="cta-new" style="cursor:pointer">{icon("camera", 18)} 음식 사진으로 시작하기</button>
          <button class="bh-btn ghost" data-cta="cta-kitchen" style="cursor:pointer">{icon("fridge", 18)} 내 주방 관리</button>
        </div>
      </div>
      {_stat_html}
      {_cart_section}
      {_recent_section}
    </div>
    """,
        unsafe_allow_html=True,
    )

    # ── 숨겨진 Streamlit 트리거 버튼들 ──
    # 홈 CTA
    if st.button("음식 사진으로 시작하기", use_container_width=True, type="primary", key="home_cta_new"):
        st.session_state["view"] = "chat"
        st.session_state["step"] = 1
        st.session_state["chat_history"] = []
        st.session_state["image_bytes"] = None
        st.session_state["mime_type"] = None
        st.session_state["vision_result"] = None
        st.session_state["recipe_result"] = None
        st.session_state["recipe_confirmed"] = False
        st.rerun()
    if st.button("내 주방 관리", use_container_width=True, key="home_cta_kitchen"):
        st.session_state["view"] = "kitchen"
        st.rerun()
    if st.button("주방 재료 보기", use_container_width=True, key="home_link_kitchen"):
        st.session_state["view"] = "kitchen"
        st.rerun()

    # 레시피 타일 트리거 버튼들
    for r in _all_recipes[:6]:
        _safe_key = _tile_key(r["path"])
        if st.button("_", key=f"tile_{_safe_key}", use_container_width=True):
            st.session_state["view"] = "chat"
            st.session_state["load_recipe_pending"] = r["data"]
            if r["kind"] == "cart":
                st.session_state["cart_selected_path"] = r["path"]
            st.rerun()

    # ── 패턴 B: data 속성 → hidden st.button 연결 (hero CTA + 타일) ──
    components.html(
        """
<script>
(function() {
  var doc = window.parent.document;
  function attach() {
    // hero CTA + 장바구니 링크
    var ctaMap = {
      'cta-new': 'home_cta_new',
      'cta-kitchen': 'home_cta_kitchen',
      'link-kitchen': 'home_link_kitchen'
    };
    Object.keys(ctaMap).forEach(function(cta) {
      var el = doc.querySelector('[data-cta="' + cta + '"]');
      if (!el || el.dataset.bound) return;
      el.dataset.bound = '1';
      el.addEventListener('click', function() {
        var btn = doc.querySelector('.st-key-' + ctaMap[cta] + ' button');
        if (btn) btn.click();
      });
    });
    // 레시피 타일들
    doc.querySelectorAll('[data-tile-key]').forEach(function(tile) {
      if (tile.dataset.bound) return;
      tile.dataset.bound = '1';
      tile.addEventListener('click', function() {
        var key = tile.getAttribute('data-tile-key');
        var btn = doc.querySelector('.st-key-tile_' + key + ' button');
        if (btn) btn.click();
      });
    });
    // body class 마커 제거 (kitchen-mode 격리)
    doc.body.classList.remove('kitchen-mode');
    // 사이드바 토글 버튼
    doc.querySelectorAll('[data-sidebar-toggle]').forEach(function(btn) {
      if (btn.dataset.bound) return;
      btn.dataset.bound = '1';
      btn.addEventListener('click', function(e) {
        e.stopPropagation();
        var sb = doc.querySelector('[data-testid="stSidebar"]');
        if (!sb) return;
        var sbBtn = sb.querySelector('button');
        if (sbBtn) sbBtn.click();
      });
    });
  }
  setTimeout(attach, 80);
})();
</script>
""",
        height=0,
    )


# ═══════════════════════════════════════════════════════════════════════════
# 내 주방
# ═══════════════════════════════════════════════════════════════════════════
@st.dialog("완료 처리")
def _show_completion_dialog() -> None:
    """완료 처리 모달: 재료 차감 내역 확인 후 확정."""
    comp_target = st.session_state.get("kitchen_complete_target")
    comp = st.session_state.get("kitchen_complete_result")
    if not comp_target or not comp:
        st.rerun()
        return
    try:
        with open(comp_target, encoding="utf-8") as _fd:
            _rdata = json.load(_fd)
        _dish_nm = html.escape(str(_rdata.get("dish_name") or "레시피"))
    except Exception:
        _dish_nm = "레시피"

    st.markdown(f"**{_dish_nm}** — 사용한 재료를 주방에서 자동 차감해요")

    _used_d = comp.get("used", [])
    _blocked_d = comp.get("blocked", [])
    _missing_d = comp.get("missing", [])

    if _used_d:
        st.markdown("**✓ 자동 차감**")
        _lines_html = ""
        for _it in _used_d:
            _lines_html += (
                f'<div class="complete-line">'
                f'<span class="cl-ic" style="background:var(--green-soft);color:var(--green-ink)">'
                f'{icon("check", 14)}</span>'
                f'<span class="nm">{html.escape(_it["name"])}</span>'
                f'<span style="font-size:12px;color:var(--ink-3);margin-left:6px">{html.escape(_it["amount"])}</span>'
                f'<span class="chg">{html.escape(_it["before"])} → <b>{html.escape(_it["after"])}</b></span>'
                f'</div>'
            )
        st.markdown(_lines_html, unsafe_allow_html=True)
    if _blocked_d:
        st.markdown("**⚠ 자동 차감 불가**")
        _blocked_html = ""
        for _it in _blocked_d:
            _blocked_html += (
                f'<div class="complete-line">'
                f'<span class="cl-ic" style="background:var(--amber-soft);color:var(--amber)">⚠</span>'
                f'<span class="nm">{html.escape(_it["name"])}</span>'
                f'<span class="chg">{html.escape(_it["reason"])}</span>'
                f'</div>'
            )
        st.markdown(_blocked_html, unsafe_allow_html=True)
    if _missing_d:
        st.markdown("**구매 필요**")
        _missing_html = ""
        for _it in _missing_d:
            _a = html.escape(_it.get("amount", ""))
            _n = html.escape(_it["name"])
            _missing_html += (
                f'<div class="complete-line">'
                f'<span class="cl-ic" style="background:var(--amber-soft);color:var(--amber-ink)">구매</span>'
                f'<span class="nm">{_n}</span>'
                f'{f"<span class=\"chr\">{_a}</span>" if _a else ""}'
                f'</div>'
            )
        st.markdown(_missing_html, unsafe_allow_html=True)

    st.divider()
    _dc1, _dc2 = st.columns(2)
    with _dc1:
        if st.button("취소", use_container_width=True, key="dlg_cancel"):
            st.session_state["kitchen_complete_target"] = None
            st.session_state["kitchen_complete_result"] = None
            st.rerun()
    with _dc2:
        if st.button("완료 확정 · 재료 차감", type="primary", use_container_width=True, key="dlg_confirm"):
            save_source_data_to_md(comp["updated_source"])
            save_to_completed(comp_target, comp)
            _used_cnt = len(_used_d)
            st.session_state["kitchen_complete_success_msg"] = f"✅ 완료! 재료 {_used_cnt}개가 업데이트됐어요."
            st.session_state["kitchen_complete_target"] = None
            st.session_state["kitchen_complete_result"] = None
            st.session_state["kitchen_source_edit_mode"] = False
            st.session_state["kitchen_source_edit_data"] = None
            _new_src = parse_source_md_to_data()
            st.session_state["kitchen_ingredients"] = list(_new_src["ingredients"])
            st.rerun()


def render_kitchen_view():
    from datetime import date

    # 완료 처리 성공 메시지 (rerun 후 1회 표시)
    _success_msg = st.session_state.pop("kitchen_complete_success_msg", None)
    if _success_msg:
        st.success(_success_msg)

    # 완료 처리 dialog 열기 — one-shot 플래그로 다른 버튼 클릭 시 재오픈 방지
    if st.session_state.get("kitchen_complete_dialog_open"):
        st.session_state["kitchen_complete_dialog_open"] = False
        _show_completion_dialog()

    # topbar
    import datetime as _dt
    _src_path = SOURCE_MD_PATH
    if os.path.exists(_src_path):
        _updated = _dt.datetime.fromtimestamp(os.path.getmtime(_src_path)).strftime("%Y-%m-%d")
    else:
        _updated = date.today().isoformat()
    st.markdown(
        f"""
    <div class="topbar">
      <button class="sidebar-toggle" data-sidebar-toggle="1">{icon("menu", 20)}</button>
      <div class="k-panel-ic" style="width:34px;height:34px;background:var(--blue-soft);color:var(--blue);border-radius:10px;display:grid;place-items:center">{icon("fridge", 18)}</div>
      <div>
        <div class="topbar-title">내 주방</div>
        <div class="topbar-sub">보유 재료와 취향을 관리하면 레시피가 더 정확해져요</div>
      </div>
      <div class="topbar-spacer"></div>
      <div style="font-size:12.5px;color:var(--ink-3);font-weight:600">최종 갱신 <span class="mono">{_updated}</span></div>
    </div>
    """,
        unsafe_allow_html=True,
    )

    _cart_items = list_cart_items()

    # ── 완료 처리 대기 패널 (이슈 #50, #51): 전체 패널을 HTML로 렌더 + hidden 버튼 JS 연결 ──
    if _cart_items:
        # 1. 레시피 행 HTML 조립
        _rows_html = ""
        for ci in _cart_items:
            _safe_key = _tile_key(ci["path"])
            _ci_name = html.escape(str(ci.get("dish_name") or "레시피"))
            _ci_ings = len((ci.get("data") or {}).get("ingredients") or [])
            _ci_time = html.escape(str((ci.get("data") or {}).get("cooking_time") or "-"))
            _rows_html += f"""
            <div class="r-listrow" data-view="{_safe_key}" style="cursor:pointer">
              <div class="r-listrow-thumb ph" style="background:#ECF1FF;font-size:22px">🍽️</div>
              <div style="min-width:0;flex:1">
                <div class="r-listrow-name">{_ci_name}</div>
                <div class="r-listrow-intro">재료 {_ci_ings}개 · {_ci_time}</div>
              </div>
              <button class="btn btn-green btn-sm" data-complete="{_safe_key}"
                      onclick="event.stopPropagation()">
                {icon("check", 15)} 완료 처리
              </button>
            </div>"""

        _panel_html = f"""
        <div class="k-panel fade-up" style="margin-top:24px;margin-bottom:0">
          <div class="k-panel-head">
            <div class="k-panel-ic" style="background:var(--amber-soft);color:var(--amber-ink)">{icon("cart", 19)}</div>
            <div style="flex:1">
              <div class="k-panel-title">완료 처리 대기</div>
              <div class="k-panel-sub">요리를 마친 레시피를 완료하면 사용한 재료가 자동 차감돼요</div>
            </div>
            <span class="tag tag-amber">{len(_cart_items)}개</span>
          </div>
          <div class="k-panel-body" style="padding-top:14px">
            <div class="recipe-list">{_rows_html}</div>
          </div>
        </div>"""

        # 2. HTML 렌더링
        st.markdown(_panel_html, unsafe_allow_html=True)

        # 3. hidden st.button (레시피 보기 + 완료 처리) — CSS로 숨김, JS로 트리거
        for ci in _cart_items:
            _safe_key = _tile_key(ci["path"])
            if st.button("_view", key=f"view_recipe_{_safe_key}", use_container_width=False):
                st.session_state["view"] = "chat"
                st.session_state["load_recipe_pending"] = ci["data"]
                st.session_state["viewing_recipe"] = ci["path"]
                st.session_state["cart_selected_path"] = ci["path"]
                st.rerun()
            if st.button("_complete", key=f"kitchen_complete_{_safe_key}", use_container_width=False):
                st.session_state["kitchen_complete_target"] = ci["path"]
                _sd = parse_source_md_to_data()
                _res = calculate_source_update(ci["data"], _sd)
                st.session_state["kitchen_complete_result"] = _res
                st.session_state["kitchen_source_edit_data"] = _sd
                st.session_state["kitchen_source_edit_mode"] = False
                st.session_state["kitchen_complete_dialog_open"] = True
                st.rerun()

    # ── 보유 재료 패널 (Bug #26 수정) ──
    _ings = st.session_state.get("kitchen_ingredients") or []
    _ing_count = len(_ings)

    # 재료 칩 HTML 구성 — × 버튼은 data-del → components.html JS → hidden st.button del_ing_{i}
    # Bug #26: amount 문자열을 그대로 표시. 수량/단위 분해 금지.
    if _ings:
        _chips_html = ""
        for i, ing in enumerate(_ings):
            _name = html.escape(str(ing.get("name", "")))
            _amount = html.escape(str(ing.get("amount", "")))
            _amt_badge = f'<span class="amt mono">{_amount}</span>' if _amount else ""
            _chips_html += (
                f'<div class="k-ing">'
                f'<span>{_name}</span>'
                f'{_amt_badge}'
                f'<button class="x" data-del="{i}" style="cursor:pointer">{icon("x", 13)}</button>'
                f'</div>'
            )
        _chips_block = f'<div class="ing-chips">{_chips_html}</div>'
    else:
        _chips_block = '<div style="font-size:13px;color:var(--ink-3)">아직 등록된 재료가 없어요.</div>'

    # ── 보유 재료 패널: st.container로 header+chips+input 하나의 카드로 ──
    pass  # 패널 간격은 CSS margin-top으로 처리
    with st.container(border=True):
        st.markdown(
            f"""
        <div class="k-panel-head" style="padding:18px 22px 14px">
          <div class="k-panel-ic" style="background:var(--green-soft);color:var(--green-ink)">{icon("leaf", 19)}</div>
          <div style="flex:1">
            <div class="k-panel-title">보유 재료</div>
            <div class="k-panel-sub">재료명(양) 형식으로 적어주세요 · 양은 g, ml로 통일</div>
          </div>
          <span class="tag tag-green">{_ing_count}개</span>
        </div>
        <div style="height:1px;background:var(--line-2);margin:0 -22px"></div>
        <div style="padding:18px 22px 22px">{_chips_block}</div>
        """,
            unsafe_allow_html=True,
        )
        with st.form(key="ing_form", border=False, clear_on_submit=True):
            _ic1, _ic2 = st.columns([5, 1])
            with _ic1:
                _new_ing = st.text_input(
                    "새 재료", key="new_ing_input",
                    placeholder="예: 당근(2개)", label_visibility="collapsed",
                )
            with _ic2:
                _ing_submitted = st.form_submit_button("+ 추가", key="add_ing_btn", use_container_width=True)
            if _ing_submitted and _new_ing.strip():
                _parsed = _parse_ingredient_string(_new_ing.strip())
                st.session_state["kitchen_ingredients"].append(_parsed)
                _save_kitchen_to_source()
                st.rerun()

    # 숨겨진 재료 삭제 트리거 버튼들
    for i in range(len(_ings)):
        if st.button("_", key=f"del_ing_{i}", use_container_width=True):
            st.session_state["kitchen_ingredients"].pop(i)
            _save_kitchen_to_source()
            st.rerun()

    # ── 사용자 취향 패널: st.container로 header+pref list+add 버튼 하나의 카드로 ──
    _prefs = st.session_state.get("kitchen_preferences") or []
    _pref_count = len(_prefs)
    _adding_pref = st.session_state.get("adding_pref_mode", False)

    if _prefs:
        _pref_rows = ""
        for i, pref in enumerate(_prefs):
            _ptx = html.escape(str(pref))
            _pref_rows += (
                f'<div class="pref-row">'
                f'<span class="pref-ic" style="background:var(--blue-soft);color:var(--blue-ink)">{icon("heart", 14)}</span>'
                f'<span class="pref-tx">{_ptx}</span>'
                f'<button class="x" data-del-pref="{i}" style="cursor:pointer">{icon("x", 15)}</button>'
                f'</div>'
            )
        _pref_block = f'<div class="pref-list">{_pref_rows}</div>'
    else:
        _pref_block = '<div style="font-size:13px;color:var(--ink-3);padding:4px 0">아직 등록된 취향이 없어요.</div>'

    pass  # 패널 간격은 CSS margin-top으로 처리
    with st.container(border=True):
        st.markdown(
            f"""
        <div class="k-panel-head" style="padding:18px 22px 14px">
          <div class="k-panel-ic" style="background:var(--blue-soft);color:var(--blue-ink)">{icon("heart", 19)}</div>
          <div style="flex:1">
            <div class="k-panel-title">사용자 취향</div>
            <div class="k-panel-sub">알러지·식단·선호 맛 — 모든 레시피에 자동 반영돼요</div>
          </div>
          <span class="tag tag-blue">{_pref_count}개</span>
        </div>
        <div style="height:1px;background:var(--line-2);margin:0 -22px"></div>
        <div style="padding:18px 22px 22px">{_pref_block}</div>
        """,
            unsafe_allow_html=True,
        )
        if not _adding_pref:
            if st.button("+ 취향 추가", key="add_pref_btn"):
                st.session_state["adding_pref_mode"] = True
                st.rerun()
        else:
            with st.form(key="pref_form", border=False, clear_on_submit=True):
                _pc1, _pc2 = st.columns([5, 1])
                with _pc1:
                    _new_pref = st.text_input(
                        "새 취향", key="new_pref_input",
                        placeholder="예: 매운 음식 선호, 토마토 알러지",
                        label_visibility="collapsed",
                    )
                with _pc2:
                    _pref_submitted = st.form_submit_button("+ 추가", use_container_width=True, key="confirm_add_pref")
                if _pref_submitted:
                    if _new_pref.strip():
                        st.session_state["kitchen_preferences"].append(_new_pref.strip())
                        _save_kitchen_to_source()
                    st.session_state["adding_pref_mode"] = False
                    st.rerun()

    # 숨겨진 취향 삭제 트리거 버튼들
    for i in range(len(_prefs)):
        if st.button("_", key=f"del_pref_{i}", use_container_width=True):
            st.session_state["kitchen_preferences"].pop(i)
            _save_kitchen_to_source()
            st.rerun()

    # ── 패턴 B: 완료처리/재료삭제/취향삭제 버튼 → hidden st.button 연결 ──
    components.html(
        """
<script>
(function() {
  var doc = window.parent.document;

  // 모든 kitchen 클릭을 event delegation으로 처리 — rerun 후 timing 문제 방지
  if (doc.__kitchenDelListener) {
    doc.body.removeEventListener('click', doc.__kitchenDelListener);
  }
  doc.__kitchenDelListener = function(e) {
    // 재료 삭제
    var delBtn = e.target.closest('[data-del]');
    if (delBtn) {
      e.stopPropagation();
      var i = delBtn.getAttribute('data-del');
      var hidden = doc.querySelector('.st-key-del_ing_' + i + ' button');
      if (hidden) hidden.click();
      return;
    }
    // 취향 삭제
    var delPrefBtn = e.target.closest('[data-del-pref]');
    if (delPrefBtn) {
      e.stopPropagation();
      var i = delPrefBtn.getAttribute('data-del-pref');
      var hidden = doc.querySelector('.st-key-del_pref_' + i + ' button');
      if (hidden) hidden.click();
      return;
    }
    // 완료 처리 버튼
    var completeBtn = e.target.closest('[data-complete]');
    if (completeBtn) {
      e.stopPropagation();
      var key = completeBtn.getAttribute('data-complete');
      var hidden = doc.querySelector('.st-key-kitchen_complete_' + key + ' button');
      if (hidden) hidden.click();
      return;
    }
    // 레시피 행 클릭 (레시피 보기) — complete 버튼 클릭은 제외
    var viewRow = e.target.closest('[data-view]');
    if (viewRow) {
      var key = viewRow.getAttribute('data-view');
      var hidden = doc.querySelector('.st-key-view_recipe_' + key + ' button');
      if (hidden) hidden.click();
    }
  };
  doc.body.addEventListener('click', doc.__kitchenDelListener);

  function attach() {
    // body class 마커 (kitchen-mode 격리)
    doc.body.classList.add('kitchen-mode');
    // 사이드바 토글 버튼
    doc.querySelectorAll('[data-sidebar-toggle]').forEach(function(btn) {
      if (btn.dataset.bound) return;
      btn.dataset.bound = '1';
      btn.addEventListener('click', function(e) {
        e.stopPropagation();
        var sb = doc.querySelector('[data-testid="stSidebar"]');
        if (!sb) return;
        var sbBtn = sb.querySelector('button');
        if (sbBtn) sbBtn.click();
      });
    });
  }
  setTimeout(attach, 80);
})();
</script>
""",
        height=0,
    )


# ═══════════════════════════════════════════════════════════════════════════
# 채팅 뷰 (기존 Step 1~4 플로우)
# ═══════════════════════════════════════════════════════════════════════════
def render_chat_view():
    # body class 마커 제거 + 사이드바 토글 버튼 JS
    components.html(
        """<script>(function(){var d=window.parent.document;
        function r(){
          d.body.classList.remove('kitchen-mode');
          d.querySelectorAll('[data-sidebar-toggle]').forEach(function(btn){
            if(btn.dataset.bound)return;btn.dataset.bound='1';
            btn.addEventListener('click',function(e){
              e.stopPropagation();
              var sb=d.querySelector('[data-testid="stSidebar"]');
              if(!sb)return;var sbBtn=sb.querySelector('button');if(sbBtn)sbBtn.click();
            });
          });
        }
        setTimeout(r,40);setTimeout(r,300);})();</script>""",
        height=0,
    )
    # ============================================================
    # DEV MODE — 프로덕션 배포 전 이 블록 전체 삭제
    # ============================================================
    if st.session_state.get("_dev_mode"):
        _DEV_DUMMY_VISION = {
            "is_food": True,
            "dish_name": "카놀리",
            "ingredients": ["튀긴 페이스트리 셸", "리코타 치즈", "설탕", "초콜릿 칩", "바닐라 에센스"],
            "characteristics": "이탈리아 전통 디저트. 바삭한 셸 안에 크리미한 리코타 필링."
        }
        _DEV_DUMMY_RECIPE = {
            "dish_name": "카놀리",
            "servings": 2,
            "cooking_time": "30분",
            "difficulty": "medium",
            "ingredients": [
                "튀긴 페이스트리 셸(4개)",
                "리코타 치즈(200g)",
                "설탕(3큰술)",
                "초콜릿 칩(2큰술)",
                "바닐라 에센스(1작은술)",
            ],
            "steps": [
                "리코타 치즈를 고운 체에 걸러 물기를 뺀다.",
                "설탕, 바닐라 에센스를 넣고 부드럽게 섞는다.",
                "초콜릿 칩의 절반을 필링에 섞는다.",
                "페이스트리 셸에 필링을 짤주머니로 채운다.",
                "남은 초콜릿 칩으로 양 끝을 장식하고 슈거파우더를 뿌린다.",
            ],
            "tags": ["이탈리아", "디저트", "간식", "베이킹"]
        }

        def _dev_reset():
            for k in ["step","vision_result","servings","extra_requests","recipe_result",
                      "chat_history","recipe_confirmed","awaiting_revision","reanalyze_pending",
                      "image_bytes","mime_type","_typing","_pending_recipe","_last_file_name"]:
                st.session_state[k] = SESSION_DEFAULTS.get(k)
            st.session_state["chat_history"] = []

        # 플로팅 패널 CSS — 채팅 레이아웃 밖(position:fixed)에 렌더링
        # DEV MODE CSS — style.css의 DEV MODE 섹션에서 로드됨

        with st.container(key="_dev_panel"):
            _is_open = st.session_state.get("_dev_mode_open", False)

            if not _is_open:
                # 접힘 상태 — 작은 🛠 버튼만
                if st.button("🛠", key="_dev_open"):
                    st.session_state["_dev_mode_open"] = True
                    st.rerun()
            else:
                # 펼침 상태 — 닫기 + 7개 점프 버튼 (2열 배치)
                if st.button("✕ DEV MODE", key="_dev_close"):
                    st.session_state["_dev_mode_open"] = False
                    st.rerun()
                _dev_r1 = st.columns(2)
                _dev_r2 = st.columns(2)
                _dev_r3 = st.columns(2)
                _dev_r4 = st.columns(1)
                _dev_c = [_dev_r1[0], _dev_r1[1], _dev_r2[0], _dev_r2[1],
                          _dev_r3[0], _dev_r3[1], _dev_r4[0]]

                with _dev_c[0]:
                    if st.button("① 초기화", key="_dev_step1"):
                        _dev_reset()
                        st.session_state["view"] = "chat"
                        st.rerun()
                with _dev_c[1]:
                    if st.button("② 인분선택", key="_dev_step2"):
                        _dev_reset()
                        st.session_state["view"] = "chat"
                        st.session_state["vision_result"] = _DEV_DUMMY_VISION
                        st.session_state["step"] = 2
                        add_ai_message(
                            f'<b>카놀리</b>(으)로 분석했어요! 🎉<br>'
                            f'<span class="sub">보이는 재료 — 튀긴 페이스트리 셸, 리코타 치즈, 설탕, 초콜릿 칩</span>'
                        )
                        add_ai_message("몇 인분으로 만들까요? <span class='sub'>(기본 2인분)</span>")
                        st.rerun()
                with _dev_c[2]:
                    if st.button("③ 추가요청", key="_dev_step3"):
                        _dev_reset()
                        st.session_state["view"] = "chat"
                        st.session_state["vision_result"] = _DEV_DUMMY_VISION
                        st.session_state["servings"] = 2
                        st.session_state["step"] = 3
                        add_ai_message(
                            f'<b>카놀리</b>(으)로 분석했어요! 🎉<br>'
                            f'<span class="sub">보이는 재료 — 튀긴 페이스트리 셸, 리코타 치즈, 설탕, 초콜릿 칩</span>'
                        )
                        add_ai_message("몇 인분으로 만들까요? <span class='sub'>(기본 2인분)</span>")
                        add_user_message("2인분")
                        add_ai_message("2인분으로 준비할게요 👍")
                        add_ai_message("더 반영할 요청이 있나요? <span class='sub'>(없으면 건너뛰어도 돼요)</span>")
                        st.rerun()
                with _dev_c[3]:
                    if st.button("④ 레시피카드", key="_dev_step4"):
                        _dev_reset()
                        st.session_state["view"] = "chat"
                        st.session_state["vision_result"] = _DEV_DUMMY_VISION
                        st.session_state["servings"] = 2
                        st.session_state["extra_requests"] = ""
                        st.session_state["recipe_result"] = _DEV_DUMMY_RECIPE
                        st.session_state["step"] = 4
                        add_ai_message(
                            f'<b>카놀리</b>(으)로 분석했어요! 🎉<br>'
                            f'<span class="sub">보이는 재료 — 튀긴 페이스트리 셸, 리코타 치즈, 설탕, 초콜릿 칩</span>'
                        )
                        add_ai_message("몇 인분으로 만들까요? <span class='sub'>(기본 2인분)</span>")
                        add_user_message("2인분")
                        add_ai_message("2인분으로 준비할게요 👍")
                        add_user_message("추가 요청 없음")
                        add_ai_message("레시피가 완성됐어요! 아래에서 확인해 보세요 ✨")
                        add_ai_message(build_recipe_card_html(_DEV_DUMMY_RECIPE))
                        st.rerun()
                with _dev_c[4]:
                    if st.button("⑤ 비음식에러", key="_dev_nonfood"):
                        _dev_reset()
                        st.session_state["view"] = "chat"
                        add_user_message("음식 사진을 올렸어요 📷")
                        add_ai_message("음식 사진이 아닌 것 같아요 🤔<br><span class='sub'>음식 사진이 아닙니다. 일반 물체 사진입니다.</span>")
                        st.rerun()
                with _dev_c[5]:
                    if st.button("⑥ API에러", key="_dev_apierror"):
                        _dev_reset()
                        st.session_state["view"] = "chat"
                        add_user_message("음식 사진을 올렸어요 📷")
                        add_ai_message("⚠️ API 오류가 발생했어요.<br><span class='sub'>연결 시간이 초과됐어요. 잠시 후 다시 시도해 주세요.</span>")
                        st.rerun()
                with _dev_c[6]:
                    if st.button("⑦ 레시피확정", key="_dev_confirmed"):
                        _dev_reset()
                        st.session_state["view"] = "chat"
                        st.session_state["vision_result"] = _DEV_DUMMY_VISION
                        st.session_state["servings"] = 2
                        st.session_state["recipe_result"] = _DEV_DUMMY_RECIPE
                        st.session_state["recipe_confirmed"] = True
                        st.session_state["step"] = 4
                        add_ai_message("레시피가 완성됐어요! 아래에서 확인해 보세요 ✨")
                        add_ai_message(build_recipe_card_html(_DEV_DUMMY_RECIPE))
                        add_user_message("레시피 확정!")
                        add_ai_message("레시피를 저장했어요 🎉 장바구니에서 확인해 보세요.")
                        st.rerun()
    # ============================================================
    # DEV MODE END
    # ============================================================

    # ── 저장 레시피 채팅 로드 처리 (1단계: 로딩 화면 트리거) — topbar보다 먼저 ──
    _pending_recipe = st.session_state.get("load_recipe_pending")
    if _pending_recipe is not None:
        st.session_state["load_recipe_pending"] = None
        st.session_state["_recipe_loading"] = True
        st.session_state["_recipe_loading_data"] = _pending_recipe
        st.rerun()

    # ── 저장 레시피 채팅 로드 처리 (2단계: 로딩 화면만 표시 후 데이터 구성) — topbar보다 먼저 ──
    if st.session_state.get("_recipe_loading"):
        _loading_data = st.session_state.get("_recipe_loading_data")
        st.session_state["_recipe_loading"] = False
        st.session_state["_recipe_loading_data"] = None
        st.markdown(
            """<div style="display:flex;flex-direction:column;align-items:center;
            justify-content:center;height:100vh;gap:16px">
            <div class="typing"><i></i><i></i><i></i></div>
            <div style="color:var(--ink-3);font-size:14px;font-weight:500">레시피를 불러오는 중...</div>
            </div>""",
            unsafe_allow_html=True,
        )
        st.session_state["chat_history"] = []
        st.session_state["step"] = 4
        st.session_state["recipe_result"] = _loading_data
        st.session_state["recipe_confirmed"] = True
        st.session_state["awaiting_revision"] = False
        st.session_state["reanalyze_pending"] = False
        st.session_state["image_bytes"] = None
        st.session_state["mime_type"] = None
        st.session_state["vision_result"] = None
        add_ai_message("📂 저장된 레시피를 불러왔어요.")
        add_ai_message(build_recipe_card_html(_loading_data))
        _saved_missing = _loading_data.get("missing_ingredients", [])
        if _saved_missing:
            with st.spinner("네이버 쇼핑 검색 중..."):
                _shop_html = build_shopping_html(_saved_missing)
            if _shop_html:
                add_ai_message(_shop_html)
        st.rerun()
        return  # 안전 장치: rerun 후 아래 렌더링 차단

    # topbar
    st.markdown(
        f"""
    <div class="topbar">
      <button class="sidebar-toggle" data-sidebar-toggle="1">{icon("menu", 20)}</button>
      <div class="msg-ai-avatar" style="width:34px;height:34px;border-radius:10px;margin-top:0">{icon("chef", 17)}</div>
      <div>
        <div class="topbar-title">쿸</div>
        <div class="topbar-sub">음식 사진 → 맞춤 레시피</div>
      </div>
      <div class="topbar-spacer"></div>
    </div>
    """,
        unsafe_allow_html=True,
    )

    # ── 웰컴 메시지 (최초 1회) ──
    if not st.session_state["chat_history"]:
        add_ai_message(
            "안녕하세요! 음식 사진을 올려주시면 재료를 분석해 맞춤 레시피를 만들어 드려요. 📸"
        )

    # ── 재분석 처리 (step 분기보다 먼저) ──
    if st.session_state.get("reanalyze_pending"):
        st.session_state["reanalyze_pending"] = False
        get_ai_client()
        with st.spinner("이미지를 다시 분석하고 있어요..."):
            try:
                vision_result, raw = analyze_food_image(
                    st.session_state["image_bytes"],
                    st.session_state["mime_type"],
                )
            except Exception as e:
                handle_api_error(e)
        if vision_result is None:
            add_ai_message("분석 결과를 이해하지 못했어요. 다시 시도해 주세요.")
            st.session_state["vision_result"] = vision_result
        else:
            st.session_state["vision_result"] = vision_result
            add_ai_message("몇 인분으로 만들까요? <span class='sub'>(기본 2인분)</span>")
            st.session_state["step"] = 2
            st.rerun()

    # ── STEP 1: 이미지 업로드 ──
    if st.session_state["step"] == 1:
        render_chat_history()

        # file_uploader는 항상 렌더 (CSS로 기본 UI 숨김)
        # label_visibility="collapsed"로 라벨도 숨김
        uploaded_file = st.file_uploader(
            "음식 사진 업로드",
            type=["jpg", "jpeg", "png", "webp"],
            accept_multiple_files=False,
            label_visibility="collapsed",
        )

        if uploaded_file is None:
            # 파일 미선택: 업로드 존 표시 + JS 클릭 트리거
            st.markdown(
                f"""
            <div class="upload-zone" id="upload-zone-custom">
              <div class="upload-ic">{icon("upload", 24)}</div>
              <h4>음식 사진 올리기</h4>
              <p>클릭해서 사진을 선택하세요 · JPG·PNG·WEBP · 4MB 이하</p>
            </div>""",
                unsafe_allow_html=True,
            )
            components.html(
                """<script>
                (function(){
                  var doc=window.parent.document;
                  var zone=doc.getElementById('upload-zone-custom');
                  if(!zone||zone.dataset.bound==='1')return;
                  zone.dataset.bound='1';zone.style.cursor='pointer';
                  zone.addEventListener('click',function(){
                    var inp=doc.querySelector('[data-testid="stFileUploaderDropzone"] input[type="file"]')
                           ||doc.querySelector('[data-testid="stFileUploader"] input[type="file"]');
                    if(inp)inp.click();
                  });
                })();
                </script>""",
                height=0,
            )
        else:
            # 파일 선택됨
            image_bytes = uploaded_file.getvalue()
            mime_type = uploaded_file.type or mimetypes.guess_type(uploaded_file.name)[0] or "image/jpeg"
            _cur_name = uploaded_file.name

            # 2단계 A: 새 파일 감지 → typing 표시 후 rerun
            if st.session_state.get("_last_file_name") != _cur_name and not st.session_state.get("_pending_analyze"):
                if len(image_bytes) > 4 * 1024 * 1024:
                    st.error("이미지가 너무 커요. 4MB 이하 이미지를 사용해주세요.")
                    st.stop()
                st.session_state["image_bytes"] = image_bytes
                st.session_state["mime_type"] = mime_type
                st.session_state["_last_file_name"] = _cur_name
                st.session_state["_typing"] = True
                st.session_state["_pending_analyze"] = True
                st.rerun()

            # 2단계 B: 분석 대기 중 → API 호출
            if st.session_state.get("_pending_analyze"):
                st.session_state["_pending_analyze"] = False
                _ib = st.session_state.get("image_bytes") or image_bytes
                _mt = st.session_state.get("mime_type") or mime_type
                get_ai_client()
                try:
                    vision_result, raw = analyze_food_image(_ib, _mt)
                except Exception as e:
                    st.session_state["_typing"] = False
                    handle_api_error(e)
                st.session_state["_typing"] = False
                if vision_result is None:
                    add_ai_message("분석 결과를 이해하지 못했어요. 다시 시도해 주세요.")
                    st.session_state["_last_file_name"] = None
                else:
                    is_food = vision_result.get("is_food", True)
                    if not is_food:
                        reason = vision_result.get("non_food_reason", "음식이 아닌 것 같아요.")
                        add_ai_message(f"🤔 {reason}")
                        st.session_state["image_bytes"] = None
                        st.session_state["mime_type"] = None
                        st.session_state["vision_result"] = None
                        st.session_state["_last_file_name"] = None
                    else:
                        dish = vision_result.get("dish_name", "")
                        ings = vision_result.get("ingredients", [])
                        ing_text = ", ".join(str(i) for i in ings) if ings else "재료 정보 없음"
                        add_user_message("음식 사진을 올렸어요 📷")
                        add_ai_message(
                            f"<b>{html.escape(str(dish))}</b>(으)로 분석했어요! 🎉<br>"
                            f'<span class="sub">보이는 재료 — {html.escape(ing_text)}</span>'
                        )
                        add_ai_message("몇 인분으로 만들까요? <span class='sub'>(기본 2인분)</span>")
                        st.session_state["vision_result"] = vision_result
                        st.session_state["step"] = 2
                st.rerun()

    # ── STEP 2: 인분 입력 (패턴 A: pure st.button + CSS) ──
    if st.session_state["step"] == 2:
        render_chat_history()
        # quick-reply 칩 — 채팅 좌측 정렬 (container key로 CSS 스코핑)
        with st.container(key="step2_chips"):
            _cols2 = st.columns(5, gap="small")
            for _i, (label, val) in enumerate([("1인분", 1), ("2인분", 2), ("4인분", 4), ("6인분", 6)]):
                with _cols2[_i]:
                    if st.button(label, key=f"serv_{val}"):
                        add_user_message(label)
                        st.session_state["servings"] = val
                        add_ai_message(f"{val}인분으로 준비할게요 👍")
                        add_ai_message("더 반영할 요청이 있나요? <span class='sub'>(없으면 건너뛰어도 돼요)</span>")
                        st.session_state["step"] = 3
                        st.rerun()
            with _cols2[4]:
                if st.button("건너뛰기 →", key="serv_skip"):
                    add_user_message("인분 수는 상관없어요")
                    st.session_state["servings"] = None
                    add_ai_message("기본 2인분으로 준비할게요 👍")
                    add_ai_message("더 반영할 요청이 있나요? <span class='sub'>(없으면 건너뛰어도 돼요)</span>")
                    st.session_state["step"] = 3
                    st.rerun()

        # 직접 입력은 하단 st.chat_input()이 처리

    # ── STEP 3: 추가 요청 ──
    if st.session_state["step"] == 3:
        render_chat_history()
        # quick-reply 칩 — 채팅 좌측 정렬 (container key로 CSS 스코핑)
        with st.container(key="step3_chips"):
            quick_prefs = ["채식으로 바꿔줘", "안 맵게", "10분 이내", "설탕 줄여줘"]
            p_cols = st.columns(5, gap="small")
            for i, label in enumerate(quick_prefs):
                with p_cols[i]:
                    if st.button(label, key=f"pref_{i}"):
                        add_user_message(label)
                        st.session_state["extra_requests"] = label
                        st.session_state["step"] = 4
                        st.rerun()
            with p_cols[-1]:
                if st.button("건너뛰기 →", key="pref_skip"):
                    add_user_message("추가 요청 없음")
                    st.session_state["extra_requests"] = ""
                    st.session_state["step"] = 4
                    st.rerun()

        # 직접 입력은 하단 st.chat_input()이 처리

    # ── STEP 4: 레시피 생성 및 표시 ──
    if st.session_state["step"] == 4:
        render_chat_history()

        # 레시피가 아직 생성되지 않았으면 생성
        if st.session_state["recipe_result"] is None:
            # 2단계 A: typing 표시 후 rerun
            if not st.session_state.get("_pending_recipe"):
                st.session_state["_typing"] = True
                st.session_state["_pending_recipe"] = True
                st.rerun()

            # 2단계 B: 실제 생성
            st.session_state["_pending_recipe"] = False
            get_ai_client()
            try:
                recipe_result, raw = generate_recipe(
                    st.session_state["vision_result"],
                    st.session_state["servings"],
                    st.session_state["extra_requests"],
                )
            except Exception as e:
                st.session_state["_typing"] = False
                handle_api_error(e)
            st.session_state["_typing"] = False
            if recipe_result is None:
                add_ai_message("레시피 생성에 실패했어요. 다시 시도해 주세요.")
            else:
                st.session_state["recipe_result"] = recipe_result
                add_ai_message("레시피가 완성됐어요! 아래에서 확인해 보세요 ✨")
                add_ai_message(build_recipe_card_html(recipe_result))
                extra = st.session_state.get("extra_requests")
                if extra:
                    get_ai_client()
                    extract_and_save_feedback(extra)
            st.rerun()

        # 확정/수정/재분석 버튼
        if st.session_state["recipe_result"] is not None and not st.session_state["recipe_confirmed"]:
            if st.session_state.get("awaiting_revision"):
                # 수정 내용은 하단 st.chat_input()이 처리
                if st.button("취소", key="cancel_revision", use_container_width=True):
                    st.session_state["awaiting_revision"] = False
                    st.rerun()
            else:
                bc1, bc2, bc3 = st.columns(3)
                with bc1:
                    _confirm_clk = st.button(
                        "✓ 레시피 확정", key="confirm_recipe", type="primary", use_container_width=True
                    )
                with bc2:
                    _revise_clk = st.button("✎ 수정 요청", key="request_revision", use_container_width=True)
                with bc3:
                    _reanalyze_clk = st.button("↺ 이미지 재분석", key="reanalyze_btn", use_container_width=True)
                if _confirm_clk:
                    add_user_message("레시피 확정!")
                    st.session_state["recipe_confirmed"] = True
                    _confirmed = st.session_state["recipe_result"]
                    _cart_path = save_to_cart(_confirmed)
                    st.session_state["cart_selected_path"] = _cart_path
                    _cart_path_disp = html.escape(str(_cart_path))
                    add_ai_message(
                        f'{icon("cart", 15, color="var(--green)")} '
                        '레시피가 <b>장바구니</b>에 추가됐어요! 🛒<br>'
                        '<span class="sub">요리를 마치면 내 주방에서 완료 처리하고 재료를 자동 차감할 수 있어요.</span>'
                        f'<span class="save-path">{icon("book", 13)}'
                        f'<span class="mono">{_cart_path_disp}</span></span>'
                    )
                    # 네이버 쇼핑 — 확정 이후에만 표시
                    _missing_confirmed = _confirmed.get("missing_ingredients", [])
                    if not _missing_confirmed:
                        # source.md가 없는 경우에는 missing_ingredients 정보가 비어 있을 수 있으므로
                        # 레시피 재료 전체를 검색 대상으로 사용한다.
                        if not load_source_md():
                            _missing_confirmed = _confirmed.get("ingredients", [])

                    if _missing_confirmed:
                        with st.spinner("네이버 쇼핑 검색 중..."):
                            _shop_html_confirmed = build_shopping_html(_missing_confirmed)
                        if _shop_html_confirmed:
                            add_ai_message(_shop_html_confirmed)
                    st.rerun()
                if _revise_clk:
                    st.session_state["awaiting_revision"] = True
                    st.rerun()
                if _reanalyze_clk:
                    add_user_message("이미지 다시 분석해줘")
                    st.session_state["reanalyze_pending"] = True
                    st.rerun()

        # 확정 후 액션 (패턴 A: visible st.button)
        if st.session_state["recipe_confirmed"]:
            ac1, ac2 = st.columns(2)
            with ac1:
                _go_kitchen = st.button(
                    "내 주방에서 완료 처리", key="go_kitchen_btn", use_container_width=True
                )
            with ac2:
                _restart = st.button("처음부터 다시", key="restart_btn", use_container_width=True)
            if _go_kitchen:
                st.session_state["view"] = "kitchen"
                st.rerun()
            if _restart:
                reset_all()
                st.rerun()

    # ── 하단 고정 composer (st.chat_input) ──
    _step_now = st.session_state.get("step", 1)
    _revising_now = st.session_state.get("awaiting_revision", False)
    if _step_now == 1:
        _ph, _dis = "음식 사진을 먼저 올려주세요", True
    elif _step_now == 2:
        _ph, _dis = "인분 수를 입력하세요 (예: 3인분)", False
    elif _step_now == 3:
        _ph, _dis = "추가 요청사항을 입력하세요 (없으면 그냥 전송)", False
    elif _revising_now:
        _ph, _dis = "어떻게 수정할까요? (예: 더 매콤하게)", False
    else:
        _ph, _dis = "아래 버튼으로 확정하거나 수정하세요", True

    _chat_val = st.chat_input(_ph, disabled=_dis, key="main_chat_input")
    if _chat_val:
        if _step_now == 2:
            add_user_message(_chat_val)
            st.session_state["servings"] = parse_servings(_chat_val)
            _sv = st.session_state["servings"]
            _sv_label = f"{_sv}인분" if _sv else "기본 2인분"
            add_ai_message(f"{_sv_label}으로 준비할게요 👍")
            add_ai_message("더 반영할 요청이 있나요? <span class='sub'>(없으면 건너뛰어도 돼요)</span>")
            st.session_state["step"] = 3
            st.rerun()
        elif _step_now == 3:
            add_user_message(_chat_val)
            st.session_state["extra_requests"] = _chat_val
            st.session_state["step"] = 4
            st.rerun()
        elif _revising_now and _chat_val.strip():
            add_user_message(_chat_val)
            _prev_extra = (st.session_state.get("extra_requests") or "").strip()
            st.session_state["extra_requests"] = (
                f"{_prev_extra}\n{_chat_val.strip()}" if _prev_extra else _chat_val.strip()
            )
            st.session_state["recipe_result"] = None
            st.session_state["awaiting_revision"] = False
            st.rerun()

    scroll_to_bottom()


# ═══════════════════════════════════════════════════════════════════════════
# 뷰 라우팅
# ═══════════════════════════════════════════════════════════════════════════
render_sidebar()

_view = st.session_state.get("view", "home")
if _view == "home":
    render_home_view()
elif _view == "kitchen":
    render_kitchen_view()
else:  # "chat"
    render_chat_view()

