방: 하이브리드 백테스트

# VERIFY — 음수 CAGR 칼마 미산출 보류 수정 (negcalmar v1)

- 기준: 저장소 커밋 944be53 (HANDOFF.md 60·61번은 6af5bed에서 읽음. 944be53..6af5bed에서 draft/ 소스 변경 없음, __pycache__만 다름)
- 변경 파일: draft/run_backtest.py, draft/test_hbt_fix2.py (이 ZIP에는 이 두 파일과 VERIFY.md만 있음)
- 저장소 푸시 없음

## 수정
score(): `cal is None`을 먼저 검사해 CAGR 부호와 무관하게 None 반환. 그 뒤 양수/음수 분기. docstring도 맞춰 고침.
순위 키 (1, cal, cagr) / (0, cagr, -mdd)는 그대로.
verdict()는 안 고침: score()가 None이면 "판정 보류(칼마 미산출)"를 반환함을 코드와 시험으로 확인.

## 기존 시험 1줄 변경 (중요)
test_score_none_is_not_infinite의 둘째 단언은 옛 동작을 요구했다.
  assertIsNotNone(score(CAGR -5%, 칼마 없음))  →  assertIsNone(...)
따라서 "기존 84개 통과"는 이 단언 1줄을 새 결정에 맞게 바꾼 상태의 결과다.

## 추가 시험 3개
- 칼마 없음 × CAGR +5% / 0 / -5%: score None, 판정 "판정 보류(칼마 미산출)", 순위 목록에서 빠짐
- 음수 A(-10%, MDD 50%, 칼마 -0.20) / B(-8%, MDD 10%, 칼마 -0.80): B가 먼저
- 순위 [칼마 2.0] > [칼마 1.0] > [CAGR 0, 칼마 0.0] > [음수]

## 실행 (Python 3.13.18, 합성시험만)
명령: cd draft && python3 -m unittest test_hbt test_kiwoom_probe test_hbt_fix2
- 수정 전(944be53 그대로): 84/84 통과
- 수정 후: 87/87 통과 (38 + 7 + 42, 실패 0) = 기존 84(위 1줄 변경) + 신규 3
- 새 시험 파일을 수정 전 코드에 적용: 42개 중 실패 3건
  (칼마 없음 시험의 CAGR 0·음수 하위 경우 2건, 바꾼 단언 1건). 신규 순위 유지 시험 2개는 수정 전에도 통과.
- 944be53을 새 폴더에 풀고 이 ZIP을 덮어써 재실행: 87/87 통과

## 코드 안전 검토
- diff는 순위 판정 함수와 시험뿐. 주문·계좌·네트워크·서버 코드 변경 없음.
- score() 호출처(verdict, rank, rule_effects)를 확인: rank와 rule_effects는 판정이 "순위"인 행만 비교하므로 None끼리 비교되지 않음.
- 모든 설정이 보류면 rank()는 빈 순위를 반환(합성 확인). 보고서 코드에 빈 순위 분기가 있음(코드 읽기로 확인. 전부 칼마 보류인 보고서 생성 자체는 따로 실행 안 함).

## 영향
칼마는 MDD가 1e-9 이하일 때 None이다(hbt_engine.metrics()). 그래서 거래가 없어 CAGR 0·MDD 0인 설정도 이제 순위에서 빠진다.

## 미실행
실데이터 백테스트, 원본 Python 대조, 별도 에이전트 검토(수정 규모 1곳).
