# 작업 규칙 (사람·AI 에이전트 공통)

이 파일은 Claude Code·Codex·Antigravity가 매번 읽습니다. 그래서 짧게 유지합니다. 자세한 설명은 `docs/DEVELOPMENT.md`에 있고, 필요할 때만 여세요.

## 토큰을 아끼며 일하기

- **기능 하나 = 브랜치 하나 = 세션 하나.** PR을 올리면 다음 작업은 새 세션에서 합니다. 대화가 길어질수록 도구를 한 번 쓸 때마다 앞의 대화가 전부 다시 전달됩니다.
- **필요한 곳만 읽기.** 아래 코드 지도로 파일을 고르고, `grep -n`으로 줄 번호를 찾은 뒤 그 부분만 읽습니다. `skill_viewer.py`(약 1,000줄)를 통째로 읽지 마세요.
- **부분 수정.** 기존 파일은 바뀌는 줄만 고치고, 파일 전체를 다시 쓰지 않습니다.
- **확인은 요약 출력으로.**
  - 수정하는 동안: `python tools/check.py fast` (창이 뜨지 않는 테스트만, 약 30초)
  - 한 테스트만: `python -m unittest test_viewer.PanelTest.test_03_tabs_search_rail`
  - 푸시 직전 한 번: `python tools/check.py` (린트·타입·전체 테스트, 통과하면 단계마다 한 줄, 실패하면 실패 부분만)
- **CI 실패 로그는 실패 부분만:** `gh run view <번호> --log-failed | grep -A15 -E "^(FAIL|ERROR)"`
- **이미지는 꼭 필요할 때만.** 화면을 바꿨을 때만 `python docs/make_screenshots.py`를 실행하고, 바뀐 이미지만 엽니다.
- **서브에이전트·여러 에이전트 리뷰(`/review-pr` 등)는 꼭 필요할 때만.** 에이전트마다 diff를 따로 읽습니다.

## 커밋과 PR

- `main`에 직접 커밋하지 않습니다. `feat/…`, `fix/…`, `chore/…` 브랜치에서 PR을 엽니다.
- 커밋은 이유 단위로 나누되, 같은 이유의 고침은 한 커밋으로 합칩니다. 커밋마다 보안 검토가 한 번씩 돕니다.
- 커밋 메시지: `Type: 한국어 요약`(Feat·Fix·Refactor·Chore·Docs·Test·Style, 명사로 끝, 마침표 없음). 본문은 필요할 때만 "왜"를 불릿 2~4개로.
- PR 본문은 `.github/pull_request_template.md`의 세 항목만, 항목마다 3~6줄. 검증에는 실제로 실행한 명령과 결과를 한 줄씩.
- 머지는 CI(Windows·macOS) 통과 후, squash 없이 머지 커밋으로.

## 코드 지도

| 할 일 | 볼 곳 |
|---|---|
| 화면, 탭, 카테고리 목록, 스킬 줄, 메뉴, 트레이 | `skill_viewer.py` – `App` |
| 스킬 추가 대화상자(링크 설치·새로 만들기) | `skill_viewer.py` – `AddDialog` |
| 스킬 목록 읽기, 출처·카테고리·한국어 설명 | `skill_viewer.py` – `collect`, `describe_origin`, `categorize`, `brief` |
| 링크 해석, git 받기, 설치·업데이트·삭제·생성 | `skill_manager.py` |
| Windows/macOS 차이(링크, 파일 열기, 글꼴, 화면 영역) | `platform_support.py` |
| 카테고리 정의, 스킬별 한국어 설명 | `skill_categories.json`, `skill_notes.json` |
| 추천 탭의 목록(읽기 `load_recommended`, 새로 만들기 `tools/update_recommend.py`) | `skill_manager.py`, `skill_recommend.json` |
| 시작 메뉴 / macOS 앱 등록 | `install.py` |
| 테스트 | `test_manager.py`(로직), `test_viewer.py`(`PanelTest`만 창을 띄움), `test_install.py`, `test_check.py` |

## 지켜야 할 것

- Tk 코드는 `skill_viewer.py`에만, OS 분기는 `platform_support.py`에만 둡니다.
- 사용자 파일을 영구 삭제하지 않습니다. 지울 때는 `trash/`로 옮깁니다.
- 링크 설치는 https만 받습니다.
- 버튼·체크박스는 테마 위젯(`App._button`, `App._check`)을 씁니다. 시스템 위젯은 테마 색이 적용되지 않습니다.
- 색을 바꾸면 명도 대비 테스트(`test_14_…`)가 통과해야 합니다.
