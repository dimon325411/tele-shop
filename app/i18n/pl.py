"""Polish UI texts. Keys are shared by all languages; add e.g. ``en.py`` with the same keys.

Texts use Telegram HTML formatting. Placeholders use str.format syntax.
"""

MESSAGES: dict[str, str] = {
    # --- general
    "start.welcome_new": (
        "👋 Cześć, {name}!\n\n"
        "Ten bot sprawdza ogłoszenia i wysyła Ci link, gdy pojawi się nowa oferta pasująca do Twoich kryteriów.\n\n"
        "🎁 Aktywowaliśmy darmowy okres próbny: <b>{plan}</b> do {until}.\n\n"
        "ℹ️ Uwaga: obecnie działa tylko <b>źródło testowe</b>. Monitoring OLX.pl i Allegro Lokalnie "
        "jest wyłączony do czasu uzyskania zgody serwisów (/help)."
    ),
    "start.welcome_personal": (
        "👋 Cześć, {name}! To Twój prywatny bot.\n\n"
        "Źródła: OLX.pl (publiczne wyniki wyszukiwania, sprawdzanie najczęściej co 10 min) i źródło testowe.\n"
        "Jeśli OLX ograniczy dostęp, monitoring tego źródła zatrzyma się automatycznie."
    ),
    "start.welcome_back": "👋 Witaj ponownie, {name}! Wybierz, co chcesz zrobić.",
    "menu.title": "Menu główne:",
    "btn.new_search": "➕ Nowe wyszukiwanie",
    "btn.searches": "📋 Moje wyszukiwania",
    "btn.plan": "⭐ Subskrypcja",
    "btn.status": "📊 Status",
    "btn.settings": "⚙️ Ustawienia",
    "btn.help": "❓ Pomoc",
    "btn.cancel": "✖️ Anuluj",
    "btn.skip": "⏭ Pomiń",
    "btn.back": "⬅️ Wstecz",
    "btn.yes": "✅ Tak",
    "btn.no": "❌ Nie",
    "btn.done": "✅ Gotowe",
    "btn.save": "💾 Zapisz",
    "btn.open_listing": "🔗 Otwórz ogłoszenie",
    "btn.pause_search": "⏸ Wstrzymaj to wyszukiwanie",
    "btn.notify_settings": "🔔 Ustawienia powiadomień",
    "btn.pause": "⏸ Wstrzymaj",
    "btn.resume": "▶️ Wznów",
    "btn.pause_all": "⏸ Wstrzymaj wszystkie",
    "btn.delete": "🗑 Usuń",
    "btn.buy": "Kup {plan} — {price} ⭐",
    "btn.accept_terms": "✅ Akceptuję regulamin",
    "btn.quiet_hours": "🌙 Godziny ciszy",
    "btn.price_drops_on": "📉 Spadki cen: włączone",
    "btn.price_drops_off": "📉 Spadki cen: wyłączone",
    "btn.delete_account": "🗑 Usuń konto",
    "cancelled": "Anulowano.",
    "error.generic": "😕 Coś poszło nie tak. Spróbuj ponownie za chwilę. Jeśli problem się powtarza: /support",
    "error.rate_limited": "⏳ Zbyt wiele poleceń. Odczekaj chwilę.",
    "error.suspended": "⛔ Twoje konto zostało zawieszone. Kontakt: /support",
    "error.private_bot": "🔒 To jest prywatny bot. Nie przyjmuje nowych użytkowników.",
    "error.not_admin": "Ta komenda jest dostępna tylko dla administratora.",

    # --- help & legal
    "help": (
        "<b>Jak to działa</b>\n"
        "1. /newsearch — tworzysz wyszukiwanie (słowa kluczowe, cena, miasto…).\n"
        "2. Bot regularnie sprawdza źródła i porównuje ogłoszenia z kryteriami.\n"
        "3. Gdy pojawi się nowe pasujące ogłoszenie, dostaniesz link.\n\n"
        "<b>Komendy</b>\n"
        "/newsearch — nowe wyszukiwanie\n/searches — lista wyszukiwań\n/status — stan usługi\n"
        "/plan — subskrypcja i limity\n/pause, /resume — wstrzymaj/wznów monitoring\n"
        "/delete — usuń wyszukiwanie\n/settings — powiadomienia i godziny ciszy\n"
        "/terms — regulamin\n/privacy — prywatność\n/support — pomoc\n/paysupport — płatności\n\n"
        "<b>Źródła</b>\nObecnie aktywne jest wyłącznie źródło testowe (ogłoszenia oznaczone [TEST]). "
        "Regulaminy OLX.pl i Allegro nie pozwalają na automatyczne zbieranie ich ogłoszeń bez zgody, "
        "dlatego te źródła są wyłączone do czasu uzyskania zgody.\n\n"
        "Częstotliwość sprawdzania zależy od planu i dostępności źródła — powiadomienia nie są natychmiastowe."
    ),
    "terms": (
        "<b>Regulamin (skrót)</b>\n"
        "• Usługa wysyła powiadomienia o ogłoszeniach z dostępnych źródeł. Nie gwarantujemy natychmiastowości "
        "ani kompletności — zależą od planu i dostępności źródeł.\n"
        "• Subskrypcja jest usługą cyfrową opłacaną w Telegram Stars (XTR). Okres i limity planu są widoczne w /plan.\n"
        "• Po wygaśnięciu subskrypcji monitoring zatrzymuje się, a Twoje wyszukiwania pozostają zapisane.\n"
        "• Zwroty rozpatrujemy indywidualnie przez /paysupport.\n"
        "• Zabronione jest nadużywanie usługi (np. automatyczne zapytania do bota).\n"
        "• Wsparcie Telegrama nie obsługuje zakupów w tym bocie — w sprawie płatności pisz przez /paysupport.\n"
        "• Kupując, potwierdzasz akceptację regulaminu."
    ),
    "terms.need_accept": "Przed zakupem zaakceptuj regulamin (/terms).",
    "terms.accepted": "✅ Regulamin zaakceptowany.",
    "privacy": (
        "<b>Prywatność</b>\n"
        "Przechowujemy: Twój identyfikator Telegram, nazwę użytkownika, ustawienia wyszukiwań, historię "
        "powiadomień i płatności. Nie zbieramy numeru telefonu ani lokalizacji.\n"
        "Dane ogłoszeń przechowujemy do {listings_days} dni, historię powiadomień do {notifications_days} dni.\n"
        "Konto możesz usunąć w /settings → „Usuń konto”. Dane płatności zachowujemy tak długo, jak wymagają "
        "tego przepisy księgowe."
    ),
    "support": "💬 Pomoc techniczna: {contact}\nOpisz problem i podaj swój ID: <code>{tg_id}</code>",
    "paysupport": (
        "💳 Pomoc w sprawie płatności: {contact}\n"
        "Podaj swój ID <code>{tg_id}</code> i datę płatności. Wsparcie Telegrama nie obsługuje zakupów w tym bocie."
    ),

    # --- wizard
    "wizard.name": "✏️ <b>Krok 1/10.</b> Podaj nazwę wyszukiwania (np. „Rower do 1500 zł”).",
    "wizard.sources": "🌐 <b>Krok 2/10.</b> Wybierz źródła (możesz kilka), potem „Gotowe”.",
    "wizard.sources_none": "Wybierz co najmniej jedno źródło.",
    "wizard.source_unavailable": "{source} — niedostępne",
    "wizard.keywords": (
        "🔑 <b>Krok 3/10.</b> Wpisz słowa kluczowe, które <b>muszą</b> wystąpić. Kilka fraz oddziel przecinkiem.\n"
        "Np.: <code>rower, 29</code>. Wpisz „-”, jeśli chcesz podać tylko słowa alternatywne."
    ),
    "wizard.alt_keywords": (
        "🔀 <b>Krok 4/10.</b> Słowa alternatywne — wystarczy <b>jedno</b> z nich. "
        "Np.: <code>kross, giant, trek</code>. Wpisz „-”, aby pominąć."
    ),
    "wizard.excluded": "🚫 <b>Krok 5/10.</b> Słowa wykluczające (np. <code>uszkodzony, na części</code>). „-” — pomiń.",
    "wizard.category": "🗂 <b>Krok 6/10.</b> Kategoria (ogłoszenia bez kategorii przechodzą, chyba że wyłączysz to w podsumowaniu):",
    "wizard.price": (
        "💰 <b>Krok 7/10.</b> Zakres cen w PLN: <code>100-500</code>, <code>-500</code> (do 500), "
        "<code>100-</code> (od 100). „-” — bez limitu.\n"
        "Ogłoszenia bez ceny nie są wysyłane, gdy ustawisz zakres."
    ),
    "wizard.condition": "📦 <b>Krok 8/10.</b> Stan przedmiotu:",
    "wizard.city": "📍 <b>Krok 9/10.</b> Miasto (np. <code>Kraków</code>) lub „-”, aby szukać w całej Polsce.",
    "wizard.delivery": "🚚 <b>Krok 10/10.</b> Sposób odbioru:",
    "wizard.summary": "<b>Podsumowanie</b>\n{summary}\n\nZapisać wyszukiwanie?",
    "wizard.saved": (
        "✅ Zapisano wyszukiwanie „{name}”.\nSprawdzanie co {interval}. Pierwsze sprawdzenie zapamięta obecne "
        "ogłoszenia — potem wyślę tylko nowe."
    ),
    "wizard.unsupported_filters": "ℹ️ {source} nie filtruje sam po: {filters}. Sprawdzamy to po naszej stronie na podstawie danych ogłoszenia.",
    "wizard.btn_interval": "⏱ Częstotliwość: {interval}",
    "wizard.btn_unknown_on": "❔ Ogłoszenia bez danych: wysyłaj",
    "wizard.btn_unknown_off": "❔ Ogłoszenia bez danych: pomijaj",
    "wizard.btn_fuzzy_on": "🔤 Tolerancja literówek: włączona",
    "wizard.btn_fuzzy_off": "🔤 Tolerancja literówek: wyłączona",
    "category.any": "Dowolna",
    "category.elektronika": "Elektronika",
    "category.dom": "Dom i ogród",
    "category.moda": "Moda",
    "category.sport": "Sport i hobby",
    "category.motoryzacja": "Motoryzacja",
    "category.dzieci": "Dla dzieci",
    "filter.price": "cena",
    "filter.category": "kategoria",
    "filter.condition": "stan",
    "filter.city": "lokalizacja",
    "filter.delivery": "wysyłka",
    "condition.any": "Dowolny",
    "condition.new": "Nowy",
    "condition.used": "Używany",
    "delivery.any": "Dowolny",
    "delivery.shipping": "Tylko z wysyłką",
    "delivery.pickup": "Odbiór osobisty",

    # --- invalid input
    "invalid.empty": "To pole nie może być puste.",
    "invalid.too_long": "Za długie (maks. {max} znaków).",
    "invalid.too_many": "Za dużo fraz (maks. {max}).",
    "invalid.price": "Nieprawidłowa cena. Przykład: 1200 albo 99,99.",
    "invalid.price_too_high": "Cena jest za wysoka.",
    "invalid.price_range": "Podaj zakres w formacie 100-500, -500 albo 100-.",
    "invalid.price_order": "Cena minimalna nie może być większa od maksymalnej.",
    "invalid.currency": "Nieobsługiwana waluta.",
    "invalid.place": "Nieprawidłowa nazwa miejscowości.",
    "invalid.radius": "Promień musi być liczbą 0–500 km.",
    "invalid.hour": "Godzina musi być liczbą 0–23.",
    "invalid.quiet_hours": "Podaj godziny w formacie 22-7 albo „-”, aby wyłączyć.",

    # --- access denied
    "deny.no_subscription": "🔒 Nie masz aktywnej subskrypcji. Zobacz /plan.",
    "deny.suspended": "⛔ Konto zawieszone. Kontakt: /support",
    "deny.search_limit": "Osiągnięto limit aktywnych wyszukiwań w Twoim planie ({limit}). Wstrzymaj inne albo zmień plan (/plan).",
    "deny.source_not_in_plan": "Twój plan nie obejmuje źródła: {sources}.",
    "deny.source_disabled": "Źródło jest obecnie niedostępne: {sources}.",
    "deny.feature_not_in_plan": "Ta funkcja nie jest dostępna w Twoim planie.",
    "deny.not_found": "Nie znaleziono wyszukiwania.",
    "deny.no_keywords": "Podaj co najmniej jedno słowo kluczowe lub alternatywne.",
    "deny.no_sources": "Wybierz co najmniej jedno źródło.",
    "deny.trial_used": "Okres próbny został już wykorzystany.",
    "deny.already_subscribed": "Masz już aktywną subskrypcję.",
    "deny.trial_unavailable": "Okres próbny jest niedostępny.",

    # --- searches
    "searches.empty": "Nie masz jeszcze wyszukiwań. Utwórz pierwsze: /newsearch",
    "searches.title": "<b>Twoje wyszukiwania</b>",
    "searches.item": "{icon} <b>{name}</b> — {sources}\n   {criteria}\n   Ostatnie sprawdzenie: {last}",
    "searches.choose_pause": "Które wyszukiwanie wstrzymać?",
    "searches.choose_resume": "Które wyszukiwanie wznowić?",
    "searches.choose_delete": "Które wyszukiwanie usunąć?",
    "searches.none_to_pause": "Brak aktywnych wyszukiwań.",
    "searches.none_to_resume": "Brak wstrzymanych wyszukiwań.",
    "searches.paused": "⏸ Wstrzymano „{name}”.",
    "searches.paused_all": "⏸ Wstrzymano wyszukiwania: {count}. Wznowienie: /resume",
    "searches.resumed": "▶️ Wznowiono „{name}”.",
    "searches.confirm_delete": "Na pewno usunąć „{name}”? Tej operacji nie można cofnąć.",
    "searches.deleted": "🗑 Usunięto „{name}”.",
    "searches.over_limit": "⚠️ Nie monitorowane — przekroczony limit planu",
    "never": "jeszcze nie",

    # --- status
    "status": (
        "<b>Status</b>\n{subscription}\nAktywne wyszukiwania: {active}/{limit}\n"
        "Powiadomienia dziś: {sent_today}/{daily_limit}\n\n<b>Źródła</b>\n{sources}"
    ),
    "status.source_line": "{icon} {name}: {state}",
    "status.source.ok": "działa",
    "status.source.disabled": "wyłączone",
    "status.source.blocked": "wstrzymane (ograniczenia źródła)",
    "status.source.degraded": "problemy",
    "status.source.test": "dane testowe",

    # --- plans
    "plan.current": "Twój plan: <b>{plan}</b>, ważny do {until}.",
    "plan.none": "Brak aktywnej subskrypcji.",
    "plan.list_title": "<b>Dostępne plany</b>",
    "plan.item": (
        "<b>{name}</b> — {price} ⭐ / {days} dni\n"
        "• wyszukiwania: {searches}\n• sprawdzanie co {interval}\n• powiadomienia/dzień: {daily}\n{extras}"
    ),
    "plan.extra.fuzzy": "• tolerancja literówek",
    "plan.extra.price_drop": "• alerty o spadku ceny",
    "plan.payments_disabled": (
        "💤 Płatności są obecnie wyłączone (tryb testowy usługi). Skontaktuj się z /paysupport."
    ),
    "plan.change_note": "Zmiana planu działa od razu; niewykorzystany czas obecnego planu przeliczamy na dodatkowy czas nowego.",
    "plan.expired": "⌛ Twoja subskrypcja wygasła. Wyszukiwania zostały zachowane, ale monitoring jest wstrzymany. Odnów: /plan",

    # --- payments
    "pay.invoice_title": "Subskrypcja {plan}",
    "pay.invoice_description": "Plan {plan} na {days} dni. Usługa cyfrowa, płatność w Telegram Stars.",
    "pay.success": "🎉 Dziękujemy! Plan <b>{plan}</b> aktywny do {until}.",
    "pay.duplicate": "Ta płatność została już zaksięgowana.",
    "pay.problem": "⚠️ Płatność otrzymana, ale nie mogliśmy jej przypisać. Skontaktuj się z /paysupport — pomożemy lub zwrócimy środki.",
    "pay.error.bad_payload": "Nieprawidłowa faktura. Utwórz nową w /plan.",
    "pay.error.currency": "Nieprawidłowa waluta płatności.",
    "pay.error.user_mismatch": "Ta faktura należy do innego użytkownika.",
    "pay.error.suspended": "Konto zawieszone — płatność niemożliwa.",
    "pay.error.plan_unavailable": "Ten plan jest niedostępny.",
    "pay.error.amount": "Nieprawidłowa kwota.",
    "pay.error.price_changed": "Cena planu się zmieniła. Utwórz nową fakturę w /plan.",
    "pay.error.payments_disabled": "Płatności są obecnie wyłączone.",
    "pay.error.unknown_user": "Najpierw uruchom bota komendą /start.",
    "pay.error.terms_not_accepted": "Przed zakupem zaakceptuj regulamin (/terms).",

    # --- notifications
    "notify.new": "🆕 <b>{title}</b>",
    "notify.price_drop": "📉 <b>Spadek ceny:</b> {title}",
    "notify.price": "💰 {price} {currency}",
    "notify.price_drop_line": "💰 <s>{old} {currency}</s> → <b>{price} {currency}</b>",
    "notify.price_unknown": "💰 cena nie podana",
    "notify.source": "🌐 {source}",
    "notify.location": "📍 {location}",
    "notify.condition": "📦 {condition}",
    "notify.found_at": "🕒 wykryto {time}",
    "notify.search": "🔎 wyszukiwanie: {name}",
    "notify.test_marker": "🧪 <i>Ogłoszenie testowe — nie jest prawdziwą ofertą.</i>",
    "notify.baseline": "🔎 „{name}”: pierwsze sprawdzenie zakończone, pasujących ogłoszeń teraz: {count}. Od teraz wyślę tylko nowe.",
    "notify.search_paused": "⏸ Wstrzymano wyszukiwanie „{name}”.",

    # --- settings
    "settings.title": "<b>Ustawienia</b>\nGodziny ciszy: {quiet}\nStrefa czasowa: {tz}",
    "settings.quiet_none": "wyłączone",
    "settings.ask_quiet": "Podaj godziny ciszy, np. <code>22-7</code> (powiadomienia poczekają do rana), albo „-”, aby wyłączyć.",
    "settings.quiet_saved": "🌙 Zapisano godziny ciszy: {quiet}.",
    "settings.confirm_delete_account": (
        "⚠️ Usunąć konto? Usuniemy wyszukiwania i historię powiadomień, a subskrypcja zostanie anulowana bez zwrotu. "
        "Dane płatności zachowamy zgodnie z przepisami."
    ),
    "settings.account_deleted": "Konto usunięte. Dziękujemy za korzystanie z usługi. /start — aby zacząć od nowa.",

    # --- match reasons
    "reason.keyword": "słowo „{word}”",
    "reason.alt_keyword": "alternatywa „{word}”",
    "reason.price": "cena {price}",
    "reason.condition": "stan: {condition}",
    "reason.city": "miasto: {city}",
    "reason.distance": "{km} km",
    "reason.unknown": "brak danych: {field}",
    "reason.excluded": "wykluczone „{word}”",

    # --- admin
    "admin.help": (
        "<b>Administrator</b>\n/admin — statystyki\n/admin_sources — źródła\n"
        "/source_off &lt;kod&gt; [powód], /source_on &lt;kod&gt;\n/plans_admin — plany\n"
        "/plan_set &lt;plan&gt; &lt;pole&gt; &lt;wartość&gt;\n/payerrors — błędy płatności\n"
        "/suspend &lt;tg_id&gt; [powód], /unsuspend &lt;tg_id&gt;\n/grant &lt;tg_id&gt; &lt;plan&gt; &lt;dni&gt;\n"
        "/refund &lt;charge_id&gt; [powód]\n/audit — dziennik działań"
    ),
    "admin.stats": (
        "<b>Statystyki</b>\nUżytkownicy: {users}\nAktywne subskrypcje: {active_subscriptions} (płatne: {paid_subscriptions})\n"
        "Wyszukiwania: {active_searches}/{searches}\nOgłoszenia w bazie: {listings}\n"
        "Powiadomienia 24h: {notifications_24h}, w kolejce: {pending_notifications}\n"
        "Opłacone płatności: {payments_paid}\nBłędy sprawdzeń 24h: {runs_failed_24h}"
    ),
    "admin.ok": "✅ Gotowe.",
    "admin.usage": "Użycie: {usage}",
    "admin.error.bad_field": "Nieznane pole. Dostępne: {fields}",
    "admin.error.bad_value": "Nieprawidłowa wartość.",
    "admin.error.no_plan": "Nie ma takiego planu.",
    "admin.error.no_user": "Nie ma takiego użytkownika.",
    "admin.empty": "Brak wpisów.",
    "admin.refund.refunded": "✅ Zwrot wykonany, subskrypcja cofnięta.",
    "admin.refund.not_found": "Nie znaleziono płatności.",
    "admin.refund.already_refunded": "Ta płatność została już zwrócona.",
    "admin.refund.failed": "❌ Telegram odrzucił zwrot. Szczegóły w dzienniku.",

    # --- units
    "unit.minutes": "{n} min",
    "unit.hours": "{n} godz.",
}
