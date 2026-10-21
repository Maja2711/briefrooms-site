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

## Standard treści powiadomień (PL/EN)

Status otwarcia lub zamknięcia jest **zawsze informacją główną**. Powód techniczny wyjścia (`exit_reason`: stop-loss, take-profit, risk exit itd.) pozostaje w kanonicznym evencie i w `data.exit_reason` Web Push, ale **nie zastępuje statusu w treści alertu**.

- **Stock Trading (GPW/USA):** `POZYCJA OTWARTA · XP @ 21.195` / `POZYCJA ZAMKNIĘTA · XP @ 30.9362493 · Wynik cenowy: +45,96%`. Bez etykiety LONG/SHORT w alertach akcyjnych. Wersja EN: `POSITION OPENED` / `POSITION CLOSED`.
- **Daily EUR/USD oraz Weekly EUR/USD, BTC/USD, S&P 500 futures:** `OTWARTY LONG`, `ZAMKNIĘTY LONG`, `OTWARTY SHORT`, `ZAMKNIĘTY SHORT` + instrument i odpowiednia cena. Wersja EN: `OPENED LONG`, `CLOSED LONG` itd.
- Jeżeli źródło nie zawiera poprawnej strony LONG/SHORT, alert stosuje status ogólny (bez dopowiadania kierunku).
- Cena zamknięcia jest prezentowana tylko przy obecnym `exit_price`; nie wolno wyświetlić ceny wejścia jako ceny wyjścia.
- `Wynik cenowy` dla zamkniętych akcji jest liczony z utrwalonych cen wejścia/wyjścia i kierunku: LONG = (`exit/entry − 1`) × 100%, SHORT = (`1 − exit/entry`) × 100%; nie uwzględnia prowizji, poślizgu ani finansowania i nie jest równoznaczny z wynikiem netto rachunku. Gdy wartości nie są wiarygodne, nie wyświetla się procentu.

Ten sam formatter obowiązuje backend Web Push oraz przeglądarkowe powiadomienia recovery. Zmiana tekstu nie zmienia `event_id`, deduplikacji, subskrypcji ani mechaniki transakcyjnej.

Do 5 minut włącznie nie dodajemy do treści informacji o opóźnieniu. Dopiero starsze zdarzenie dostaje `OPÓŹNIONE … min` / `DELAYED … min`. Czas zdarzenia, przyjęcia przez dostawcę i wyświetlenia pozostają oddzielne oraz niezmienione w diagnostyce. Recovery CLOSE używa `closed_at`, a przy jego braku `observed_at`, nigdy dawnego czasu otwarcia pozycji.

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

Dla wszystkich kanałów produkcyjna ścieżka push jest natychmiastowa i commit-bound: **Daily EUR/USD, Weekly EUR/USD, Weekly BTC/USD, Weekly S&P 500 futures oraz Stock Trading (GPW i USA)**. Każdy kanoniczny writer, który skutecznie zapisze zmianę pozycji na `main`, przekazuje do Workera przez `/sync-trading` wyłącznie SHA tego konkretnego commita oraz nazwę kanału. Worker weryfikuje, że SHA jest aktualnym lub niedawnym przodkiem aktualnego `main`, pobiera dokładny stan z commita i jego bezpośredniego rodzica, a następnie emituje wyłącznie rzeczywiste przejścia `brak -> OPEN` oraz `OPEN -> CLOSED`. Dzięki temu równoczesne zamknięcie jednej pozycji i otwarcie innej nie gubi alertu, a zwykła aktualizacja mark-to-market nie tworzy fałszywego powiadomienia.

Dla Weekly mechanizm obejmuje wszystkie trzy instrumenty WES: EUR/USD, BTC/USD i S&P 500 futures, niezależnie od tego, czy zapis pochodzi z WES admission/lifecycle, pełnego maintenance, freshness recovery, live-price/risk writera, Event Intelligence czy kanonicznego 5-minutowego risk-exit path. Dla Stock Trading obejmuje v2 production admission, canonical portfolio lifecycle oraz Event Intelligence overlay. Daily używa tego samego wspólnego mechanizmu także w realtime exit watcherze.

Język Web Push jest ustalany per urządzenie z zapisanej subskrypcji: urządzenie PL otrzymuje polski tekst i prowadzi do strony PL, urządzenie EN otrzymuje angielski tekst i prowadzi do strony EN. Event ID pozostaje deterministyczny względem `engine + event_type + position_id`, więc równoległy recovery path nie może zdublować już wysłanego zdarzenia.

Publiczny bezpośredni `/ingest` jest wyłączony. Wewnętrzny `https://internal/ingest` jest dostępny wyłącznie dla schedulera Workera jako mechanizm recovery, który odczytuje kanoniczny `data/notifications/trading-events.json`; polling nie jest już podstawową ścieżką dostarczenia alertu. Pierwsza inicjalizacja recovery pozostaje seed-only, więc nie wysyła historycznych zdarzeń.

Awaria backendu push nie ma wpływu na TR-03/TR-04/TR-05 ani na execution/risk/decision path.

## GitHub–Cloudflare: handoff, retry i wdrożenie

Writer wywołuje `scripts/sync_trading_push_commit.sh` od razu po skutecznym zapisie commita. Żądanie ma limit połączenia 5 s i całkowity 25 s, maksymalnie cztery próby transportu. Potwierdzenie musi odpowiadać dokładnemu SHA i żądanym kanałom. Skrypt nie czeka na wyświetlenie na telefonie ani na osobny workflow publikacji strony.

GitHub Actions przekazuje swój krótkotrwały token w nagłówku HTTPS wyłącznie do własnego Workera. Worker używa go tylko podczas tego żądania do GET wskazanego repozytorium w API GitHub, bez przekierowań, zapisu tokenu lub logowania. Pozwala to uniknąć limitów anonimowego API; fallback bez tokenu zachowuje weryfikację utrwalonego stanu. Token nie daje warstwie powiadomień ścieżki wykonywania transakcji.

Pierwsza wysyłka rusza natychmiast. Jawne odrzucenie `429/5xx` może wejść do trwałej kolejki: alarm Durable Object uruchamia retry od 2 s, respektuje `Retry-After`, ogranicza całość do pięciu prób i 10 minut od utworzenia dostawy. HTTP 202 oznacza zweryfikowane, trwałe przyjęcie z zaplanowanym alarmem, nie dowód wyświetlenia. Zakończona lub niejednoznaczna wysyłka nie jest odtwarzana; błąd 403 dostawcy nie usuwa subskrypcji. Awarie feedów recovery nie blokują niezależnych retry.

Inicjalizacja odbiorców i wysyłka są serializowane. ACK telefonu wymaga zakończonego `showNotification` lub potwierdzonej istniejącej wiadomości; wpis `PENDING` w IndexedDB nie jest dowodem wyświetlenia. Przyjęcie przez usługę push nie jest równoznaczne z odbiorem przez urządzenie i brak ACK nie powoduje ponownego alarmu.

Deployment sprawdza read-only `/health` i `/inbox`, wersję `commit-bound-durable-v3` oraz obsługę alarmów. Dodatkowy uwierzytelniony `GET /verify-commit?sha=...` weryfikuje dostęp Actions do GitHuba i dokładnego commita przez Cloudflare; nie zapisuje stanu ani nie wywołuje synchronizacji lub wysyłki. Nie wywołujemy produkcyjnych przejść jako testu i nie wysyłamy fikcyjnych powiadomień. Istniejące klucze VAPID są zachowane; gdy brakuje tylko jednego, deployment wymaga przywrócenia oryginalnego klucza zamiast unieważnić wszystkie urządzenia. Push zmian tego połączenia uruchamia walidację writerów; runtime pozycji pozostaje wyłącznie harmonogramem lub jawnym/governed uruchomieniem.

Nieudany handoff zapisuje osobny czas i kod błędu z zamkniętego katalogu, zachowując poprzedni potwierdzony sukces. Logi Actions pokazują wyłącznie bezpieczny kod, nigdy surową odpowiedź lub token. Walidacja publikacji porównuje również `br-trading-sw.js` i `scripts/trading-notifications.js`; osiem stron tradingowych PL/EN ma wspólną, aktualną wersję URL tego skryptu. Otwarcie lub odświeżenie aplikacji pozwala pobrać aktualizację; istniejąca subskrypcja urządzenia pozostaje ważna.

Nadanie do usługi push następuje bez celowego oczekiwania na cykl recovery. Ostateczny czas wyświetlenia zależy również od sieci, dostawcy Web Push i systemu telefonu; metryki nie deklarują zerowego opóźnienia ani dostarczenia bez dowodu ACK.

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
