(function (root) {
  'use strict';

  var OUTLOOK_RE = /\/data\/ai_outlook\.json(?:\?|$)/i;
  var STATE_KEY = '__BR_AI_OUTLOOK_GOVERNANCE__';
  var observer = null;
  var applyScheduled = false;

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

  function setTextIfChanged(node, value) {
    if (!node) return false;
    var text = String(value || '');
    if (node.textContent === text) return false;
    node.textContent = text;
    return true;
  }

  function setDatasetIfChanged(node, key, value) {
    if (!node || !node.dataset) return false;
    var text = String(value || '');
    if (node.dataset[key] === text) return false;
    node.dataset[key] = text;
    return true;
  }

  function applyDisclaimer() {
    applyScheduled = false;
    if (!root.document) return false;
    var payload = root[STATE_KEY];
    var card = root.document.getElementById('ai-outlook');
    if (!payload || !card) return false;

    var lang = language();
    var item = payload[lang] || {};
    var governance = item.governance || payload.governance || {};
    var disclaimer = String(item.disclaimer || (governance.disclaimers || {})[lang] || '').trim();

    if (governance.disclaimer_required === true && !disclaimer) {
      card.remove();
      if (observer) {
        observer.disconnect();
        observer = null;
      }
      return true;
    }

    var node = card.querySelector('.ai-outlook__disclaimer');
    setTextIfChanged(node, disclaimer);
    setDatasetIfChanged(card, 'governanceValidated', 'true');
    setDatasetIfChanged(card, 'riskClass', governance.risk_class || 'legacy');
    setDatasetIfChanged(card, 'editionLanguage', lang);

    if (observer) {
      observer.disconnect();
      observer = null;
    }
    return true;
  }

  function scheduleApply() {
    if (applyScheduled) return;
    applyScheduled = true;
    var schedule = typeof root.requestAnimationFrame === 'function'
      ? root.requestAnimationFrame.bind(root)
      : function (callback) { return root.setTimeout(callback, 0); };
    schedule(applyDisclaimer);
  }

  function addedAiOutlook(records) {
    for (var recordIndex = 0; recordIndex < records.length; recordIndex += 1) {
      var nodes = records[recordIndex].addedNodes || [];
      for (var nodeIndex = 0; nodeIndex < nodes.length; nodeIndex += 1) {
        var node = nodes[nodeIndex];
        if (!node || node.nodeType !== 1) continue;
        if (node.id === 'ai-outlook') return true;
        if (node.querySelector && node.querySelector('#ai-outlook')) return true;
      }
    }
    return false;
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
          scheduleApply();
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

  function installCardObserver() {
    if (!root.document || typeof MutationObserver !== 'function') return;
    if (root.document.getElementById('ai-outlook')) {
      scheduleApply();
      return;
    }
    observer = new MutationObserver(function (records) {
      if (addedAiOutlook(records)) scheduleApply();
    });
    observer.observe(root.document.documentElement, {
      childList: true,
      subtree: true
    });
  }

  installFetchGuard();
  installCardObserver();

  root.BriefRoomsAiOutlookGovernance = {
    validate: validate,
    applyDisclaimer: applyDisclaimer,
    scheduleApply: scheduleApply
  };
})(typeof globalThis !== 'undefined' ? globalThis : this);
