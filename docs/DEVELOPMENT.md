# 개발 가이드

SkillDesk를 고치거나 기능을 더하려는 사람을 위한 문서입니다. 사용 방법은 [README](../README.md)를 보세요.

## 목차

- [한눈에 보기](#한눈에-보기)
- [개발 환경 준비](#개발-환경-준비)
- [실행과 디버깅](#실행과-디버깅)
- [폴더 구조](#폴더-구조)
- [구조와 흐름](#구조와-흐름)
- [테스트](#테스트)
- [린트와 타입 검사](#린트와-타입-검사)
- [CI](#ci)
- [자주 하는 작업](#자주-하는-작업)
- [코드 규칙](#코드-규칙)
- [브랜치·커밋·PR 규칙](#브랜치커밋pr-규칙)
- [알려진 한계](#알려진-한계)

> 매일 쓰는 규칙(토큰을 아끼는 작업 방식, 커밋·PR 규칙, 코드 지도)은 저장소 루트의 [`AGENTS.md`](../AGENTS.md)에 짧게 정리돼 있습니다. AI 에이전트도 그 파일을 읽습니다.

## 한눈에 보기

| 항목 | 내용 |
|---|---|
| 언어 | Python 3.12 이상 |
| 화면 | 표준 라이브러리 `tkinter` (Tk 8.6) |
| 트레이 | `pystray` + `pillow` (macOS는 `pyobjc-framework-Quartz` 추가) |
| 외부 명령 | `git` (링크 설치·업데이트), Windows는 `cmd /c mklink /J`, `powershell` (바로 가기) |
| 지원 OS | Windows 10/11, macOS 10.15 이상. CI에서 둘 다 테스트 |
| 테스트 | 표준 라이브러리 `unittest` |
| 린트 / 타입 | `ruff`, `pyright` |

외부 패키지를 일부러 적게 씁니다. 새 패키지가 꼭 필요하면 PR에 이유를 적어 주세요.

## 개발 환경 준비

### 1. 필요한 프로그램

- Python 3.12 이상 (설치 방법은 [README의 Python 설치](../README.md#2-python-설치))
  - macOS는 python.org 설치본이나 Homebrew `python-tk@3.12`처럼 **Tk 8.6**이 있는 Python이어야 합니다.
- Git
- 편집기: 무엇이든 됩니다. VS Code라면 Python, Ruff 확장을 권장합니다.

### 2. 저장소 받기와 가상환경

**Windows (PowerShell)**

```bash
git clone https://github.com/sgustjd2/skill-manage-viewer.git
cd skill-manage-viewer
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt ruff pyright
```

PowerShell이 스크립트 실행을 막으면 `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned`를 한 번 실행하거나, 명령 프롬프트에서 `.venv\Scripts\activate.bat`을 쓰세요.

**macOS (터미널)**

```bash
git clone https://github.com/sgustjd2/skill-manage-viewer.git
cd skill-manage-viewer
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt ruff pyright
```

- `.venv/`는 `.gitignore`에 들어 있어 커밋되지 않습니다.
- `pyright`는 처음 실행할 때 Node.js를 쓰거나 내려받습니다.

### 3. 잘 준비됐는지 확인

```bash
python -c "import tkinter, pystray, PIL; print('Tk', tkinter.TkVersion)"   # Tk 8.6 이 나와야 합니다
python skill_viewer.py --check                                           # 이 PC의 스킬 탐색 결과
python -m unittest tests.test_manager tests.test_install tests.test_update   # 모두 OK
```

## 실행과 디버깅

```bash
python skill_viewer.py            # 콘솔에 오류가 그대로 보여 개발할 때 편합니다
pythonw skill_viewer.py           # Windows: 콘솔 없이 (실사용)
python skill_viewer.py --hidden   # 트레이로만 시작
python skill_viewer.py --check    # 창 없이 에이전트별 개수·카테고리·출처만 출력
python skill_viewer.py --make-icon   # skill_viewer.ico 다시 만들기 (저장소 맨 위 실행 파일이 src/skilldesk로 넘겨 줍니다)
python skill_viewer.py --make-icns   # macOS 아이콘(skill_viewer.icns) 만들기
```

- 앱은 **한 번에 하나만** 뜹니다(`platform_support.already_running`, 임시 폴더의 `skill-catalog.lock` 파일 잠금). 다시 실행하면 새 창 대신 떠 있는 앱에 "창을 보여 달라"는 쪽지(임시 폴더의 `skilldesk.show`, `platform_support.ask_running_copy_to_show`)를 남기고 끝납니다. 떠 있는 앱은 0.1초마다 쪽지를 확인해(`App._pump`) 창을 띄웁니다. 그래서 고친 코드를 확인하려면 **트레이 메뉴 → 종료**로 이전 것을 끄고 다시 실행하세요(이전 앱이 새 코드가 아니면 쪽지를 읽지 못합니다).
- 시작에 4~8초가 걸리는 것은 정상입니다. 세 에이전트의 스킬 폴더를 읽고 git 스킬의 커밋을 조회하기 때문입니다.
- 실제 `~/.claude` 등을 건드리지 않고 화면만 보고 싶으면 `python docs/make_screenshots.py`를 참고하세요. 임시 폴더에 예시 스킬을 만들어 앱을 띄웁니다.

## 폴더 구조

```
skill-manage-viewer/
├─ src/skilldesk/           앱 본체(파이썬 패키지). 모듈끼리는 `from . import …`로 부릅니다
│  ├─ skill_viewer.py       화면: 창, 탭, 카테고리 목록, 스킬 줄, 메뉴, 추가 대화상자, 트레이, 색·글꼴. `main()`이 시작점
│  ├─ skill_manager.py      일: 링크 해석, git 받기, 설치·업데이트·삭제·생성, SKILL.md 읽기·저장(`read_skill_md`/`save_skill_md`), 메모·설정 저장 (Tk 없음)
│  ├─ skill_agent.py        에이전트(Claude Code)에게 스킬을 만들게 함: 임시 폴더에서 실행·시간 제한·취소, 결과 검사(Draft), 미리보기 뒤 설치 (Tk 없음)
│  ├─ skill_harness.py      프로젝트 하네스: 세트·프로젝트 저장(`harness.json`), 적용·해제(링크 기록으로 이 앱이 만든 것만 지움), 구조 가이드 트리 (Tk 없음). 화면은 `skill_viewer.py`의 `HarnessWindow`
│  ├─ skill_usage.py        Claude Code 대화 기록(.jsonl)에서 달별 스킬 호출 횟수 집계, 최근 2개월 안 쓴 스킬(정리 후보) 계산 (Tk 없음, 파일별 캐시)
│  ├─ platform_support.py   OS마다 다른 것: 폴더 링크, 파일 열기, 화면 영역, 글꼴 로드, 중복 실행 방지
│  ├─ install.py            시작 메뉴 바로 가기 / macOS .app 만들기·지우기
│  ├─ skill_update.py       앱 자신의 자동 업데이트: 안전 조건 확인, fast-forward, 새 코드 시험 실행, 실패 시 되돌리기 (git 클론 전용; exe에서는 항상 건너뜀)
│  ├─ self_update.py        exe·dmg 설치용 자동 업데이트: 릴리스 읽기 → 자산 고르기 → 허용 주소만 내려받기(크기·시간 제한) → SHA-256 대조 → Windows는 설치 파일 조용히 실행, macOS는 .app 교체 스크립트 (Tk 없음)
│  ├─ update_check.py       ZIP 설치(와 자동 업데이트를 못 하는 exe·dmg)용 새 버전 알림: `VERSION` 읽기, 릴리스 API 읽기·검증·비교 (Tk 없음). 내려받기·실행은 하지 않음
│  ├─ __main__.py           `python -m skilldesk` (src를 경로에 넣었을 때)
│  ├─ data/
│  │  ├─ skill_categories.json  카테고리 정의: 이름, 기준 문장(about), 소속 스킬, 새 스킬 분류용 keywords
│  │  ├─ skill_notes.json       스킬별 한국어 한 줄 용도 (이름 → 설명)
│  │  └─ skill_recommend.json   추천 탭에 보여 줄 skills.sh 인기·요즘 뜨는 목록, 어제 급등 목록, 날짜별 스타 수의 스냅샷 (tools/update_recommend.py가 만듦)
│  └─ assets/fonts/         Pretendard Regular·Bold + 라이선스
├─ tests/                   `test_*.py` 전부. `tests/__init__.py`가 `src/`를 경로에 넣습니다
│  ├─ test_manager.py       skill_manager 테스트
│  ├─ test_agent.py         skill_agent 테스트: 가짜 에이전트로 감지·미리보기 뒤 설치·임시 폴더·실패·시간 초과·취소
│  ├─ test_viewer.py        화면·플랫폼·카테고리·탐색 테스트
│  ├─ test_harness.py       skill_harness 테스트 (임시 폴더)
│  ├─ test_install.py       install.py 테스트
│  ├─ test_update.py        skill_update 테스트 (옛 폴더 구조의 설치가 새 구조로 업데이트되는 경우 포함)
│  ├─ test_update_check.py  update_check 테스트: 버전 비교, 릴리스 응답 검증, 실패 무시, exe·git·ZIP 구분
│  ├─ test_self_update.py   self_update 테스트: 주소·리다이렉트 허용 목록, 해시 목록 파싱·대조, 크기·시간 제한, 자산 선택, 상태 전이, 실행 인자, macOS 교체 스크립트 (로컬 가짜 서버)
│  ├─ test_recommend.py     추천 스냅샷: 페이지 파싱·거르기·실패 시 보존(`tools/update_recommend.py`)과 `load_recommended()`
│  └─ test_usage.py · test_release.py · test_check.py · test_privacy.py   그 밖의 테스트
├─ tools/                   check.py: 푸시 전 확인 한 번에 · update_recommend.py: 추천 스냅샷 다시 만들기 · release.py: 버전·릴리스 노트 · build_exe.py + skilldesk.iss: Windows 설치 파일 · build_mac.py: macOS 앱(dmg) 만들기 · update_e2e.ps1 / update_e2e.sh: PR의 CI에서만 도는 자동 업데이트 실제 시험 (앱은 쓰지 않음; 워크플로가 돌림)
├─ docs/
│  ├─ DEVELOPMENT.md        이 문서
│  ├─ make_screenshots.py   README 스크린샷 다시 찍기
│  └─ images/               README 스크린샷
├─ .github/workflows/       ci.yml(Windows·macOS CI) · recommend.yml(매일 추천 목록) · release.yml(버전·릴리스·설치 파일 빌드·공개)
├─ skill_viewer.py          실행 파일(얇음): 시작 메뉴 바로 가기·macOS 앱·`start.*`·옛 설치의 자동 업데이트가 이 경로를 씁니다. 고치지 말고 위치도 옮기지 마세요
├─ install.py               `python install.py`용 실행 파일(얇음). 본체는 `src/skilldesk/install.py`
├─ skill_viewer.ico         앱 아이콘 (Windows). 이미 만든 바로 가기가 이 경로를 가리킵니다
├─ start.bat / start.command  더블클릭 실행 (Windows / macOS)
├─ VERSION · requirements.txt · pyrightconfig.json
└─ README.md · AGENTS.md · CLAUDE.md
```

## 구조와 흐름

```
           ┌───────────────────────────┐
           │ skill_viewer.py (Tk 화면)  │  ← 사용자가 보고 누르는 것
           └──────┬───────────┬────────┘
                  │           │
     목록 읽기 collect()      │ 설치·업데이트·삭제·생성 (작업 스레드에서)
                  │           ▼
                  │   ┌──────────────────────┐
                  │   │ skill_manager.py      │  ← 파일·git 작업, Tk 모름
                  │   └──────────┬───────────┘
                  ▼              ▼
           ┌───────────────────────────┐
           │ platform_support.py        │  ← Windows / macOS 차이
           └───────────────────────────┘
```

**목록을 읽는 순서** (`skill_viewer.collect`)

1. 에이전트마다 읽을 폴더를 정합니다. 사용자 폴더(`mgr.AGENT_DIRS`), Claude Code 플러그인(`claude_plugin_roots`), Antigravity 내장 폴더.
2. `scan()`이 `SKILL.md`가 있는 폴더를 찾습니다. 링크는 따라 들어가되 같은 실제 경로는 한 번만 봅니다(macOS의 심볼릭 링크, 순환 링크 대비).
3. `describe_origin()`이 출처(`git`, `로컬`, `플러그인` …)와 관리 가능 여부(`managed`)를 정합니다. 에이전트 폴더 바로 아래에 있는 것만 관리 대상입니다.
4. `categorize()`가 카테고리를 정합니다. `skill_categories.json`의 `skills` 목록이 먼저이고, 목록에 없으면 `keywords`로 이름 → 설명 순서로 맞춰 봅니다.
5. `brief()`가 한국어 한 줄을 정합니다. 사용자가 고친 메모 → `skill_notes.json` → 원문 설명 첫 문장 순서입니다.

**설치가 일어나는 방식** (`skill_manager`)

- 링크 → `parse_link()`(https만, 경로 검사) → `fetch()`가 `~/.skill-manager/sources/`에 얕은 clone(`protocol.allow=never`) → `discover()`로 스킬 폴더 찾기 → `install()`이 각 에이전트 폴더에 **링크**를 만듭니다.
- 복사하지 않으므로 업데이트는 원본 clone에서 `git pull --ff-only` 한 번이면 됩니다(`update()`). 폴더가 옮겨졌으면 `relink()`가 다시 연결합니다.
- 삭제(`remove()`)는 링크면 끊고, 실제 폴더면 `trash/`로 옮깁니다. **영구 삭제 코드는 없습니다.**

**화면과 작업 스레드**

- 오래 걸리는 일(clone, fetch, pull)은 `App.bg(fn, done)`으로 작업 스레드에서 돌리고, 결과는 `App.events` 큐를 거쳐 Tk 스레드의 `_pump()`가 `done()`을 부릅니다. Tk 위젯은 Tk 스레드에서만 만지세요.
- 트레이: Windows는 `pystray`를 같은 프로세스에서(`run_detached`), macOS는 `pystray`가 메인 스레드를 요구해 **별도 프로세스**(`--tray-child`)로 띄우고 표준 출력으로 명령을 받습니다. 받은 명령은 `TRAY_COMMANDS`에 있는 것만 실행합니다.

**OS 차이는 `platform_support.py`에만**

| 기능 | Windows | macOS |
|---|---|---|
| 폴더 링크 | junction (`mklink /J`, 관리자 권한 불필요) | 심볼릭 링크 |
| 링크 지우기 | `os.rmdir` | `unlink` |
| 파일 열기 | `os.startfile` (안 되면 메모장) | `open -t` |
| 화면 영역 | `SPI_GETWORKAREA` | 메뉴 막대·Dock을 뺀 근사값 |
| 글꼴 로드 | `AddFontResourceEx(FR_PRIVATE)` | CoreText 프로세스 범위 등록 |
| 글꼴 크기 | 그대로 | ×4/3 (Tk가 72dpi 기준) |
| 우클릭 | `<Button-3>` | `<Button-2>`, `<Control-Button-1>` |
| 버튼 | Label로 그림 (macOS Tk가 네이티브 버튼 색을 무시해서 두 OS 모두 Label 사용) | 같음 |

새로 OS마다 다르게 해야 하는 일이 생기면 이 파일에 함수를 추가하고, 다른 파일에서는 `plat.함수()`만 부르세요.

## 테스트

```bash
python tools/check.py                                        # 린트·타입·모든 테스트, 실패한 것만 출력 (푸시 전)
python tools/check.py fast                                   # 창이 뜨지 않는 테스트만, 약 30초 (수정하는 동안)
python -m unittest tests.test_manager tests.test_viewer tests.test_install -v
python -m unittest tests.test_viewer.PanelTest.test_03_tabs_search_rail -v   # 하나만
```

- 테스트는 **임시 폴더만** 씁니다. `mgr.STORE`, `mgr.AGENT_DIRS` 등을 바꿔 끼워서 실제 `~/.claude`, `~/.codex`, `~/.skill-manager`에는 쓰지 않습니다.
- `test_viewer`는 실제로 창을 띄웠다 닫습니다. **Windows 로컬에서는 보이지 않는 별도 데스크톱(`CreateDesktopW`)에서 돌아 화면에 안 뜨고 포커스도 안 뺏습니다.** 눈으로 보려면 `SKILLDESK_TEST_VISIBLE=1`을 붙입니다. CI(`CI` 환경변수)와 macOS는 숨기지 않고 예전처럼 돕니다. 숨은 데스크톱에서는 트레이 아이콘과 스크린샷 저장을 건너뜁니다(`platform_support.TEST_DESKTOP_HIDDEN`). 키보드·마우스 입력을 흉내 내지 않고 코드로만 조작합니다.
- Windows에서 `SKILLDESK_TEST_VISIBLE=1`일 때만 `%TEMP%`에 확인용 스크린샷(`t3_list.png` 등)도 남깁니다.

| 파일 | 확인하는 것 |
|---|---|
| `test_manager.py` | 링크 해석(허용·거부 형식), 로컬 git 저장소로 설치·업데이트·재연결·삭제, 이름 충돌과 교체, 휴지통 이동, 새 스킬 생성, 메모 저장, 자동 업데이트 스크립트 목록 정리 |
| `test_viewer.py` – `PanelTest` | 트레이·최소화·화면 가운데 배치, 출처 표시, 탭·검색·카테고리 선택, 메뉴, 용도 수정, 추가 대화상자(생성·링크 설치·충돌·교체), 업데이트 확인·적용, 삭제, 테마 저장, 기울임 없는 글꼴, Pretendard 사용, **모든 글씨의 명도 대비**(라이트·다크, 마우스 올림·선택·비활성 포함) |
| `test_viewer.py` – `PlatformTest` | 폴더 링크 만들기·지우기, 중복 실행 방지, 다시 실행할 때 남기는 "창을 보여 달라" 쪽지(한 번만 읽힘·오래된 쪽지 무시), 우클릭 이벤트, 트레이 명령 허용 목록 |
| `test_viewer.py` – `CategoryTest` | 한 스킬이 두 카테고리에 들어가지 않는지, 설명 있는 스킬의 카테고리 누락, 색·기준 문장 짝 |
| `test_viewer.py` – `DiscoveryTest` | 다른 PC 상황: 링크로 설치된 스킬, 순환 링크, 이상한 `SKILL.md`, 없는 에이전트 폴더, 깨진 플러그인 파일, 환경변수 |
| `test_harness.py` | 세트·프로젝트 저장과 불러오기(이상한 파일 무시, 알 수 없는 키 보존), 적용 시 링크 생성, 해제 시 **이 앱이 만든 링크만** 제거, 같은 이름의 실제 폴더·사용자가 바꾼 링크는 그대로, 없는 프로젝트 폴더·경로 검증, `.git/info/exclude` 추가와 되돌리기(기본 꺼짐), 구조 트리가 아무것도 만들지 않는지 |
| `test_install.py` | macOS `.app` 구성과 제거 범위(모든 OS), Windows 바로 가기 내용(Windows만) |
| `test_recommend.py` | 추천 스냅샷: 급등 계산(첫날 빈 목록·증가량 순·하한·같은 날 다시 돌리기·기록 보관 일수), GitHub 실패 시 기존 파일 유지, 실제 페이지 모양의 작은 예시로 파싱, GitHub 아닌 출처·저장소별 상한·같은 이름·설치 수 하한 거르기, 형식이 바뀌면 실패, 실패하면 기존 파일 유지, 요청이 두 공개 페이지뿐인지, `load_recommended()`가 없거나 깨진 파일을 빈 목록으로 보는지 (네트워크 없음) |
| `test_usage.py` | 호출 횟수 집계: 도구 호출과 `/이름`, 달별 합계, 이어받은 대화의 중복, 캐시 재사용, 깨진 입력, 플러그인 접두어, 정리 후보(최근 2개월 기준·해가 바뀔 때·기록이 없을 때), 기록이 시작된 시점 |
| `test_update.py` | 앱 자동 업데이트: 공식 저장소·`main`·수정 없음일 때만 동작하는지, fast-forward만 하는지, 의존성 설치 조건, **실패하면 정확히 이전 커밋으로 되돌리는지**(임시 저장소 사용) |

**명도 대비 테스트**(`test_14_…`)는 화면에 실제로 그려진 모든 글씨와 배경 색 조합의 대비를 계산해 WCAG AA(일반 4.5:1, 큰 글씨·비활성 3:1)를 넘는지 봅니다. 색을 바꾸면 반드시 이 테스트를 돌리세요. 실패하면 어느 글씨가 몇 대 몇인지 알려 줍니다.

## 린트와 타입 검사

```bash
python -m ruff check . --line-length 140 --select E,F,W,B,UP,SIM
python -m ruff format --line-length 140 tests/test_viewer.py tests/test_manager.py tests/test_install.py src/skilldesk/install.py
python -m pyright src/skilldesk/skill_manager.py src/skilldesk/skill_viewer.py src/skilldesk/platform_support.py src/skilldesk/install.py   # 경로는 pyrightconfig.json
```

- 한 줄은 140자까지입니다.
- `pyright`는 지금 PC의 OS 기준으로 검사합니다. 다른 OS 전용 모듈(`fcntl` 등)에서 나오는 경고는 해당 줄에 `# pyright: ignore[...]`로 이유와 함께 표시합니다.

## CI

`.github/workflows/ci.yml`이 PR과 `main` 푸시마다 **windows-latest**, **macos-latest**에서 돌립니다.

1. Python 3.12 설치
2. `pip install -r requirements.txt ruff`
3. `python tools/check.py` (ruff + 모든 `test_*.py`. 통과한 단계는 한 줄, 실패하면 실패 부분만 출력해 로그가 짧습니다)

- macOS 장비가 없어도 macOS 동작을 이 CI로 확인합니다. 실제로 macOS CI가 "심볼릭 링크로 설치한 스킬이 안 보이는" 버그를 잡았습니다.
- CI에서는 화면 캡처 권한이 없어 스크린샷 저장을 건너뛰고, macOS 메뉴 막대 아이콘은 띄우지 않습니다.
- 실패 로그 보기: `gh run view <실행 번호> --log-failed`

## 자주 하는 작업

### 스킬을 분류하거나 한국어 설명을 붙일 때

1. `src/skilldesk/data/skill_categories.json`에서 맞는 카테고리의 `skills` 배열에 이름을 넣습니다. **한 곳에만** 넣으세요.
2. `src/skilldesk/data/skill_notes.json`에 `"이름": "한 줄 용도"`를 넣습니다. 짧게, "무엇을 할 때 쓰는지"가 드러나게 씁니다.
3. `python -m unittest tests.test_viewer.CategoryTest`로 중복·누락을 확인합니다.

플러그인 스킬은 `superpowers:brainstorming`처럼 앞에 플러그인 이름이 붙어도 뒤의 이름(`brainstorming`)으로 찾습니다.

### 추천 목록을 새로 받을 때

`python tools/update_recommend.py` (표준 라이브러리만 씁니다). skills.sh의 `/`와 `/trending`을 **한 번씩만** 받아 `src/skilldesk/data/skill_recommend.json`을 새로 씁니다. 앱은 이 파일만 읽고 네트워크를 쓰지 않습니다.

- 목록은 페이지에 실린 `initialSkills` JSON에서 읽습니다(화면 마크업이 아님). `/api/`·`/search`는 `robots.txt`가 막으므로 부르지 않습니다.
- 거르는 기준은 스크립트 위쪽 상수입니다: GitHub `owner/repo`만, 저장소당 `PER_REPO`개, 같은 스킬 이름은 한 번, 설치 `MIN_INSTALLS` 이상, 목록당 `SIZE`개.
- 받지 못했거나 페이지 형식이 바뀌어 `initialSkills`를 못 찾으면 **기존 파일을 그대로 두고** 종료 코드 1로 끝납니다. 이 경우 `parse_skills()`를 새 형식에 맞게 고치고 `test_recommend.py`의 예시를 갱신하세요.
- **어제 급등**: 위 두 목록에 나온 저장소(최대 약 60개)의 GitHub 스타 수를 `https://api.github.com/repos/<owner>/<repo>`로 한 번씩 받아 `stars`에 날짜별로 쌓고(`KEEP_DAYS`일치만 보관), 가장 가까운 이전 날짜와의 차이를 `surge`에 씁니다(`MIN_GAIN` 이상, `SURGE_SIZE`개까지, `surge_since`는 비교한 날짜). 첫 실행은 비교 대상이 없어 `surge`가 비어 있고 앱이 "내일부터 표시"라고 안내합니다. 날짜는 한국 시간입니다.
- `GITHUB_TOKEN` 환경변수가 있으면 API 호출에 쓰고(`GITHUB_TOKEN=$(gh auth token) python tools/update_recommend.py`), 없으면 비로그인(시간당 60회)으로 부릅니다. 저장소가 사라졌으면(404) 그 저장소만 빼고, 한도 초과·네트워크 오류면 기존 파일을 그대로 두고 종료 코드 1로 끝납니다.
- **매일 자동 갱신**: `.github/workflows/recommend.yml`이 한국 시간 오전 7시(UTC 22:00)에 스크립트를 돌려, 바뀌면 `src/skilldesk/data/skill_recommend.json`만 `main`에 커밋합니다(공식 저장소에서만, `contents: write` 권한만, 수동 실행도 가능). 앱은 자동 업데이트로 받는데, 바뀐 파일이 `skill_recommend.json`뿐이면(`skill_update.DATA_FILES`) 재시작 안내 없이 적용하고 추천 탭은 열 때마다 파일을 다시 읽습니다.
- 새 파일은 `git diff`로 눈으로 훑고 커밋합니다(설치 수는 품질·안전 보증이 아니라서, 이상한 저장소가 상위에 보이면 걸러내는 기준을 손봅니다).

### 카테고리를 새로 만들거나 바꿀 때

1. `src/skilldesk/data/skill_categories.json`에 `{ "name", "about", "keywords", "skills" }`를 추가합니다. 배열 순서가 새 스킬 자동 분류의 우선순위입니다.
2. `skill_viewer.py`의 `CAT_COLORS`에 같은 이름으로 색 4개(라이트 글씨, 라이트 배경, 다크 글씨, 다크 배경)를 추가합니다. 다른 카테고리와 겹치지 않는 색이어야 합니다.
3. `python -m unittest tests.test_viewer`: 색 중복, 대비, 카테고리 짝을 함께 확인합니다.
4. README의 [카테고리 기준](../README.md#카테고리-기준) 표도 고칩니다.

### 색을 바꿀 때

`skill_viewer.py`의 `LIGHT`, `DARK`(앱 전체 색)와 `CAT_COLORS`(카테고리 색)를 고칩니다. 그다음 `test_14_text_is_readable_on_its_background_in_both_themes`로 대비를 확인합니다.

### 화면을 바꿨을 때 스크린샷 다시 찍기

```bash
python docs/make_screenshots.py     # Windows에서 실행, docs/images/*.png를 새로 만듭니다
```

- 임시 폴더에 공개 예시 스킬을 만들어 찍으므로 **내 스킬, 경로, 설정이 사진에 나오지 않습니다**. 내 PC 화면을 직접 캡처해 올리지 마세요.
- 찍는 동안 스크립트가 창을 맨 위로 올려 둡니다. 다른 창을 띄우지 마세요.
- 찍은 뒤 README에 쓰인 이미지가 맞는지 눈으로 확인하고 함께 커밋합니다.

### OS마다 다르게 해야 하는 기능을 더할 때

1. `platform_support.py`에 함수를 만들고 Windows / macOS 분기를 그 안에 둡니다.
2. 두 OS에서 같아야 하는 동작은 `test_viewer.PlatformTest`에 테스트를 추가합니다. 한 OS에서만 의미 있는 검사는 `if plat.WIN:` 또는 `@unittest.skipUnless`로 감쌉니다.
3. PR을 올리면 CI에서 두 OS 결과를 확인합니다.

### 에이전트로 스킬 만들기를 고칠 때

- 실행 인자(`skill_agent.FLAGS`)는 `claude --help`에서 확인한 것만 씁니다(확인한 버전 2.1.302). `--restricted`가 셸·웹 도구를 빼고 파일 도구를 작업 폴더에 가두며, `--permission-prompts none`이 묻는 동작을 거부로 바꿉니다. 인자를 바꾸면 임시 폴더 밖 쓰기가 거부되는지 직접 확인하세요.
- 에이전트 명령 찾기는 `platform_support.find_claude()`(PATH → 설치 프로그램이 두는 폴더)입니다. Codex·Antigravity는 비대화형 실행 방식을 확인하기 전에는 더하지 않습니다.
- 테스트는 실제 에이전트를 실행하지 않습니다. `test_agent.py`의 가짜 실행 파일이나 `generate`를 목으로 바꿔 쓰세요.

### 새 링크 형식을 받게 할 때

`skill_manager.parse_link()`를 고치고 `test_manager.ParseLink`에 허용·거부 예를 함께 추가합니다. **https 이외의 형식은 받지 않는다**는 원칙은 유지하세요(`file:`, `ext::` 같은 git 프로토콜은 명령 실행으로 이어질 수 있습니다).

## 코드 규칙

- **내 PC의 정보를 커밋하지 않습니다.** 이 프로그램은 각자의 PC에 설치된 스킬과 플러그인을 읽는 도구라서, 저장소에는 내 홈 폴더 경로, 계정 이름, 내 PC나 회사에만 있는 스킬 이름이 들어가면 안 됩니다. `skill_notes.json`·`skill_categories.json`에는 누구나 설치할 수 있는 공개 스킬만 넣고, 내 스킬의 설명은 앱의 `⋯ → 용도 설명 수정`(내 PC의 `~/.skill-manager/notes.json`에만 저장됩니다)으로 바꾸세요. `test_privacy.py`가 이를 검사합니다.
- **Tk는 `skill_viewer.py`에만** 둡니다. `skill_manager.py`는 화면 없이 테스트할 수 있어야 합니다.
- **OS 분기는 `platform_support.py`에만** 둡니다.
- **아무것도 영구 삭제하지 않습니다.** 사용자 폴더를 지울 때는 `mgr.trash()`로 옮깁니다. 지우는 것은 우리가 방금 만든 임시물(실패한 clone 등)뿐입니다.
- 사용자에게 보이는 오류는 `ManagerError(한국어 문장)`로 올리고, 화면에서 그대로 보여 줍니다.
- 오래 걸리는 일은 `App.bg()`로 작업 스레드에서 합니다. 위젯은 Tk 스레드에서만 만집니다.
- 글꼴은 `App.f(크기, 굵게)`로만 지정합니다. 기울임꼴은 쓰지 않습니다(테스트가 막습니다).
- 버튼은 `App._button()`(Label 기반), 체크박스는 `App._check()`(Canvas로 그림), 메뉴는 `Popup`을 씁니다. `tk.Button`·`tk.Checkbutton`·`tk.Menu`는 시스템 모양으로 그려져 테마 색이 적용되지 않습니다(macOS는 색을 아예 무시).
- 주석은 코드만 봐서는 알 수 없는 **이유**만 한 줄로 적습니다.
- 알려진 한계가 있는 지름길은 `# shortcut: <한계>, <언제 고칠지>` 형식으로 남깁니다.

## 브랜치·커밋·PR 규칙

- `main`에 직접 커밋하지 않고 `feat/…`, `fix/…` 브랜치에서 작업한 뒤 PR로 합칩니다.
- 커밋 메시지는 `Type: 한국어 요약`(명사로 끝, 마침표 없음). Type은 `Feat`, `Fix`, `Refactor`, `Chore`, `Docs`, `Test`, `Style` 중 하나입니다.
  - 본문은 필요할 때만 `- ` 불릿으로, **왜** 바꿨는지를 개조식으로 적습니다.
  - 예: `Fix: macOS에서 링크로 설치한 스킬이 목록에 안 보이던 문제 수정`
- PR 본문은 **목적 / 변경 내용 / 검증** 세 항목을 씁니다. 검증에는 실제로 실행한 명령과 결과만 적고, 확인하지 못한 것은 "아직 하지 않은 것"으로 따로 적습니다.
- PR은 CI(Windows·macOS)가 통과한 뒤 머지합니다. 머지는 squash 없이 머지 커밋으로 합니다.

## 버전과 릴리스

- 버전은 `VERSION` 파일(`MAJOR.MINOR.PATCH`)이 기준입니다. 앱 자동 업데이트는 커밋 단위로 받으므로 버전은 안내용입니다.
- main에 머지되면 `.github/workflows/release.yml`이 `tools/release.py`를 돌립니다. 마지막 태그 뒤 커밋 제목으로 정합니다.

| 커밋 제목 | 올라가는 것 | 예 |
|---|---|---|
| `Feat:` 이 하나라도 있으면 | 마이너 (1.2.0 → 1.3.0) | `Feat: 추천 탭에 어제 급등 목록 추가` |
| `Fix:`·`Refactor:`·`Style:`만 | 패치 (1.2.0 → 1.2.1) | `Fix: 상세 창 링크가 안 열리던 문제 수정` |
| `Docs:`·`Test:`·`Chore:`만 | 릴리스 없음 | `Docs: 설치 방법 보강` |
| 메이저 | `VERSION`을 직접 고쳐 커밋 | `1.9.0` → `2.0.0` (사람이 결정) |

- 자동으로 `Chore: 버전 X.Y.Z 올림` 커밋을 만들고, `vX.Y.Z` 태그를 붙이고, GitHub 릴리스를 만듭니다. 릴리스 노트는 커밋 제목을 종류별로 모은 것입니다.
- `VERSION`을 직접 올렸으면 그 번호를 그대로 릴리스합니다.
- 처음 머지되는 날은 태그가 없으므로 `VERSION`(1.0.0)으로 첫 릴리스를 만듭니다.
- 테스트: `python -m unittest tests.test_release -v`

### Windows 설치 파일 (exe)

릴리스마다 `SkillDesk-Setup-vX.Y.Z.exe`가 릴리스 Assets에 붙습니다. 같은 `release.yml` 안에서 릴리스를 만든 **뒤** `exe` 작업(windows-latest)이 이어서 돌립니다. macOS 앱은 같은 때 `mac` 작업이 따로 만들고(아래), 공개는 마지막의 `publish` 작업이 합니다. (`GITHUB_TOKEN`으로 만든 태그는 다른 워크플로를 깨우지 않아서, 별도 워크플로가 아니라 같은 워크플로의 다음 작업으로 둡니다.) 방식은 이렇습니다.

1. 방금 만든 **태그의 커밋**을 체크아웃합니다(버전 올림 커밋이 태그보다 먼저라서, 이래야 exe 안의 `VERSION`이 맞습니다).
2. `python tools/build_exe.py`: PyInstaller(`--onedir --windowed`)가 `dist/SkillDesk/`를 만들고, Inno Setup(`tools/skilldesk.iss`)이 `dist/SkillDesk-Setup-vX.Y.Z.exe`로 묶습니다. 관리자 권한 없이 `%LOCALAPPDATA%\Programs\SkillDesk`에 설치하고, 시작 메뉴 바로 가기와 제거 항목(설정 → 앱)을 만듭니다. 바탕 화면 바로 가기와 로그인 때 트레이 시작은 설치 중 고르는 선택 항목입니다.
3. 스모크 테스트: 설치 파일을 조용히 설치 → 파일(글꼴·데이터·`VERSION`)·바로 가기 확인 → `SkillDesk.exe --check` → `--hidden`으로 10초간 실제 시작 → 제거.
4. 릴리스에 `gh release upload`로 붙입니다. 릴리스는 초안으로 만들어 두었다가 `publish` 작업이 `gh release edit --draft=false`로 공개하므로, 공개된 릴리스에는 항상 설치 파일이 있습니다.

- **PR**에서는 같은 빌드와 스모크 테스트를 돌리되 아무것도 올리지 않습니다(3일간 보관되는 Actions 산출물만 남깁니다. 손으로 설치해 보려면 거기서 받으세요).
- **exe 빌드가 실패하면** 릴리스는 초안으로 남고(공개되지 않아 앱의 새 버전 알림에도 안 보입니다), Actions 실행이 실패로 표시됩니다. 원인을 고친 뒤 아래 "이미 있는 태그에 붙이기"로 다시 돌리면 붙이고 공개합니다.
- **이미 있는 태그에 붙이기**: Actions → Release → Run workflow → 태그 입력(예: `v1.1.0`). 태그의 `VERSION`과 일치해야 하고, 그 태그에 `tools/build_exe.py`가 있어야 합니다(이 기능이 들어간 v1.1.0 이전 태그에는 없어서 실패합니다).
- **로컬에서 만들기**(Windows): `python -m pip install pyinstaller==6.11.1` 후 `winget install JRSoftware.InnoSetup`, 그리고 `python tools/build_exe.py` (`--app-only`는 PyInstaller만). 결과는 `dist/`(git이 무시합니다).
- **exe 안의 경로**: 코드는 `Path(__file__)`을 쓰되 PyInstaller가 `skilldesk/data`, `skilldesk/assets`를 같은 상대 위치에 풀어 두므로 그대로 동작합니다. `VERSION`만 번들 맨 위(`sys._MEIPASS`)에 두고 `update_check.current_version()`이 읽습니다. exe·macOS 앱에서는 git 자동 업데이트가 꺼지고(`skill_update.eligible`), 아래 `self_update`가 대신합니다.
- **코드 서명은 하지 않습니다.** SmartScreen·백신 경고가 있을 수 있어 README와 릴리스 노트에 안내합니다. 서명하려면 인증서를 사서 `iscc`의 `SignTool`과 PyInstaller 결과 서명을 넣어야 합니다.

### macOS 설치 파일 (dmg)

릴리스마다 `SkillDesk-vX.Y.Z-macOS-arm64.dmg`도 붙습니다(`mac` 작업, macos-latest). 흐름은 exe와 같습니다: 태그의 커밋 체크아웃 → `python tools/build_mac.py` → 스모크 테스트 → 업로드.

1. `build_mac.py`: `skill_viewer.py --make-icns`로 아이콘을 그리고, PyInstaller(`--onedir --windowed`)가 `dist/SkillDesk.app`을 만듭니다. 그 `Info.plist`에 버전을 적고 **임시(ad-hoc) 서명**(`codesign --force --deep -s -`)을 한 다음, `hdiutil`로 `.app`과 `/Applications` 바로 가기를 담은 dmg를 만듭니다.
2. 스모크 테스트: dmg를 열어 앱을 복사 → 서명 확인 → 글꼴·데이터·`VERSION` 확인 → `SkillDesk --check` → `--hidden`으로 10초간 실제 시작하고 메뉴 막대 프로세스(`--tray-child`)가 떠 있는지 확인.
3. **메뉴 막대 프로세스**: macOS에서는 pystray를 별도 프로세스로 띄웁니다(`skill_viewer.tray_child_cmd`). 묶인 앱은 `sys.executable`이 앱 자신이라 스크립트 경로 없이 `--tray-child`만 넘깁니다.
4. **CPU는 arm64만**입니다. universal2로 만들려면 Pillow·pyobjc 같은 모든 바이너리 패키지와 Python 자체가 두 구조를 다 가져야 하는데, pip는 보통 그 PC의 구조에 맞는 wheel만 받고(Pillow는 구조별로 따로 배포됩니다) 확인할 방법이 CI뿐이라 시도하지 않았습니다. Intel Mac은 소스 설치를 안내합니다(README, 릴리스 노트). Intel용을 더하려면 `macos-15-intel`에서 같은 `mac` 작업을 하나 더 돌리면 됩니다(dmg 이름에 CPU가 들어가 겹치지 않습니다). 릴리스 노트의 링크도 함께 고치세요.
5. **서명·공증은 하지 않습니다.** Apple Developer ID(유료)가 없어서 처음 실행 때 "확인되지 않은 개발자" 경고가 뜨고, README와 릴리스 노트에 허용 방법을 적었습니다. 공증하려면 Developer ID로 `codesign --options runtime`을 하고 `notarytool`로 제출한 뒤 `stapler`를 붙이도록 `build_mac.py`와 워크플로를 바꿔야 합니다.
6. 로컬(macOS): `python3 -m pip install -r requirements.txt pyinstaller==6.11.1` 후 `python3 tools/build_mac.py` (`--app-only`는 dmg 없이). 개발 PC가 Windows라면 CI의 PR 실행이 유일한 확인이고, 실제 Mac에서 열어 본 것이 아닙니다. PR 실행이 남기는 `SkillDesk-macOS` 산출물(3일)로 Mac에서 직접 받아 볼 수 있습니다.

### 릴리스 공개 (`publish`)

`exe`·`mac`이 끝나면 `publish` 작업이 릴리스를 초안에서 공개로 바꿉니다.

- **Windows exe는 필수**: 실패하면 공개하지 않고 초안으로 남깁니다.
- **macOS dmg는 아직 필수가 아님**: 실패해도 공개하되, 릴리스 노트 끝에 "macOS 설치 파일이 없습니다" 안내를 붙입니다(다음 실행에서 dmg가 붙으면 그 안내는 지워집니다). 안정되면 `release.yml`의 `publish` 작업에서 `MAC_REQUIRED: "true"`로 바꾸세요. 그러면 dmg가 없을 때도 초안으로 남습니다.
- **PR 실행**에서는 `exe`와 `mac` 모두 항상 돌고(관련 파일이 바뀐 PR), 어느 쪽이든 실패하면 PR의 확인이 빨갛게 됩니다. 그래서 머지 전에 잡힙니다. `publish`는 PR에서 돌지 않습니다.
- **`SHA256SUMS.txt`는 필수**: `publish`의 첫 단계가 릴리스에 붙은 `SkillDesk-*` 파일을 받아 `sha256sum`으로 목록을 만들어 같은 릴리스에 올립니다. Windows 설치 파일이 없거나 이 단계가 하나라도 실패하면 공개하지 않고 초안으로 남깁니다(목록 없는 릴리스를 공개하면 설치된 앱이 자동 업데이트를 못 하고 알림으로 물러서기만 하므로, 막히는 쪽을 택했습니다). 복구는 아래 "이미 있는 태그에 붙이기"로 같은 태그를 다시 돌리는 것입니다.
- **이미 있는 태그에 붙이기**(Run workflow)도 `exe`·`mac`·`publish`를 모두 돌립니다. 빌드 스크립트가 없는 오래된 태그는 "tools/build_mac.py is missing"처럼 이유를 적고 끝납니다(그 태그의 exe가 만들어지면 mac 없이 공개하고 노트에 안내를 붙입니다).
- 워크플로 문법은 `ci.yml`의 `workflows` 작업(actionlint)이 PR마다 검사합니다.

### 설치 파일 앱의 자동 업데이트 (`self_update.py`)

exe·dmg로 설치한 앱(`sys.frozen`)은 git이 없으므로 새 **설치 파일을 받아 바꿉니다**. git 클론은 `skill_update.py`, 소스 ZIP과 이 방식을 쓸 수 없는 위치의 설치는 `update_check.py`의 알림만 씁니다. 화면 쪽은 `App.auto_update` → `_self_update` → `_prepared` → `install_update`입니다(진행 중 표시는 `_updating`·`_applying`, 받아 둔 결과는 `App.staged`, 알약은 `새 버전 · 지금 업데이트`). 설정 `auto_update`가 git 설치와 같은 스위치입니다: 꺼져 있으면 내려받지 않고 알림(`받기`)만 만들고, 사용자가 `SkillDesk 업데이트 확인`을 직접 누르면 꺼져 있어도 내려받습니다.

**흐름**

1. `prepare()`(작업 스레드): 릴리스 API → 더 높은 `X.Y.Z`인가 → 이 설치가 스스로 바꿀 수 있는가(`unsupported_reason`: Windows는 설치 폴더에 `unins000.exe`가 있고 쓸 수 있을 것, macOS는 `.app`이 `/Volumes`·AppTranslocation 밖이고 부모 폴더에 쓸 수 있을 것) → 자산 목록에 이 플랫폼의 설치 파일과 `SHA256SUMS.txt`가 있는가 → 임시 폴더 `skilldesk-update-…`에 둘을 받기 → SHA-256 대조 → (macOS) dmg를 `hdiutil attach -nobrowse -readonly`로 열어 `SkillDesk.app`을 임시 폴더로 `ditto`하고 닫은 뒤 번들 ID·버전·`codesign --verify`를 확인. 어느 단계든 막히면 폴더를 지우고 알림으로 물러섭니다.
2. `Staged("staged")`가 되면 창이 보일 때는 알약, 숨어 있을 때는 바로 `install_update(ask=False)`입니다. 저장하지 않은 SKILL.md 편집이 있으면(`_unsaved_edits`) 자동으로는 하지 않고, 알약을 누르면 `discard_ok()`로 묻습니다.
3. `apply()`:
   - **Windows**: 설치 파일의 SHA-256을 다시 확인하고 `SkillDesk-Setup-vX.Y.Z.exe /VERYSILENT /SUPPRESSMSGBOXES /NORESTART /LOG=… /RELAUNCH=hidden|shown`을 떼어 낸 프로세스로 실행(`shell=False`)한 뒤 앱이 닫힙니다. `AppId`가 같아 같은 폴더에 덮어 설치되고(이미 실행 중이면 `CloseApplications=yes`가 닫습니다), 조용한 설치는 `skipifsilent` 항목을 건너뛰므로 `skilldesk.iss`에 `[Run]` 항목 두 개를 더 두어 `/RELAUNCH` 값(`{param:RELAUNCH|}`)을 `Check: Relaunch(...)`로 읽고 새 버전을 다시 켭니다. **설치 프로그램에 재시작을 맡긴 이유**: PyInstaller 앱에는 설치 폴더 밖에서 돌 수 있는 인터프리터가 없고, 설치 폴더 안의 도우미는 설치가 덮어쓰려는 파일을 붙잡으며, PowerShell 같은 외부 해석기는 정책으로 막힌 PC가 있기 때문입니다. 그 대가로 설치 프로그램이 도중에 실패하면 아무도 앱을 다시 켜 주지 않습니다(아래 "복구").
   - **macOS**: `/bin/sh`가 `MAC_SCRIPT`(`apply-update.sh`)를 실행합니다. 값(앱 PID, 새 앱, 설치된 앱, 로그, hidden|shown)은 위치 인자로만 넘기고 스크립트 본문에 섞지 않습니다. 앱이 끝나길 기다렸다가(최대 60초) 새 앱을 `….update-new`로 복사 → 옛 앱을 `….update-old`로 옮김 → 새 앱을 제자리로 → 옛 앱 삭제. 실패하면 옛 앱을 되돌리고, 어느 쪽이든 `open`으로 앱을 다시 엽니다.
   - 시작에 성공하면 `~/.skill-manager/update-attempt.json`에 `{from, to}`를 적습니다. 같은 `to`가 같은 `from` 그대로 남아 있으면(설치가 끝나지 않음) 그 버전은 저절로 다시 시도하지 않고 알림으로 물러섭니다. 직접 `업데이트 확인`을 누르거나 더 새 릴리스가 나오면 다시 시도합니다.
4. 앱을 켠 뒤 첫 확인에서 `skilldesk-update-*` 폴더를 모두 지우고, 이후에는 1시간 지난 것을 지웁니다.

**보안: 한 일과 못 하는 일**

- 응답은 믿지 않습니다. 릴리스 응답에서는 태그(`vX.Y.Z`)와 자산 *이름*만 읽고, 받을 주소와 파일 이름은 앱이 태그로 직접 만듭니다(`browser_download_url`은 읽지 않습니다). `https://github.com/<공식 저장소>/releases/download/v…`만 받고, 리다이렉트는 한 단계마다 `objects.githubusercontent.com`·`release-assets.githubusercontent.com`인지 확인합니다. 200MB(목록은 64KB)·연결 20초·전체 10분 제한. 해시 목록 형식이 이상하거나 같은 파일이 다른 값으로 두 번 나오면 통째로 버립니다.
- 받은 파일은 **검증 전에 실행하지 않습니다.** 실행하는 것은 임시 폴더 안의 검증된 파일 하나이고, 인자는 고정이며 사용자나 서버가 준 문자열은 인자에도 셸에도 들어가지 않습니다.
- **한계**: 이것은 전송 중 손상·변조, 엉뚱한 주소로의 유도, 응답 형식 속임을 막을 뿐입니다. 코드 서명이 없고 `SHA256SUMS.txt`가 설치 파일과 같은 릴리스에 있으므로, 저장소나 릴리스에 쓸 수 있는 사람이 침해되면 설치 파일과 목록을 함께 바꿔 올릴 수 있고 앱은 막지 못합니다. 더 막으려면 Windows Authenticode·Apple Developer ID로 서명하거나, 앱에 공개키를 넣어 목록에 분리 서명을 붙여야 합니다.
- 앱이 받은 파일에는 "인터넷에서 받음"(Windows Mark-of-the-Web)·격리(macOS quarantine) 표시가 붙지 않아 SmartScreen·Gatekeeper가 뜨지 않습니다. 그 몫을 해시 확인이 합니다. macOS는 임시(ad-hoc) 서명이 빌드마다 달라 TCC(파일·폴더 접근) 허용을 버전마다 다시 물을 수 있습니다.
- 검증과 실행 사이에 같은 사용자 권한의 프로그램이 임시 파일을 바꿀 수 있지만, 그런 프로그램은 앱 폴더도 바꿀 수 있어 막을 대상이 아닙니다. Windows는 실행 직전에 해시를 한 번 더 봅니다.

**시험**

- 단위 테스트 `tests/test_self_update.py`(로컬 가짜 서버)와 `test_viewer`의 `test_03i2b…d`(알약·숨김·저장 안 한 편집·실패 시 물러서기).
- PR의 CI에서만, `exe` 작업이 `tools/update_e2e.ps1`로 **실제 제자리 업그레이드**를 시험합니다: 설치 파일을 `--version 0.0.1`로 한 번 더 만들어 설치 → 가짜 GitHub(로컬 `http.server`, 환경 변수 `SKILLDESK_UPDATE_TEST_ORIGIN`)를 가리키게 하고 `--hidden`으로 시작 → 앱이 스스로 받아 바꾸고 새 버전이 숨은 채로 다시 켜지는지 확인, 그다음 해시를 틀리게 해서 아무것도 실행되지 않고 옛 앱이 계속 도는지 확인. `mac` 작업은 같은 시험을 `tools/update_e2e.sh`로 합니다. `publish`에는 영향을 주지 않으므로 릴리스(push)에서는 돌지 않습니다.
- `SKILLDESK_UPDATE_TEST_ORIGIN`은 `http://127.0.0.1:포트`(또는 localhost)일 때만 받아들입니다. 그 환경 변수를 정할 수 있는 쪽은 이미 같은 사용자 권한이라, 이 시험 장치가 새 공격 경로를 열지는 않습니다.

**복구**

- Windows 설치가 도중에 실패해 앱이 안 켜지면: 시작 메뉴의 SkillDesk를 다시 켜 보고, 안 되면 릴리스의 설치 파일을 직접 실행해 덮어 설치합니다. 원인은 `~/.skill-manager/update-setup.log`(Inno Setup 기록)에 있습니다.
- macOS: `~/.skill-manager/update-apply.log`. 임시 파일이 남았다면 `/Applications/SkillDesk.app.update-old`가 옛 앱입니다.
- 자동 업데이트를 못 하는 환경이 있으면 설정에서 끄면 알림(`받기`)만 남습니다.

**확인하지 못한 것**: 실제 Mac(위의 모든 macOS 동작은 CI의 `macos-latest`뿐), 깨끗한 PC·여러 사용자·한글 사용자 폴더에서의 설치, 백신·SmartScreen의 반응(앱이 받은 설치 파일을 백신이 막는 경우), 설치가 도중에 실패했을 때의 상태(재현하지 않았습니다. 위 복구 안내는 설계에서 나온 것입니다).

## 알려진 한계

- **프로젝트 하네스의 범위**: 세트에는 스킬 이름만 담습니다. `harness.json`의 세트는 키가 있는 딕셔너리(`{"skills": [...]}`)이고 `version` 필드가 있어서 `CLAUDE.md`·훅·MCP 설정을 더하려면 키를 늘리면 되지만, 무엇을 담을지는 아직 정해지지 않았습니다(구조 가이드는 있는지만 보여 줍니다).
- **프로젝트 폴더의 Windows 링크**: Claude Code 문서는 프로젝트 `.claude/skills`의 심볼릭 링크만 명시하고 junction은 언급하지 않습니다. 사용자 폴더(`~/.claude/skills`)에서 이 앱이 쓰던 junction과 같은 방식이라 같은 동작을 기대하지만, 프로젝트 폴더에서 실제 Claude Code로 확인한 적은 없습니다.
- **macOS 창 위치**: Tk가 메뉴 막대·Dock 크기를 알려 주지 않아 위 25px, 아래 90px을 비운 근사값으로 놓습니다.
- **브랜치 이름에 `/`가 있는 링크**: `tree/feature/x/...`는 브랜치를 `feature`로 읽습니다. 저장소 루트 링크를 쓰세요.
- **삭제한 스킬의 clone**: `~/.skill-manager/sources/`에 남겨 둡니다(다시 설치할 때 빠르도록). 필요하면 직접 지워도 됩니다.
- **한국어 용도 설명**: `skill_notes.json`에 있는 스킬만 한국어이고, 처음 보는 스킬은 원문 첫 문장을 보여 줍니다.
- **실제 macOS 기기**: CI에서 테스트는 통과하지만, 메뉴 막대 아이콘과 `.app` 실행은 실제 맥에서 직접 확인한 적이 아직 없습니다.
