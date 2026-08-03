(function (root) {
  'use strict';

  var OUTLOOK_RE = /\/data\/ai_outlook\.json(?:\?|$)/i;
  var STATE_KEY = '__BR_AI_OUTLOOK_GOVERNANCE__';

  function requestUrl(input) {
    try {
      return new URL(
        typeof input === 'string' ? input : input && input.url || '',
        root.location && root.location.href || 'https://briefrooms.com/'
      );
    } catch (error) {
      return null;
    }
  }

  function language() {
    return String(root.document && root.document.documentElement.lang || 'pl')
      .toLowerCase().indexOf('en') === 0 ? 'en' : 'pl';
  }

  function governedEngine(engine) {
    return Boolean(
      engine &&
      String(engine.version || '').indexOf('ai-outlook-engine-v1.1') === 0
    );
  }

  function validateEdition(payload, lang, strictLanguageSources) {
    var item = payload[lang];
    if (!item || typeof item !== 'object') return false;
    var engine = item.engine || payload.engine;
    var governance = item.governance || payload.governance;
    if (!governedEngine(engine)) return true;
    if (!governance || typeof governance !== 'object') return false;
    if (engine.edition_language && String(engine.edition_language) !== lang) return false;

    if (strictLanguageSources) {
      if (String(item.source_language || '') !== lang) return false;
      var sources = item.sources;
      if (!Array.isArray(sources) || !sources.length) return false;
      for (var index = 0; index < sources.length; index += 1) {
        if (!sources[index] || String(sources[index].source_language || '') !== lang) return false;
      }
    }

    if (governance.disclaimer_required === true) {
      var disclaimers = governance.disclaimers;
      if (!disclaimers || !String(disclaimers[lang] || '').trim()) return false;
      item.disclaimer = String(disclaimers[lang]);
    }
    return true;
  }

  function validate(payload) {
    if (!payload || typeof payload !== 'object') return null;
    var independent = Number(payload.schema_version) === 2 || payload.edition_policy === 'independent-per-language';
    if (independent) {
      if (!payload.source_policy || typeof payload.source_policy !== 'object') return null;
      if (!validateEdition(payload, 'pl', true) || !validateEdition(payload, 'en', true)) return null;
      return payload;
    }

    var engine = payload.engine;
    var governance = payload.governance;
    if (!governedEngine(engine)) return payload;
    if (!governance || typeof governance !== 'object') return null;
    if (governance.disclaimer_required === true) {
      var disclaimers = governance.disclaimers;
      if (!disclaimers || !String(disclaimers.pl || '').trim() || !String(disclaimers.en || '').trim()) {
        return null;
      }
      ['pl', 'en'].forEach(function (lang) {
        if (payload[lang] && typeof payload[lang] === 'object') {
          payload[lang].disclaimer = String(disclaimers[lang]);
        }
      });
    }
    return payload;
  }

  function applyDisclaimer() {
    if (!root.document) return;
    var payload = root[STATE_KEY];
    var card = root.document.getElementById('ai-outlook');
    if (!payload || !card) return;

    var lang = language();
    var item = payload[lang] || {};
    var governance = item.governance || payload.governance || {};
    var disclaimer = String(item.disclaimer || (governance.disclaimers || {})[lang] || '').trim();

    if (governance.disclaimer_required === true && !disclaimer) {
      card.remove();
      return;
    }
    var node = card.querySelector('.ai-outlook__disclaimer');
    if (node && disclaimer) node.textContent = disclaimer;
    card.dataset.governanceValidated = 'true';
    card.dataset.riskClass = String(governance.risk_class || 'legacy');
    card.dataset.editionLanguage = lang;
  }

  function installFetchGuard() {
    if (typeof root.fetch !== 'function' || root.fetch.__brAiOutlookGovernance) return;
    var originalFetch = root.fetch.bind(root);
    var guardedFetch = function (input, init) {
      return originalFetch(input, init).then(function (response) {
        var url = requestUrl(input);
        if (!url || !OUTLOOK_RE.test(url.pathname + url.search) || !response || !response.ok) {
          return response;
        }
        return response.clone().json().then(function (payload) {
          var checked = validate(payload);
          if (!checked) {
            return new Response(JSON.stringify({ error: 'invalid_ai_outlook_governance' }), {
              status: 422,
              headers: { 'Content-Type': 'application/json' }
            });
          }
          root[STATE_KEY] = checked;
          setTimeout(applyDisclaimer, 0);
          return new Response(JSON.stringify(checked), {
            status: response.status,
            statusText: response.statusText,
            headers: response.headers
          });
        }).catch(function () {
          return response;
        });
      });
    };
    guardedFetch.__brAiOutlookGovernance = true;
    guardedFetch.__brOriginalFetch = originalFetch;
    root.fetch = guardedFetch;
  }

  installFetchGuard();
  if (root.document && typeof MutationObserver === 'function') {
    new MutationObserver(applyDisclaimer).observe(root.document.documentElement, {
      childList: true,
      subtree: true
    });
  }
})(typeof globalThis !== 'undefined' ? globalThis : this);
