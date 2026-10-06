# 기회 레이더

해커톤·공모전·대외활동·인턴·일경험·신입 채용 공고를 GitHub Actions에서 매일 오전 9시(한국 시간)에 수집하고 Discord에 보냅니다. PC를 켜 두거나 앱을 설치할 필요 없이 같은 Discord 계정의 채널에서 확인할 수 있습니다. GitHub 예약 실행은 대기 상황에 따라 지연될 수 있습니다.

기존 아이디어 레이더와 실행 예약·코드·발송 기록을 분리했습니다. Discord 발송 이름은 `기회 레이더`입니다.

## 수집 범위

- 25개 초기 출처: 공모전·대외활동 포털, 정부·기관·지역 청년 정보와 대학·기업 관련 공지.
- 매일 여섯 종류의 기회를 검색하고 지역·분야·산학협력단·재단·연구원·협회를 추가 탐색합니다. 웹 검색에는 사이트 제한을 걸지 않습니다.
- Google News RSS에서 최근 모집 소식을 찾고 공고 링크를 확인합니다.
- 실제로 발견하고 원문을 조회한 공지 게시판을 최대 60개까지 저장해 다음 수집에 포함합니다.
- 원문 조회 예산은 회당 최대 60개입니다. 로그인·이미지·PDF에만 적힌 조건과 검색엔진이 색인하지 않은 공고는 놓칠 수 있습니다.

기본 웹 검색은 Bing 공개 RSS이며 품질과 가용성이 일정하지 않습니다. 저장소에 `NAVER_CLIENT_ID`, `NAVER_CLIENT_SECRET` Secrets가 있으면 네이버 웹 문서 검색 API를 대신 사용합니다. 이 서버 모드는 AI 해석이나 유료 OpenAI API, 개인 ChatGPT 로그인 정보를 사용하지 않습니다.

## 알림과 검증

- 처음 발견한 공고, 확인된 마감 변경, D-7·D-3·D-1 마감 알림.
- 연도가 명시된 접수 마감과 채용 원문의 `JobPosting.validThrough`만 자동 날짜로 인정합니다. 행사 개최일을 접수 마감으로 사용하지 않습니다.
- 원문 제목 대조 결과와 조건 확인이 필요한 후보를 구분해 한국어 PDF에 기록합니다. PDF 생성 실패 시 Markdown 보고서를 보냅니다.
- Discord 메시지 ID를 받은 뒤에만 발송 기록을 저장합니다. 같은 날 재실행은 생략하며, 실패한 발송을 자동 반복하지 않습니다.
- 발송 상태는 GitHub Actions cache에 보관합니다. 캐시가 삭제·만료되면 이전 공고가 다시 알림될 수 있습니다.

## 운영

Actions → **Opportunity Radar** → **Run workflow**로 수동 실행할 수 있습니다. 매일 예약은 기본 브랜치에서 동작합니다.

필수 Secret은 `DISCORD_WEBHOOK_URL`입니다. 기존 아이디어 레이더와 같은 채널을 사용합니다. 별도 채널을 원하면 `OPPORTUNITY_DISCORD_WEBHOOK_URL`을 설정하면 우선 적용됩니다. 실제 발송 주소와 검색 API 비밀 값은 코드에 넣지 않습니다.

공개 저장소는 60일 동안 저장소 활동이 없으면 GitHub가 예약 실행을 중지할 수 있습니다. Actions에서 다시 활성화할 수 있습니다.

개발 확인:

```sh
cd opportunity_radar
python -m pip install -r requirements.txt
python -m unittest discover -s tests -v
python radar.py --cloud --dry-run
```

`--dry-run`은 보고서를 만들지만 Discord 발송과 발송 기록 변경은 하지 않습니다. 개인 PC용 Codex 탐색 코드도 보존되어 있으나 GitHub 예약에서는 `--cloud`만 실행합니다.

운영 근거: [GitHub 예약 실행](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#schedule), [네이버 웹 문서 검색](https://developers.naver.com/docs/serviceapi/search/web/web.md).
