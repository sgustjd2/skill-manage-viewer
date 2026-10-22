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
python -m unittest test_manager test_viewer test_install test_update     # 모두 OK
```

## 실행과 디버깅

```bash
python skill_viewer.py            # 콘솔에 오류가 그대로 보여 개발할 때 편합니다
pythonw skill_viewer.py           # Windows: 콘솔 없이 (실사용)
python skill_viewer.py --hidden   # 트레이로만 시작
python skill_viewer.py --check    # 창 없이 에이전트별 개수·카테고리·출처만 출력
python skill_viewer.py --make-icon   # skill_viewer.ico 다시 만들기
python skill_viewer.py --make-icns   # macOS 아이콘(skill_viewer.icns) 만들기
```

- 앱은 **한 번에 하나만** 뜹니다(`platform_support.already_running`, 임시 폴더의 `skill-catalog.lock` 파일 잠금). 다시 실행하면 새 창 대신 떠 있는 앱에 "창을 보여 달라"는 쪽지(임시 폴더의 `skilldesk.show`, `platform_support.ask_running_copy_to_show`)를 남기고 끝납니다. 떠 있는 앱은 0.1초마다 쪽지를 확인해(`App._pump`) 창을 띄웁니다. 그래서 고친 코드를 확인하려면 **트레이 메뉴 → 종료**로 이전 것을 끄고 다시 실행하세요(이전 앱이 새 코드가 아니면 쪽지를 읽지 못합니다).
- 시작에 4~8초가 걸리는 것은 정상입니다. 세 에이전트의 스킬 폴더를 읽고 git 스킬의 커밋을 조회하기 때문입니다.
- 실제 `~/.claude` 등을 건드리지 않고 화면만 보고 싶으면 `python docs/make_screenshots.py`를 참고하세요. 임시 폴더에 예시 스킬을 만들어 앱을 띄웁니다.

## 폴더 구조

```
skill-manage-viewer/
├─ skill_viewer.py          화면: 창, 탭, 카테고리 목록, 스킬 줄, 메뉴, 추가 대화상자, 트레이, 색·글꼴
├─ skill_manager.py         일: 링크 해석, git 받기, 설치·업데이트·삭제·생성, SKILL.md 읽기·저장(`read_skill_md`/`save_skill_md`), 메모·설정 저장 (Tk 없음)
├─ skill_usage.py           Claude Code 대화 기록(.jsonl)에서 달별 스킬 호출 횟수 집계, 최근 2개월 안 쓴 스킬(정리 후보) 계산 (Tk 없음, 파일별 캐시)
├─ platform_support.py      OS마다 다른 것: 폴더 링크, 파일 열기, 화면 영역, 글꼴 로드, 중복 실행 방지
├─ install.py               시작 메뉴 바로 가기 / macOS .app 만들기·지우기
├─ skill_update.py          앱 자신의 자동 업데이트: 안전 조건 확인, fast-forward, 새 코드 시험 실행, 실패 시 되돌리기
├─ skill_categories.json    카테고리 정의: 이름, 기준 문장(about), 소속 스킬, 새 스킬 분류용 keywords
├─ skill_notes.json         스킬별 한국어 한 줄 용도 (이름 → 설명)
├─ skill_recommend.json     추천 탭에 보여 줄 skills.sh 인기·요즘 뜨는 목록의 스냅샷 (tools/update_recommend.py가 만듦)
├─ tools/                   check.py: 푸시 전 확인 한 번에 · update_recommend.py: 추천 스냅샷 다시 만들기 (앱은 쓰지 않음)
├─ skill_viewer.ico         앱 아이콘 (Windows)
├─ fonts/                   Pretendard Regular·Bold + 라이선스
├─ start.bat / start.command  더블클릭 실행 (Windows / macOS)
├─ requirements.txt         실행에 필요한 패키지
├─ test_manager.py          skill_manager 테스트
├─ test_viewer.py           화면·플랫폼·카테고리·탐색 테스트
├─ test_install.py          install.py 테스트
├─ test_recommend.py        추천 스냅샷: 페이지 파싱·거르기·실패 시 보존(`tools/update_recommend.py`)과 `load_recommended()`
├─ docs/
│  ├─ DEVELOPMENT.md        이 문서
│  ├─ make_screenshots.py   README 스크린샷 다시 찍기
│  └─ images/               README 스크린샷
└─ .github/workflows/ci.yml Windows·macOS CI
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
python -m unittest test_manager test_viewer test_install -v
python -m unittest test_viewer.PanelTest.test_03_tabs_search_rail -v   # 하나만
```

- 테스트는 **임시 폴더만** 씁니다. `mgr.STORE`, `mgr.AGENT_DIRS` 등을 바꿔 끼워서 실제 `~/.claude`, `~/.codex`, `~/.skill-manager`에는 쓰지 않습니다.
- `test_viewer`는 실제로 창을 띄웠다 닫습니다. 실행 중에는 화면이 잠깐 깜빡이고, 마우스를 움직이지 않는 편이 좋습니다. 키보드·마우스 입력을 흉내 내지 않고 코드로만 조작합니다.
- Windows에서는 `%TEMP%`에 확인용 스크린샷(`t3_list.png` 등)도 남깁니다.

| 파일 | 확인하는 것 |
|---|---|
| `test_manager.py` | 링크 해석(허용·거부 형식), 로컬 git 저장소로 설치·업데이트·재연결·삭제, 이름 충돌과 교체, 휴지통 이동, 새 스킬 생성, 메모 저장, 자동 업데이트 스크립트 목록 정리 |
| `test_viewer.py` – `PanelTest` | 트레이·최소화·화면 가운데 배치, 출처 표시, 탭·검색·카테고리 선택, 메뉴, 용도 수정, 추가 대화상자(생성·링크 설치·충돌·교체), 업데이트 확인·적용, 삭제, 테마 저장, 기울임 없는 글꼴, Pretendard 사용, **모든 글씨의 명도 대비**(라이트·다크, 마우스 올림·선택·비활성 포함) |
| `test_viewer.py` – `PlatformTest` | 폴더 링크 만들기·지우기, 중복 실행 방지, 다시 실행할 때 남기는 "창을 보여 달라" 쪽지(한 번만 읽힘·오래된 쪽지 무시), 우클릭 이벤트, 트레이 명령 허용 목록 |
| `test_viewer.py` – `CategoryTest` | 한 스킬이 두 카테고리에 들어가지 않는지, 설명 있는 스킬의 카테고리 누락, 색·기준 문장 짝 |
| `test_viewer.py` – `DiscoveryTest` | 다른 PC 상황: 링크로 설치된 스킬, 순환 링크, 이상한 `SKILL.md`, 없는 에이전트 폴더, 깨진 플러그인 파일, 환경변수 |
| `test_install.py` | macOS `.app` 구성과 제거 범위(모든 OS), Windows 바로 가기 내용(Windows만) |
| `test_recommend.py` | 추천 스냅샷: 실제 페이지 모양의 작은 예시로 파싱, GitHub 아닌 출처·저장소별 상한·같은 이름·설치 수 하한 거르기, 형식이 바뀌면 실패, 실패하면 기존 파일 유지, 요청이 두 공개 페이지뿐인지, `load_recommended()`가 없거나 깨진 파일을 빈 목록으로 보는지 (네트워크 없음) |
| `test_usage.py` | 호출 횟수 집계: 도구 호출과 `/이름`, 달별 합계, 이어받은 대화의 중복, 캐시 재사용, 깨진 입력, 플러그인 접두어, 정리 후보(최근 2개월 기준·해가 바뀔 때·기록이 없을 때), 기록이 시작된 시점 |
| `test_update.py` | 앱 자동 업데이트: 공식 저장소·`main`·수정 없음일 때만 동작하는지, fast-forward만 하는지, 의존성 설치 조건, **실패하면 정확히 이전 커밋으로 되돌리는지**(임시 저장소 사용) |

**명도 대비 테스트**(`test_14_…`)는 화면에 실제로 그려진 모든 글씨와 배경 색 조합의 대비를 계산해 WCAG AA(일반 4.5:1, 큰 글씨·비활성 3:1)를 넘는지 봅니다. 색을 바꾸면 반드시 이 테스트를 돌리세요. 실패하면 어느 글씨가 몇 대 몇인지 알려 줍니다.

## 린트와 타입 검사

```bash
python -m ruff check . --line-length 140 --select E,F,W,B,UP,SIM
python -m ruff format --line-length 140 test_viewer.py test_manager.py test_install.py install.py
python -m pyright skill_manager.py skill_viewer.py platform_support.py install.py
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

1. `skill_categories.json`에서 맞는 카테고리의 `skills` 배열에 이름을 넣습니다. **한 곳에만** 넣으세요.
2. `skill_notes.json`에 `"이름": "한 줄 용도"`를 넣습니다. 짧게, "무엇을 할 때 쓰는지"가 드러나게 씁니다.
3. `python -m unittest test_viewer.CategoryTest`로 중복·누락을 확인합니다.

플러그인 스킬은 `superpowers:brainstorming`처럼 앞에 플러그인 이름이 붙어도 뒤의 이름(`brainstorming`)으로 찾습니다.

### 추천 목록을 새로 받을 때

`python tools/update_recommend.py` (표준 라이브러리만 씁니다). skills.sh의 `/`와 `/trending`을 **한 번씩만** 받아 `skill_recommend.json`을 새로 씁니다. 앱은 이 파일만 읽고 네트워크를 쓰지 않습니다.

- 목록은 페이지에 실린 `initialSkills` JSON에서 읽습니다(화면 마크업이 아님). `/api/`·`/search`는 `robots.txt`가 막으므로 부르지 않습니다.
- 거르는 기준은 스크립트 위쪽 상수입니다: GitHub `owner/repo`만, 저장소당 `PER_REPO`개, 같은 스킬 이름은 한 번, 설치 `MIN_INSTALLS` 이상, 목록당 `SIZE`개.
- 받지 못했거나 페이지 형식이 바뀌어 `initialSkills`를 못 찾으면 **기존 파일을 그대로 두고** 종료 코드 1로 끝납니다. 이 경우 `parse_skills()`를 새 형식에 맞게 고치고 `test_recommend.py`의 예시를 갱신하세요.
- 새 파일은 `git diff`로 눈으로 훑고 커밋합니다(설치 수는 품질·안전 보증이 아니라서, 이상한 저장소가 상위에 보이면 걸러내는 기준을 손봅니다).

### 카테고리를 새로 만들거나 바꿀 때

1. `skill_categories.json`에 `{ "name", "about", "keywords", "skills" }`를 추가합니다. 배열 순서가 새 스킬 자동 분류의 우선순위입니다.
2. `skill_viewer.py`의 `CAT_COLORS`에 같은 이름으로 색 4개(라이트 글씨, 라이트 배경, 다크 글씨, 다크 배경)를 추가합니다. 다른 카테고리와 겹치지 않는 색이어야 합니다.
3. `python -m unittest test_viewer`: 색 중복, 대비, 카테고리 짝을 함께 확인합니다.
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

## 알려진 한계

- **macOS 창 위치**: Tk가 메뉴 막대·Dock 크기를 알려 주지 않아 위 25px, 아래 90px을 비운 근사값으로 놓습니다.
- **브랜치 이름에 `/`가 있는 링크**: `tree/feature/x/...`는 브랜치를 `feature`로 읽습니다. 저장소 루트 링크를 쓰세요.
- **삭제한 스킬의 clone**: `~/.skill-manager/sources/`에 남겨 둡니다(다시 설치할 때 빠르도록). 필요하면 직접 지워도 됩니다.
- **한국어 용도 설명**: `skill_notes.json`에 있는 스킬만 한국어이고, 처음 보는 스킬은 원문 첫 문장을 보여 줍니다.
- **실제 macOS 기기**: CI에서 테스트는 통과하지만, 메뉴 막대 아이콘과 `.app` 실행은 실제 맥에서 직접 확인한 적이 아직 없습니다.
