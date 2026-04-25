# AI 입찰 공고 분석 시스템 (v1.0)

한국 공공입찰 공고문(HTML/PDF)에서 49개 표준 항목을 자동 추출하여 구조화 JSON으로 변환하는 시스템.

- 기획서: [`docs/기획서_v1.0.md`](docs/기획서_v1.0.md)
- 추출 규칙(외부 관리): [`data/columns.csv`](data/columns.csv)

## 주요 특징

- **규칙은 코드 밖**: `data/columns.csv` 한 줄 추가/수정으로 추출 항목 확장 — Gemini 캐시는 SHA-256 해시로 자동 갱신
- **Context Caching**: 시스템 지침(규칙 + Few-shot)을 Gemini Cached Content로 저장하여 1,000건 호출 시 토큰 80%+ 절감
- **종목 AND/OR 논리 엔진**: `"A 및 B 또는 C"` → `[["A","B"], ["C"]]`, 주력분야 자동 추출, 법령 부기 자동 제거
- **후처리 검증**: 타입 강제, 산술 검증(순공사원가 = 재료비+노무비+경비), 범위/Enum/ISO datetime 검증, Source Grounding

## 설치

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
# .env 파일을 열어 GEMINI_API_KEY 입력
# (https://aistudio.google.com/apikey 에서 발급)
```

## 실행

```bash
uvicorn app.main:app --reload --port 8000
```

브라우저에서 http://localhost:8000 접속 → 공고문 파일 업로드 → 분석 결과 확인.

## API

### POST /analyze

`multipart/form-data` 또는 `application/x-www-form-urlencoded`:

| 필드 | 타입 | 설명 |
|---|---|---|
| `file` | file | HTML/PDF/TXT 공고문 (10MB 이내). `url`과 둘 중 하나 필수. |
| `url` | string | 공고문 URL. `file`과 둘 중 하나 필수. |

응답:

```json
{
  "file_name": "sample_g2b.html",
  "char_count": 1234,
  "extracted": {
    "종목": [["포장공사업", "토목공사업"]],
    "투찰율": 0.87745,
    "기초금액": 3922300000
  },
  "source": {
    "종목": "4. 입찰참가자격: 지반조성·포장공사업(주력분야: 포장공사업) 및 토목공사업"
  },
  "issues": [],
  "usage": {
    "prompt_token_count": 902,
    "cached_content_token_count": 4231,
    "candidates_token_count": 218,
    "total_token_count": 5351
  }
}
```

`cached_content_token_count` 가 0보다 크면 캐싱이 정상 작동 중입니다.

## 테스트

```bash
pytest -q
```

LLM을 호출하지 않는 결정론적 테스트만 포함 — CI에서 API 키 불필요.

## 프로젝트 구조

```
app/                 # 애플리케이션
  main.py            # FastAPI 엔트리
  routes.py          # GET /, POST /analyze
  preprocess.py      # HTML → Markdown
  rules.py           # Rule Dictionary 로더
  prompts.py         # 시스템 지침 합성
  gemini_client.py   # Gemini + Context Caching
  jongmok_parser.py  # 종목 AND/OR 논리 엔진
  validator.py       # 후처리 검증
  schemas.py         # Pydantic 모델
  storage.py         # SQLite 저장
data/
  columns.csv        # 49개 추출 규칙 (외부 관리)
  few_shot/          # 항목별 보조 예시
docs/
  기획서_v1.0.md     # 한국어 개발 기획서
samples/             # 테스트용 샘플 공고
templates/, static/  # 웹 UI
tests/               # pytest
```

## 라이선스

내부 사용 목적.
