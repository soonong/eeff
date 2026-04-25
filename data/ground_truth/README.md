# Ground Truth 라벨링 디렉토리

## 디렉토리 구조

각 공고 1건 = 1 디렉토리. 디렉토리명: `{source_prefix}_{notice_id}`

```
data/ground_truth/
├── _template/           ← 신규 라벨링 시 복사해 사용
│   ├── source.txt       # 원문 (개인정보 redact 완료본)
│   ├── expected.json    # 49키 정답 (processed by human reviewer)
│   └── meta.yaml        # 메타 정보
├── g2b_20240625-12345/  ← 나라장터 공고 예시
│   ├── source.html
│   ├── expected.json
│   └── meta.yaml
└── README.md            ← 이 파일
```

## source_prefix 규칙

| prefix | 출처 |
|--------|------|
| `g2b_` | 나라장터(G2B) |
| `b2_` | bidding2.kr A2 API |
| `local_` | 로컬 파일 직접 추가 |

## 1건당 작업 흐름

1. `_template/` 복사 → `{prefix}_{notice_id}/`로 이름 변경
2. 원문 source 파일 추가
3. `python -m scripts.redact --in {dir}/source.* --inplace` 실행
4. `python -m scripts.label_assist {dir}/source.*` → expected.json 초안 자동 생성
5. expected.json diff 검수 (AI 초안 vs 정답)
6. meta.yaml의 `reviewed_at` 채우기

## expected.json 형식

```json
{
  "extracted": {
    "종목": [["포장공사업", "토목공사업"]],
    "투찰율": 0.87745,
    "기초금액": 3922300000,
    ...
  },
  "source": {
    "종목": "입찰참가자격: 지반조성·포장공사업 및 토목공사업",
    "투찰율": "낙찰하한율: 87.745%",
    ...
  }
}
```

null = 해당 항목 공고에 없음 (AI가 null 반환해야 정답)
