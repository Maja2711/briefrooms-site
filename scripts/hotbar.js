/**
 * BriefRooms Hotbar Script (v5 – PL/EN + klikalne linki + kontakt)
 * - Wykrywa język strony (PL/EN) na podstawie <html lang> i ścieżki /en/.
 * - Czyta dane z:
 *     /.cache/news_summaries_pl.json   (PL)
 *     /.cache/news_summaries_en.json   (EN)
 * - Oczekiwany format JSON:
 *     { "v2|Some summary|2025-11-19": "https://link-do-artykulu", ... }
 * - Buduje poziomy pasek z newsami, każdy wpis to <a href="URL">Tekst</a>.
 * - Ujednolica linki kontaktowe mailto.
 */

(function () {
  // ----------------------------------------
