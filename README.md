# sayo
Sayo-Radar — 6대 자산 바겐세일 신호등 (https://sayo-umber.vercel.app)

## 구성
- `index.html` — 화면 전체(정적 HTML + 인라인 JS). 시세는 `data.json` 을 받아 그립니다.
- `style.css` — **빌드해 둔 Tailwind CSS**. CDN 스크립트(`cdn.tailwindcss.com`, 약 400KB JS)를 쓰지 않아 첫 페인트가 빠릅니다.
- `update_data.py` — GitHub Actions 가 하루 3회(07·16·23 KST) 실행해 `data.json` 을 갱신합니다.

## style.css 다시 만들기
`index.html` 에 클래스를 추가/변경한 뒤에는 아래 명령으로 다시 컴파일해 커밋하세요.

```sh
npx tailwindcss -c tailwind.config.js -i tw-input.css -o style.css --minify
```

(npm 이 없으면 Tailwind standalone CLI 사용: `tailwindcss-linux-x64 -c tailwind.config.js -i tw-input.css -o style.css --minify`)

## 푸시 규칙
커밋 작성자·커미터는 `yamal21m <yamal21m@users.noreply.github.com>` 로 맞춥니다(다른 작성자면 Vercel 배포가 막힘).
