# Idea-radar 창업정보 중심 수정본

대상: https://github.com/Wordmaster-digital/Idea-radar

원본 main: `c35631d8c4b20428181aca535906f8948b97744b`

이 패키지는 현재 Idea-radar의 코드에 맞춰 작성했다. 앞서 제공한 `startup-alert-update.zip`은 종료된 다른 저장소용이므로 Idea-radar에 적용하지 않는다.

## 적용 상태와 연결 문제

원격 저장소에는 아직 반영되지 않았다. 확인된 GitHub 계정 `Wordmaster-digital`은 Idea-radar의 소유자이며 쓰기 권한이 있다. 그러나 연결된 GitHub 앱 설치 목록에는 `sodam3156`만 존재한다. 실제 Idea-radar 브랜치 생성은 `403 Resource not accessible by integration`으로 거부됐다. 사용자 계정의 저장소 권한 문제와 앱 설치 범위 문제를 구분해야 한다.

현재 GitHub 연결의 앱 설치/저장소 접근 범위에 **Wordmaster-digital 계정과 Idea-radar 저장소**를 추가해야 한다. 이 수정에 sodam3156 저장소 접근이나 협업자 권한은 필요하지 않다.

## 포함 파일

- `changed-files/`: 변경 파일 6개 — daily_digest.py, llm_enrich.py, startup_focus.py, README.md, tests/test_digest.py, .github/workflows/daily_digest.yml.
- `idea-radar-focus.patch`: 동일 변경을 한 번에 적용할 Git 패치.
- `PREVIEW.md`: 오프라인 예제 입력으로 생성한 디스코드 게시 미리보기.

## 주요 변경

1. 기본 수집은 Show/Ask HN, Product Hunt, First Round Review, Y Combinator, 플래텀, 벤처스퀘어로 좁힌다. 기존 일반 기술·제품·산업 피드는 명시적 선택 대상으로 전환한다.
2. 고객 문제·사업모델·수익화·고객 확보·검증·창업 실행자료 중심으로 선별한다. 모든 출처에 공통 적용하며, LLM 키가 없어도 뉴스 필터를 유지한다.
3. 제목/내용/분류/활용 관점/출처/원문 링크를 게시한다. 선택적 LLM은 명시된 고객·문제·수익모델을 보강하고 단순 기사에는 제외 판정을 내린다.
4. 선별 전에 인기 점수 상위 N건을 채우던 흐름을 수정한다. 추적 URL 중복 제거, Product Hunt 원문 링크·게시일 처리, 긴 메시지 분할도 적용한다.
5. 일반 창업 자료를 '한국 시장 공백'으로 분류하지 않는다. 제품 사례의 단순 조회 무응답이나 검색 근거 부족은 미확인으로 남긴다.
6. 기존 매일 08:00 KST 일정을 유지하고, 수집 전에 테스트를 실행한다. 선택적 LLM·네이버 환경변수를 Actions Secrets에 연결한다.

## 적용 방법

저장소의 로컬 복제 폴더에서 패치 경로를 실제 압축 해제 경로로 바꿔 실행한다.

```bash
git switch -c fix/startup-information-focus
git apply --check /path/to/idea-radar-focus.patch
git apply /path/to/idea-radar-focus.patch
python3 -m unittest discover -s tests -v
git add daily_digest.py llm_enrich.py startup_focus.py README.md tests/test_digest.py .github/workflows/daily_digest.yml
git commit -m "Focus Idea-radar on actionable startup information"
git push -u origin fix/startup-information-focus
```

GitHub에서 main으로 병합하면 다음 정기 실행부터 새 코드를 사용한다. 이후 원본이 변경되어 패치 검사에 실패하면 최신 파일과 대조해서 적용한다. `changed-files/`를 같은 경로에 교체/추가하는 방법도 가능하다.

`--dry-run`도 외부 수집과 선택적 LLM 호출은 수행한다. 네트워크·LLM 비용·디스코드 발송 없는 검증에는 unittest 명령을 사용한다.

## 검증

22개 오프라인 테스트 통과. 정확한 원본 파일에서 패치 적용 검사를 통과했고, 수정 파일 6개의 내용이 패키지와 일치함을 확인했다. 실제 Discord 발송과 유료 LLM 호출은 수행하지 않았다. 새 외부 RSS의 실제 수집은 실행 환경의 네트워크 접근 문제로 검증하지 못했다. 출처별 실제 가용성은 운영 실행 로그로 확인해야 한다. 규칙은 제목/피드 설명을 사용하므로 모호하거나 설명이 짧은 자료는 누락될 수 있다.
