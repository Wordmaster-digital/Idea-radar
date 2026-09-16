# Idea-radar

국내에서 **새로 나온 창업 아이템**을 매일 모아, 그 아이템을 한 단계 끌어올린 **보완 아이디어**와 함께 디스코드로 보내는 봇이다. GitHub Actions에서 매일 08:00(KST)에 돈다.

## 어떻게 고르나

판단 틀은 '그늘로' 앱의 개발 배경에서 가져왔다. 그림자 계산과 그늘 지도는 이미 있었고, 거기에 **출발 시각별 그림자라는 축**을 더해 "지금 어느 길로 갈지"를 정해 주자 시장이 열렸다.

- **3단 계단**: 1단 계산·수집(원천 데이터) → 2단 시각화·검색·목록(정보) → 3단 의사결정(행동). 기회는 대개 1·2단은 있는데 3단이 비었을 때 생긴다.
- **그늘로 6가지 체크**: 축 하나로 행동을 정해 주는가 / 구체적 불편 장면이 있는가 / 무료 공개 데이터로 2주 안에 MVP가 되는가 / 지금이어야 하는 이유가 있는가 / 돈 없이 첫 사용자에게 닿는 채널이 있는가 / 원래 타깃보다 더 절실한 사용자층이 있는가.
- 검증 원칙(지불자 명시, 데이터 실재 확인, 함정 판정)은 `idea_ladder_prompt.md`와 같다.

## 수집 소스

| 종류 | 내용 | 왜 |
|---|---|---|
| 구글 뉴스 검색 RSS | `앱 출시`, `서비스 론칭`, `앱 등장`, `앱 화제`, `미니앱 출시` (최근 24시간) | 개인·소규모 팀이 만든 앱은 "출시"가 아니라 "등장·화제"로 보도된다. 2026년 8월 그늘로 기간을 확인해 보면 `앱 출시` 55건 중 관련 기사가 0건, `앱 등장` 25건 중 11건이었다 |
| 스타트업 매체 RSS | 벤처스퀘어, 아웃스탠딩, 스타트업레시피, 플래텀, 비석세스, 스타트업투데이 | 국내 신규 서비스 보도가 모이는 곳 |
| 앱스토어 한국 무료 차트 | 100위 안에서 최근 120일 이내 출시된 앱 | 새로 나온 앱이 차트에 오르면 화제 신호다 |

수집 → 규칙 정리(중복·잡음·이미 보낸 항목 제거) → **LLM 1: 아이템 카드**(대기업·공공기관 발표와 행사·투자 단신 제외) → 앱스토어·네이버로 국내 유사 서비스 근거 수집 → **LLM 2: 보완 아이디어**(최대 3개, 탈락 판정도 이유와 함께) 순서로 처리한다.

## 실행

Python 3.11 이상. 직접 의존성은 검증한 버전으로 고정한 `openai==2.54.0` 하나다.

```bash
pip install -r requirements.txt
python kr_digest.py --dry-run          # 발송하지 않고 화면에 출력
python kr_digest.py --hours 48         # 수집 기간 변경 (기본 24시간)
python kr_digest.py --state state/seen.json
python -m unittest discover -s tests -v   # 네트워크·실제 API 호출 없이 검증
```

`--dry-run`은 디스코드로 보내지 않고 발송 기록도 남기지 않는다. **키가 있으면 dry-run도 OpenAI API를 호출하므로 사용료가 발생한다.** 같은 항목을 다시 보내지 않도록 `state/seen.json`에 URL(7일)·서비스명(30일)·앱 ID(120일)를 기록한다.

## GitHub Actions 설정

| Secret | 용도 | 없으면 |
|---|---|---|
| `DISCORD_WEBHOOK_URL` | 발송 대상 채널 | 발송 실패로 종료 |
| `OPENAI_API_KEY` | 아이템 카드·보완 아이디어 생성 | 규칙으로 거른 아이템 목록만 보낸다 |
| `NAVER_CLIENT_ID`, `NAVER_CLIENT_SECRET` | 국내 유사 서비스 근거 보강 | 앱스토어 검색만 쓴다 |

저장소 **Settings → Secrets and variables → Actions → Secrets**에 `OPENAI_API_KEY`를 등록한다. ChatGPT 구독과 별개인 OpenAI API 프로젝트 키와 사용 가능한 API 한도가 필요하다. 키 값은 코드·로그에 넣지 않는다.

모델 기본값은 `gpt-5.6-sol`이다. 로컬에서는 환경변수 `IDEA_MODEL`, Actions에서는 같은 이름의 repository variable로 바꾼다. 비어 있으면 기본값을 쓴다. 다른 모델을 지정할 때는 Responses API, strict JSON Schema, `reasoning.effort=low/high` 지원과 해당 API 프로젝트의 접근 권한을 먼저 확인한다. 모델 자동 전환은 없다.

## OpenAI 호출과 실패 처리

- 단일 OpenAI 클라이언트의 **Responses API**를 쓴다. 카드 생성은 `low`, 아이디어 검토는 `high` reasoning effort를 유지한다.
- `text.format`의 JSON Schema에 `strict=true`를 지정한다. 출력 상한은 사고 토큰을 포함해 32,000개이며 `store=false`로 요청한다.
- 요청별 timeout은 120초, SDK 재시도는 최대 2회(최초 요청 포함 최대 3회)다. 연결·시간초과·408/409/429/5xx 등 SDK가 재시도 대상으로 분류하는 오류만 같은 모델로 재시도한다. 별도 재시도 루프나 다른 provider/model로의 우회는 없다.
- 키 없음·클라이언트 초기화 실패·카드 생성 실패는 기존 규칙 기반 목록으로 내려간다. 아이디어 생성만 실패하면 성공한 카드 목록을 유지한다.
- 거절, 미완료/중단 응답, JSON 오류, 잘못된 필드 타입·번호·중복 번호·입력 누락도 실패로 처리한다. **저하 모드에서는 새 발송 기록을 남기지 않아** 문제를 고친 뒤 같은 항목을 다시 처리할 수 있다.
- 성공 응답의 모델·입력/출력 토큰 수를 `[LLM]` 로그에 남긴다. Actions의 `success`만으로 API 사용 성공을 판단하지 말고 이 로그와 단계별 실패 안내를 함께 확인한다. 실제 사용료는 API 사용량 대시보드에서 확인한다.
- 수동 실행용 해외 다이제스트의 `llm_enrich.py`도 같은 키·모델·Responses API와 재시도 정책을 사용한다.

PR과 main 변경 시 Python 3.11/3.12에서 테스트가 실행된다. 테스트는 합성 응답과 실제 OpenAI SDK의 가짜 HTTP 전송을 사용하며 API 키·네이버·디스코드 접속 없이 요청 직렬화, 재시도 한도, 실패 시 목록 유지와 기록 보존을 확인한다. 모델의 실제 접근 권한·과금·한국어 출력 품질은 키를 설정한 별도 실행에서 확인해야 한다.

공식 문서: [Responses API](https://developers.openai.com/api/docs/guides/migrate-to-responses), [Structured Outputs](https://developers.openai.com/api/docs/guides/structured-outputs), [GPT-5.6 Sol](https://developers.openai.com/api/docs/models/gpt-5.6-sol).

## 한계

- 구글 뉴스 RSS는 공식 API가 아니어서 형식이 바뀌거나 막힐 수 있다. 기사 링크도 구글을 거치는 주소다.
- 매체·차트 피드가 클라우드 IP를 막을 수 있다. 실패한 소스는 실행 로그에 남는다.
- 카드와 아이디어는 LLM의 판단이지 사실 검증이 아니다. `국내 중복: 없음`은 검색 범위 안에서 찾지 못했다는 뜻이다.
- 공개 저장소는 60일 동안 활동이 없으면 GitHub가 예약 워크플로를 자동으로 끈다.
- 실행 기록은 Actions 캐시에 보관한다. 캐시가 지워지면 같은 항목이 한 번 더 발송될 수 있다.

## 관련 파일

- `kr_digest.py` 진입점 · `kr_sources.py` 수집 · `idea_ladder.py` LLM 단계 · `seen_state.py` 발송 기록
- `idea_ladder_prompt.md` 사람이 직접 쓰는 심층 검증 프롬프트 · `kr_check.py` 키워드 중복 검사기
- `daily_digest.py` 기존 해외 다이제스트. 자동 실행에서는 빠졌고 `python daily_digest.py`로 직접 돌릴 수 있다
- 이전 설계와 구현 계획은 `docs/superpowers/` 아래에 있다. 전환 전 기록이므로 현재 키·모델·SDK 설정은 이 README와 실행 코드를 따른다
