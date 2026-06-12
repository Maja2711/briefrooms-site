/**
 * BriefRooms Hotbar Script
 * - PL/EN autodetection.
 * - Reads latest summaries from /.cache/news_summaries_*.json.
 * - Builds a continuous clickable ticker.
 * - Keeps public contact links on contact@briefrooms.com.
 */
(function () {
  'use strict';

  var CONTACT_EMAIL = 'contact@briefrooms.com';
  var MAX_ITEMS = 12;
  var MIN_DURATION_SECONDS = 34;
  var PX_PER_SECOND = 58;

  function getLang() {
    var htmlLang = (document.documentElement.getAttribute('lang') || '').toLower