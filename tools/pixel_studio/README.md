# Pixel Studio 사용 가이드

독립 Python 도구의 조작과 변환 규약입니다. 빠른 시작은 루트 README를 참고하세요.

## 실행

저장소 루트의 `Start-Pixel-Studio.cmd`를 실행합니다. 빌드가 있으면 `out/pixel-studio/PixelStudio/PixelStudio.exe`를 실행합니다. 배포할 때는 EXE만이 아니라 **PixelStudio 폴더 전체**를 복사합니다.

소스 실행과 재빌드:

```powershell
python -m pip install -r tools/pixel_studio/requirements.txt
python -m tools.pixel_studio
# 실행 파일 빌드에는 PyInstaller가 추가로 필요합니다.
python -m pip install pyinstaller
powershell -ExecutionPolicy Bypass -File tools/pixel_studio/build.ps1
```

## 독립 도구 사용

1. PNG 또는 폴더를 가져오거나 에셋 카탈로그를 엽니다. 입출력은 PNG만 지원합니다.
2. 같은 객체의 방향·프레임을 다중 선택하고 같은 그룹으로 지정합니다. 카탈로그의 `pixel_group`도 가져옵니다.
3. 고정 W/H(예: 100×100) 또는 원본 픽셀 간격을 선택합니다. 중앙 화면에서 원본/결과, 노멀, 조명을 비교합니다. 내용 확대는 화면 표시만 확대하며 출력 여백을 바꾸지 않습니다.
4. 팔레트는 그룹 전체에서 자동 생성합니다. 색을 직접 편집·추가·삭제하거나 잠글 수 있습니다. 확정된 팔레트는 프레임 추가 시 유지하며, 재분석을 누를 때만 잠기지 않은 색을 다시 정합니다.
5. 프로젝트를 저장하고 선택 항목 또는 전체를 출력합니다. 진행률·취소·실패 항목 재시도를 지원합니다. 변경되지 않은 결과는 건너뜁니다.

디더링, 안쪽 1px 외곽선, 투명도 임계값/반투명 보존, 비율 유지/늘리기, 노멀 단계화 및 X/Y 반전, 그룹 재생·타일 반복 미리보기를 제공합니다. 그룹과 개별 에셋 설정 덮어쓰기는 JSON 편집창에서 지정합니다. 영역·피벗·메타데이터도 유지합니다.

미리보기는 원본과 결과에 동일한 좌표·배율을 사용합니다. 휠로 확대/축소하고 **마우스 오른쪽 버튼을 누른 채 드래그**하면 양쪽 이미지가 함께 움직입니다. `화면 맞춤`은 확대와 이동을 함께 초기화합니다. 조명 보기의 왼쪽 드래그는 광원 이동입니다.

`색 추가` 창의 `⌖ 스포이트`를 누르면 창이 잠시 숨겨집니다. 원본 미리보기에서 불투명한 부분을 왼쪽 클릭하면 그 PNG의 RGB를 읽어 색 선택창으로 돌아옵니다. 이동·확대·반복 표시 상태에서도 사용할 수 있고, Esc를 누르면 추출 없이 창으로 돌아옵니다. OK를 누르면 기본적으로 가장 비슷한 기존 팔레트 색을 교체해 즉시 미리보기와 출력에 반영합니다. 선택 색 교체 또는 `팔레트에 추가만`도 고를 수 있습니다. 추가만 선택하면 원본 색과 멀어서 실제 변환에 쓰이지 않는 색이 있을 수 있습니다.

단축키: **Ctrl+S** 프로젝트 저장, **Ctrl+Z** 실행 취소, **Ctrl+Shift+Z** 다시 실행.

`8-bit/16-bit`라는 시대별 미술 스타일과 파일 비트 깊이는 같은 개념이 아닙니다. 프리셋은 해상도와 색 수의 조합이며, 실제 용량은 이미지 내용에도 좌우됩니다. 이진 투명도 결과는 인덱스 PNG, 반투명 결과는 RGBA PNG로 저장합니다. 노멀은 색 팔레트에 넣지 않고 벡터로 재정규화해서 RGBA PNG로 저장합니다. PNG 용량 감소와 게임 엔진의 텍스처 메모리 감소는 별개이며, 출력 보고서의 RGBA8 추정치는 밉맵 없는 경우입니다.

배치 처리에는 에셋 수 제한을 두지 않았습니다. 한 장씩 변환하고 팔레트 분석 표본도 제한하므로 300개를 등록해 같은 규약으로 처리할 수 있습니다. 실제 검증은 요청대로 4개만 사용했으며 300개 성능 측정을 했다는 의미는 아닙니다.

```powershell
python -m tools.pixel_studio --project project.pixel.json --export output
python -m tools.pixel_studio --project project.pixel.json --export output --ids actor_front actor_back
```

출력에는 PNG 외에 `pixel-manifest.json`(피벗·영역·메타데이터·용량)과 `pixel-report.json`(성공·실패)이 포함됩니다. 원본 파일은 덮어쓰지 않습니다.

## Godot 연동 범위

원 프로젝트의 월드 에디터 GPU 미리보기 통합은 이 독립 저장소에 포함하지 않았습니다. 아래 CPU 규약은 Python 도구의 변환 구현을 설명합니다.

## 공유 규약과 검증

`pixel.asset.project` version 1, algorithm `pixel-center-rgb709-v1`을 사용합니다. 설정 우선순위는 프로젝트 → 그룹 → 에셋입니다. 그룹에는 `palette`, `locked`(색 인덱스), `settings`가 있고 에셋에는 `id`, `source`, `group`, 선택적인 `normal`, `region`, `pivot`, `metadata`, `settings`가 있습니다. 저장된 경로는 절대경로이며 상대경로를 읽을 때는 프로젝트 파일 위치를 기준으로 해석합니다.

색상은 픽셀 중심 최근접 표본, Rec.709 가중 sRGB 거리, 결정적인 팔레트 분할 및 Bayer 디더링을 사용합니다. 원 개발본은 Python CPU 출력과 Godot GPU 미리보기에서 같은 규약을 사용했습니다. 이 저장소는 Python CPU 구현을 제공합니다. 일반 이미지의 자동 축소가 수작업 픽셀 아트의 형태 교정까지 대신하지는 않습니다.

```powershell
python -m unittest tools.pixel_studio.test_core -v
python -m unittest tools.pixel_studio.test_ui -v
```

개발 당시 4개 실물 프레임으로 CPU/GPU 색상 일치, 노멀·투명도, PNG 왕복, 증분 출력·실패·취소·원본 보존을 검증했습니다. 맵에서는 간격 4/12 표시와 미리보기 해제 후 원본 복원을 검사했습니다. 일회성 샘플 생성기와 GPU/맵 캡처 스크립트는 정리했고, 위 회귀 테스트는 유지합니다. 당시 검사는 이번 정리 후 GPU/GUI를 재검증했다는 의미가 아닙니다.
