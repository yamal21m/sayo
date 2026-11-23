/** Tailwind 빌드 설정 — index.html 에 실제로 쓰인 클래스만 style.css 로 뽑습니다.
 *  다시 만들 때:
 *    npx tailwindcss -c tailwind.config.js -i tw-input.css -o style.css --minify
 *  (CDN 스크립트를 쓰지 않으므로 첫 페인트에서 400KB 급 JS 를 받지 않습니다.)
 */
module.exports = {
  content: ['./index.html'],
  theme: { extend: {} },
  plugins: []
};
