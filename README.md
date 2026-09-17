# Idea-radar

국내에서 **새로 나온 창업 아이템**을 매일 모아, 그 아이템을 한 단계 끌어올린 **보완 아이디어**와 함께 디스코드로 보내는 봇이다. ChatGPT 구독으로 로그인한 **개인 PC의 Codex CLI**가 분석한다. Windows 예약 실행의 기본 시간은 08:00(PC 현지 시간)이다.

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

## 로컬 설정

Python 3.11 이상과 Codex CLI가 필요하다. Python 외부 패키지는 없다. 이 전환은 Codex CLI `0.154.0-alpha.6.2`에서 실제 구독 호출을 검증했다. CLI가 `--ignore-user-config`, `--ephemeral`, `--output-schema`를 지원해야 한다.

1. 영구 보관할 폴더에 이 저장소를 내려받는다. 예약 작업은 이 폴더의 코드를 실행하므로 임시 폴더를 쓰지 않는다.
2. `codex login`으로 **ChatGPT 계정**에 로그인한다. `codex login status`가 `Logged in using ChatGPT`인지 확인한다.
3. `.env.example`을 `.env`로 복사하고 Discord 발송 주소를 넣는다. 이 파일은 Git에서 제외된다. ChatGPT 로그인 토큰이나 API 키는 넣지 않는다.
4. 아래 준비 확인과 미리보기를 실행한다.

```powershell
python local_runner.py --check                 # 발송 설정·구독 로그인만 확인, 모델 호출 없음
python local_runner.py --dry-run               # 구독으로 분석, 발송·발송 기록 저장 없음
python local_runner.py --dry-run --no-llm      # 구독 사용량 없이 뉴스 목록만 미리보기
python local_runner.py                         # 분석 결과를 Discord로 발송
python -m unittest discover -s tests -v        # 외부 접속 없이 검증
```

`--dry-run`도 Codex를 사용하면 **구독 사용량을 소모한다**. `--no-llm`은 모델 호출을 생략한다. 수집 기간은 `--hours 48`처럼 변경한다. `local_runner.py`는 실행 위치와 관계없이 설치 폴더의 `.env`와 `state/seen.json`을 사용하고, 중복 실행을 잠가 발송 기록 충돌을 막는다.

| 로컬 설정 | 용도 | 없으면 |
|---|---|---|
| `DISCORD_WEBHOOK_URL` | Discord 발송 대상 | 발송 실패로 종료 (`--dry-run`은 가능) |
| `NAVER_CLIENT_ID`, `NAVER_CLIENT_SECRET` | 국내 유사 서비스 근거 보강 | 앱스토어 검색만 사용 |
| `IDEA_CODEX_BIN` | Codex 실행 파일 절대 경로 | PATH, Windows Codex 앱 설치 폴더 순으로 검색 |
| `IDEA_MODEL` | 구독에서 사용할 모델 | `gpt-5.6-sol` |
| `IDEA_LLM_MODE` | `codex` 또는 `off` | `codex` |

구독 한도는 평소 Codex 사용과 공유한다. 별도 OpenAI API 호출 경로와 Python SDK 의존성은 제거했다. `OPENAI_API_KEY`나 `CODEX_API_KEY`가 환경에 있더라도 Codex 자식 프로세스에 전달하지 않는다. 별도 크레딧/추가 사용 설정은 사용자의 ChatGPT 계정 정책을 따른다.

## Windows 예약 실행

먼저 `python local_runner.py --check`가 성공해야 한다. 현재 로그인한 Windows 사용자로 실행하며, 기본 시간은 **PC 현지 시간 08:00**이다. 한국 시간대인지 확인한다.

```powershell
# Python 실행 파일의 실제 절대 경로로 바꾼다.
.\scripts\install-task.ps1 -Python 'C:\Python312\python.exe' -At '08:00'
```

설치되는 작업 이름은 `IdeaRadar-Daily`다. PC가 켜져 있고 Windows에 로그인되어 있어야 한다. 놓친 실행은 다음 실행 가능 시점에 처리한다. 작업이 이미 있으면 덮어쓰지 않고 중단한다. 실행 로그는 `logs/`, 중복 방지 기록은 `state/`에 저장한다. 로그와 기록 모두 Git에서 제외된다. 작업 삭제는 Windows 작업 스케줄러에서 `IdeaRadar-Daily`를 선택해 진행한다.

예약 작업은 설치된 코드만 실행하며 Git에서 새 코드를 자동으로 받지 않는다. 업데이트할 때는 예약 실행과 겹치지 않는 시간에 해당 폴더의 코드를 갱신하고 테스트한다.

## GitHub Actions와 전환 순서

GitHub의 기존 08:00(KST) 예약은 **LLM 없는 뉴스 목록 백업**으로 남겨둔다. 구독 로그인 정보는 GitHub에 업로드하지 않는다. 호스팅된 Actions는 Codex를 설치하거나 유료 API를 호출하지 않는다.

1. PC의 `.env`를 채우고 구독 분석 미리보기를 확인한다.
2. 로컬 예약 실행을 설치한다.
3. 저장소 **Settings → Secrets and variables → Actions → Variables**에서 `IDEA_LOCAL_ENABLED=true`를 설정한다. 그러면 GitHub의 정기 발송이 중단되어 이중 발송을 피할 수 있다.

수동 `workflow_dispatch`는 위 변수와 관계없이 목록 백업을 발송할 수 있다. GitHub에서 필요한 Secret은 `DISCORD_WEBHOOK_URL`과 선택 사항인 `NAVER_CLIENT_ID`, `NAVER_CLIENT_SECRET`뿐이다. 기존 `OPENAI_API_KEY` Secret은 새 코드에서 참조하지 않는다. PC와 GitHub의 발송 기록은 별개이므로 전환 직후에는 일부 항목이 반복될 수 있다.

## Codex 호출과 실패 처리

- 카드 생성은 `low`, 아이디어 검토는 `high` reasoning effort로 같은 모델을 사용한다. 모델/provider 자동 전환은 없다.
- 저장된 로그인 종류를 먼저 확인하고 ChatGPT 로그인만 허용한다. API 키 로그인은 변경하거나 로그아웃시키지 않고 거절한다.
- `codex exec --output-schema`로 기존 JSON 스키마를 전달한다. 실행은 임시 폴더·읽기 전용 샌드박스에서 진행한다. 사용자 설정, 셸, 브라우저, 앱/플러그인, 훅, 다중 에이전트 기능을 비활성화한다. 뉴스는 도구 실행 지시가 아닌 분석할 데이터로 취급한다.
- 봇에서 추가 호출 재시도는 하지 않는다. Codex 자체의 연결 재시도는 CLI가 관리한다. 각 단계의 전체 제한 시간은 300초다.
- CLI 없음·분석 비활성화·구독 로그인 실패·카드 생성 실패는 기존 뉴스 목록으로 내려간다. 아이디어 단계만 실패하면 성공한 카드 목록을 유지한다.
- 비정상 종료, 실패/미완료 이벤트, 빈 결과, JSON 오류, 잘못된 필드·번호·중복·입력 누락도 실패다. **저하 모드에서는 새 발송 기록을 남기지 않아** 문제를 고친 후 재처리할 수 있다.
- 성공 시 `[LLM] provider=codex auth=chatgpt`와 모델·토큰 수를 기록한다. 로그인 정보와 CLI 원문 오류는 출력하지 않는다.
- 수동 해외 다이제스트의 `llm_enrich.py`도 같은 Codex 구독 호출을 사용한다.

PR과 main 변경 시 Windows/Linux의 Python 3.11/3.12에서 테스트한다. 테스트는 가짜 CLI 응답과 실제 로컬 프로세스로 인증 종류 확인, 비밀값 전달 방지, 시간 제한, 출력 검증, 저하 모드, 기록 보존과 실행 잠금을 확인한다. CI에서 실제 모델·네이버·Discord는 호출하지 않는다.

공식 문서: [ChatGPT 구독 인증](https://learn.chatgpt.com/docs/auth), [Codex 자동 실행과 JSON 출력](https://learn.chatgpt.com/docs/non-interactive-mode), [설정 참조](https://learn.chatgpt.com/docs/config-file/config-reference).

## 한계

- 구글 뉴스 RSS는 공식 API가 아니어서 형식이 바뀌거나 막힐 수 있다. 기사 링크도 구글을 거치는 주소다.
- 매체·차트 피드가 클라우드 IP를 막을 수 있다. 실패한 소스는 실행 로그에 남는다.
- 카드와 아이디어는 LLM의 판단이지 사실 검증이 아니다. `국내 중복: 없음`은 검색 범위 안에서 찾지 못했다는 뜻이다.
- 공개 저장소는 60일 동안 활동이 없으면 GitHub가 예약 워크플로를 자동으로 끈다.
- 로컬 실행 기록은 `state/seen.json`, GitHub 백업의 기록은 Actions 캐시에 보관한다. 기록이 지워지면 같은 항목이 다시 발송될 수 있다.

## 관련 파일

- `local_runner.py` 로컬 진입점 · `codex_llm.py` 구독 호출 · `scripts/` Windows 예약 실행
- `kr_digest.py` 다이제스트 진입점 · `kr_sources.py` 수집 · `idea_ladder.py` LLM 단계 · `seen_state.py` 발송 기록
- `idea_ladder_prompt.md` 사람이 직접 쓰는 심층 검증 프롬프트 · `kr_check.py` 키워드 중복 검사기
- `daily_digest.py` 기존 해외 다이제스트. 자동 실행에서는 빠졌고 `python daily_digest.py`로 직접 돌릴 수 있다
- 이전 설계와 구현 계획은 `docs/superpowers/` 아래에 있다. 전환 전 기록이므로 현재 인증·모델·실행 설정은 이 README와 실행 코드를 따른다
