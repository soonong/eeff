# 공고확인(T3) 추출 예시

`공고확인` 필드는 공고문 다운로드 URL 또는 식별 번호. 없으면 null.

## 표준

- 원문: `공고문 다운로드: https://www.g2b.go.kr/pt/menu/selectSubFrame.do?...&notiNo=20260425001`
- 출력: `"https://www.g2b.go.kr/pt/menu/selectSubFrame.do?...&notiNo=20260425001"`
- 규칙: URL 전체 또는 공고번호 식별자.

## 부재 시 null

- 공고문 URL·식별번호 없음
- 출력: `null`
