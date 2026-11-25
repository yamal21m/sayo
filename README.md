# sayo
Sayo-Radar — 6대 자산 바겐세일 신호등 (https://sayo-umber.vercel.app)

## 구성
- `index.html` — 화면 전체(정적 HTML + 인라인 JS). 시세는 `data.json` 을 받아 그립니다.
- `style.css` — **빌드해 둔 Tailwind CSS**. CDN 스크립트(`cdn.tailwindcss.com`, 약 400KB JS)를 쓰지 않아 첫 페인트가 빠릅니다.
- `update_data.py` — GitHub Actions 가 하루 3회(07·16·23 KST) 실행해 `data.json` 을 갱신합니다.

## 점수 기준 (2026-11-26 개편)
- 신호등: **80점 이상 🟢 지금 사요 / 40~79점 🟡 나눠 사요 / 40점 미만 🔴 참아 봐요** (`index.html` 의 `GREEN_MIN = 80`, `getSignalInfo`).
- 100점 = `s1` 35(200일선 대비) + `s2` 30(고점 대비 할인율·RSI) + `s3` 20(1년 박스권 위치) + `s4` 15(공포 심리).
- `s4` 는 자산군별 기준: 크립토=크립토 공포탐욕(+마이너스 펀딩비), 미장 주식·리츠=CNN 공포탐욕, 국장=코스피 20일 실현변동성, 채권·환율·귀금속·원자재=중립 고정(5점). 아이템마다 `s4Label`·`s4Basis` 로 근거를 남기고 모달에 표시합니다.
- 채점 원지표(`maDiff`, `mddPct`, `rsi`, `boxPos`)도 `data.json` 에 저장해, 시세 조회가 실패해 직전 스냅샷을 유지할 때도 같은 산식으로 다시 채점합니다.

## style.css 다시 만들기
`index.html` 에 클래스를 추가/변경한 뒤에는 아래 명령으로 다시 컴파일해 커밋하세요.

```sh
npx tailwindcss -c tailwind.config.js -i tw-input.css -o style.css --minify
```

(npm 이 없으면 Tailwind standalone CLI 사용: `tailwindcss-linux-x64 -c tailwind.config.js -i tw-input.css -o style.css --minify`)

## 푸시 규칙
커밋 작성자·커미터는 `yamal21m <yamal21m@users.noreply.github.com>` 로 맞춥니다(다른 작성자면 Vercel 배포가 막힘).
