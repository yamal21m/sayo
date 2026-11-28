/* SAYO 서비스 워커 — 앱 셸과 data.json 은 network-first.
 *  · 온라인: 항상 네트워크에서 최신 UI·시세를 받고, 받은 사본을 캐시에 갱신
 *  · 오프라인: 캐시된 앱 셸 + 직전에 받아 둔 data.json 으로 화면이 열림
 *  · 교차 출처(업비트·코인베이스·공포탐욕·카운터·환율)는 캐시하지 않고 네트워크 직통
 */
const CACHE_NAME = 'sayo-shell-v3'; // v3: 업데이트 배너 + skipWaiting 지연(열린 화면 보호) + CSP/헤더 정리

const SHELL_URLS = [
  '/',
  '/index.html',
  '/style.css',
  '/manifest.webmanifest',
  '/icons/icon-192.png',
  '/icons/icon-512.png',
  '/icons/icon-maskable-512.png',
  '/icons/apple-touch-icon-180.png',
  '/data.json'
];

self.addEventListener('install', (event) => {
  event.waitUntil((async () => {
    const cache = await caches.open(CACHE_NAME);
    // 일부 파일이 실패해도 설치는 계속 진행한다 (하나라도 빠지면 오프라인 셸이 깨지므로 각각 담는다)
    await Promise.all(SHELL_URLS.map(async (url) => {
      try {
        const res = await fetch(new Request(url, { cache: 'reload' }));
        if (res && res.ok) await cache.put(url, res.clone());
      } catch (e) { /* 오프라인이면 다음 기회에 */ }
    }));
    // 새 버전은 여기서 바로 활성화하지 않습니다. 이미 열려 있는 화면이 옛 style.css/index.html 을
    // 참조하는 도중에 캐시가 바뀌면 화면 일부가 깨질 수 있어, 사용자가 배너에서 '새로고침'을
    // 누를 때(SKIP_WAITING 메시지)만 활성화합니다.
  })());
});

// 페이지의 '새 버전이 준비됐어요 → 새로고침' 버튼이 보내는 신호
self.addEventListener('message', (event) => {
  if (event.data && event.data.type === 'SKIP_WAITING') self.skipWaiting();
});

self.addEventListener('activate', (event) => {
  event.waitUntil((async () => {
    const keys = await caches.keys();
    await Promise.all(keys.filter((k) => k !== CACHE_NAME).map((k) => caches.delete(k)));
    await self.clients.claim();
  })());
});

function putInCache(request, response) {
  if (!response || !response.ok || response.type === 'opaque') return;
  const copy = response.clone();
  caches.open(CACHE_NAME).then((cache) => cache.put(request, copy)).catch(() => {});
}

async function networkFirst(request, fallbackUrl) {
  const cache = await caches.open(CACHE_NAME);
  try {
    const response = await fetch(request);
    if (response && response.ok) putInCache(request, response);
    return response;
  } catch (e) {
    const cached = await cache.match(request, { ignoreSearch: true });
    if (cached) return cached;
    if (fallbackUrl) {
      const fallback = await cache.match(fallbackUrl);
      if (fallback) return fallback;
    }
    return new Response(
      '<!doctype html><meta charset="utf-8"><title>SAYO — 오프라인</title>' +
      '<body style="background:#0b0f19;color:#f1f5f9;font-family:system-ui;padding:40px;text-align:center">' +
      '<h1>오프라인 상태예요</h1><p>저장된 화면이 아직 없습니다. 인터넷에 연결한 뒤 한 번 열어 주세요.</p></body>',
      { status: 503, headers: { 'Content-Type': 'text/html; charset=utf-8' } }
    );
  }
}

self.addEventListener('fetch', (event) => {
  const request = event.request;
  if (request.method !== 'GET') return;

  const url = new URL(request.url);
  // 교차 출처 API 는 서비스 워커가 개입하지 않고 브라우저가 그대로 네트워크로 보낸다.
  if (url.origin !== self.location.origin) return;

  // 페이지 이동은 항상 index.html 네트워크 우선, 오프라인이면 캐시된 셸
  if (request.mode === 'navigate') {
    event.respondWith(networkFirst(request, '/index.html'));
    return;
  }

  const path = url.pathname;
  if (path === '/' || path === '/index.html' || path === '/style.css' ||
      path === '/manifest.webmanifest' || path === '/data.json' ||
      path.startsWith('/icons/')) {
    event.respondWith(networkFirst(request));
    return;
  }

  // 그 밖의 같은 출처 자원(파비콘 등)은 캐시 우선으로 빠르게, 없으면 네트워크
  event.respondWith((async () => {
    const cache = await caches.open(CACHE_NAME);
    const cached = await cache.match(request, { ignoreSearch: true });
    if (cached) return cached;
    try {
      const response = await fetch(request);
      if (response && response.ok) putInCache(request, response);
      return response;
    } catch (e) {
      return Response.error();
    }
  })());
});
