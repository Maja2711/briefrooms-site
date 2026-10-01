# BriefRooms Trading Notifications

## Status

MVP v1. Warstwa jest wyłącznie obserwatorem zapisanych stanów tradingowych i nie ma prawa zmieniać decyzji, pozycji, ryzyka, cen wejścia, TP/SL ani workflow wykonawczych.

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

## Ograniczenie GitHub Pages

GitHub Pages nie przechowuje bezpiecznie subskrypcji Web Push ani sekretów VAPID. Dlatego v1 ma:
1. działające ustawienia per urządzenie,
2. systemowe test notification,
3. polling nowego event feedu, gdy BriefRooms jest otwarte,
4. service worker przygotowany do odbioru prawdziwego Web Push.

Pełny background push przy zamkniętej stronie oraz globalne Notification Analytics wymagają bezpiecznego backendu/serverless z bazą subskrypcji. Nie należy przechowywać endpointów subskrypcji ani kluczy wysyłkowych w publicznym repo.

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
