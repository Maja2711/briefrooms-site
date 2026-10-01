# BriefRooms Trading Notifications

## Status

Background Web Push v2. Warstwa jest wyłącznie obserwatorem zapisanych stanów tradingowych i nie ma prawa zmieniać decyzji, pozycji, ryzyka, cen wejścia, TP/SL ani workflow wykonawczych.

## Zakres użytkownika

Użytkownik może niezależnie wybrać:
- Daily Trading,
- Weekly Trading,
- Stock Trading,
- otwarcie pozycji,
- zamknięcie pozycji.

Powód zamknięcia nie jest osobną subskrypcją. CLOSE oznacza każde faktyczne przejście pozycji z OPEN do braku aktywnej pozycji / CLOSED niezależnie od TP, SL, time exit lub wcześniejszego wyjścia.

## Źródło prawdy

Warstwa notification czyta wyłącznie zapisany stan po wykonaniu przez silnik posiadający authority:
- TR-03 Daily EUR/USD,
- TR-04 Stock Trading v2,
- TR-05 Weekly/WES.

Nie wywołuje lifecycle i nie może tworzyć execution.

## Deduplikacja

Pierwszy run jedynie seeduje stan i nie emituje historycznych alertów. Event jest generowany tylko przy zmianie zbioru aktywnych position_id:
- brak -> OPEN = OPEN,
- OPEN -> brak = CLOSE.

Event ID jest deterministyczny względem engine + event_type + position_id, dzięki czemu ponowny run nie tworzy duplikatu.

## Dostęp i przyszła monetyzacja

Konfiguracja posiada:
- PUBLIC,
- AUTHENTICATED,
- PAID,

globalnie i per kanał. W MVP aktywny jest PUBLIC.

## Background Web Push

GitHub Pages pozostaje publicznym frontendem, natomiast wysyłkę push obsługuje odseparowany Cloudflare Worker `briefrooms-trading-push` z Durable Object jako trwałym magazynem subskrypcji.

Frontend rejestruje `br-trading-sw.js`, pobiera publiczny klucz VAPID z Workera, tworzy subskrypcję przez `PushManager` i zapisuje w backendzie wyłącznie endpoint push, klucze wymagane przez Web Push oraz preferencje Daily/Weekly/Stock + OPEN/CLOSE.

Prywatny klucz VAPID nigdy nie trafia do GitHub Pages ani publicznego repo. Jest generowany i przechowywany jako Cloudflare Worker Secret.

Worker co minutę pobiera kanoniczny feed `data/notifications/trading-events.json`, deduplikuje `event_id` i wysyła alert wyłącznie do pasujących subskrypcji. Pierwsza inicjalizacja jest seed-only, więc nie wysyła historycznych zdarzeń.

Awaria backendu push nie ma wpływu na TR-03/TR-04/TR-05 ani na execution/risk/decision path.

## Docelowe Notification Analytics

Backend ma raportować co najmniej:
- active_subscriptions,
- unique_users (gdy istnieje logowanie),
- active_devices,
- Daily / Weekly / Stock opt-in,
- OPEN / CLOSE opt-in,
- new_7d,
- unsubscribed_7d,
- sent,
- delivered (jeżeli provider potwierdza),
- clicked,
- CTR.

Rozdzielenie users/devices jest obowiązkowe, bo jedna osoba może mieć kilka urządzeń.
