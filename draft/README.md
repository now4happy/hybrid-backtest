# 하이브리드 매매 백테스트 (hybrid-backtest)

백테스트 전용. 주문·서버 기능 없음. Python 표준 라이브러리만 사용.

## 실행
    python run_backtest.py run                          # 일봉 전체 기간, 설정 1개
    python run_backtest.py sweep                        # 일봉 전체 기간, 설정 비교
    python run_backtest.py compare --minute-dir 폴더    # 분봉 구간: 분봉 vs 일봉 근사
    python -m unittest test_hbt test_kiwoom_probe test_hbt_fix2   # 시험

- 설정 덮어쓰기: `--set tower_tp=15 protect_ratio=0.2`
- 잔여 F: 기준 `remain=hold`. 비교는 `--set remain=sniper` 로 따로 실행 (run·compare 결과 폴더가 나뉨)
- run·compare 는 보호 끔/현금 보호 수정안 × 탑 매도 두 식(`id_desc`, `avg_desc`)을 따로 냄 (`--set tower_order=` 는 여기서 무시)
- 자본: 기본 `--share-basis fixed` (몫을 늘리지 않고, 첫 봉 시가로 1주가 안 되면 실행 불가). `first_bar` 는 첫 봉 시가 기준으로 공개적으로 늘림
- 고갈(no_cash·cash_protection) 1회 이상은 순위에서 제외. `after_shares=add` 는 `protect_ratio=0` 과 함께만
- 설정 비교 범위: config_default.json 의 `sweep`
- 분봉 파일: kiwoom_minute_probe.py 출력 `{종목코드}_1m.csv`
- 장 시간이 다른 날: config 의 `minute.sessions` 에 `"YYYY-MM-DD": ["10:00","16:30"]`
- 중지 기간(사용자 판단): config `params.halts` = `[["2026-03-01","2026-03-31","stop_buy"]]` (`stop_all` 은 매도도 중지)
- 분봉은 `compare` 를 설정 여러 개로 돌리면 오래 걸림 (`--no-grid` 로 생략)

## 분봉 비교표의 열
- 분봉: 키움 1분봉
- 일봉 근사(분봉 합성): 같은 분봉을 일봉으로 합친 것 → 일봉으로 보는 것 자체의 오차
- 일봉 근사(yahoo 등): 외부 일봉 → 자료 출처 차이까지 포함
- 저가 먼저 / 고가 먼저: 하루 안 가격 순서를 몰라 생기는 폭

**분봉이 없는 구간은 일봉만으로 판단한다.**

## 기준 규칙
meritz_laoer v1.5.3 코드 본문 기준. 기준 `remain=hold`. 비교 대상: `sell_rule=fixed`(매수 건마다 +N%), `remain=sniper`, `l2_solo`(2층 단독 매도), `tower_order`, `protect_ratio`.

## 코드와 다르거나 가정한 것
- 미국장 시각을 한국장으로 옮김(가정, 확인 필요): 개장 쉴드 09:00~09:06, 마감 60분 전부터 신규 진입 금지, 10분 게이트는 09:00 기점
- 1분봉 필터의 지표는 날짜를 넘겨 이어서 계산 (코드의 조회 범위와 같은지 미확인)
- 봉 안 가격은 시가→저가·고가→종가 순서의 직선 경로로 가정
- 체결은 가격이 닿으면 전량 체결. 호가 잔량·미체결 60초 취소·호가 단위 미반영
- F 완성 허용 오차는 목표의 0.6667% ($100/$15,000 비율)
- 분봉 15:20(920분) 이후 봉은 체결 평가에서 뺌 (고정 시각: 장 시간이 다른 날은 따로 확인 필요)
- 현금 보호의 전량 중지는 초안의 부분 매수와 다른 새 규칙. 0.2 는 시험용 값
- 상하한가에 고정된 봉(고가=저가)과 거래량 0 봉은 체결 제한
- 비주식형 ETF 과세는 과표 대신 매매차익으로 근사, 배당·분배금 미반영
- 2027년 이후 거래세는 2026년 값
- 봇의 서브 그리드 등 하이브리드 설명 밖 기능은 미반영
