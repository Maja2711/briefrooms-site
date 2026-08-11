(function (root) {
  'use strict';

  var HOME_FEED_RE = /\/(?:pl|en)\/home_brief\.json(?:\?|$)/i;
  var AI_OUTLOOK_URL = '/data/ai_outlook.json';

  function approvedPhoto(item) {
    var image = String(item && item.image || '').trim();
    return Boolean(
      item &&
      item.image_policy === 'source-linked-external' &&
      /^https:\/\//i.test(image)
    );
  }

  function filterPayload(payload) {
    if (!payload || typeof payload !== 'object') return payload;
    ['latest', 'radar'].forEach(function (section) {
      if (Array.isArray(payload[section])) {
        payload[section] = payload[section].filter(approvedPhoto);
      }
    });
    return payload;
  }

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

  function installFeedFilter() {
    if (typeof root.fetch !== 'function' || root.fetch.__brPhotoOnly) return;
    var originalFetch = root.fetch.bind(root);
    var filteredFetch = function (input, init) {
      return originalFetch(input, init).then(function (response) {
        var url = requestUrl(input);
        if (!url || !HOME_FEED_RE.test(url.pathname + url.search) || !response || !response.ok) {
          return response;
        }
        return response.clone().json().then(function (payload) {
          return new Response(JSON.stringify(filterPayload(payload)), {
            status: response.status,
            statusText: response.statusText,
            headers: response.headers
          });
        }).catch(function () {
          return response;
        });
      });
    };
    filteredFetch.__brPhotoOnly = true;
    filteredFetch.__brOriginalFetch = originalFetch;
    root.fetch = filteredFetch;
  }

  function removeCard(card) {
    if (card && card.parentNode) card.parentNode.removeChild(card);
  }

  function guardCard(card) {
    if (!card || card.dataset.brPhotoGuarded === '1') return;
    card.dataset.brPhotoGuarded = '1';
    var image = card.querySelector('.thumb.has-image img');
    if (!image || !String(image.getAttribute('src') || '').trim()) {
      removeCard(card);
      return;
    }
    image.addEventListener('error', function () {
      removeCard(card);
    }, { once: true });
    if (image.complete && image.naturalWidth === 0) removeCard(card);
  }

  function scan(scope) {
    var container = root.document && root.document.getElementById('latest-briefs');
    if (!container) return;
    container.dataset.homePhotoOnly = 'true';
    var target = scope && scope.querySelectorAll ? scope : container;
    if (target.matches && target.matches('.brief-card')) guardCard(target);
    Array.prototype.forEach.call(target.querySelectorAll('.brief-card'), guardCard);
  }

  function addOutlookStyles() {
    if (!root.document || root.document.getElementById('br-ai-outlook-style')) return;
    var style = root.document.createElement('style');
    style.id = 'br-ai-outlook-style';
    style.textContent = [
      '.ai-outlook{position:relative;margin:0 0 20px;overflow:hidden;border:1px solid rgba(127,200,255,.27);border-radius:20px;background:radial-gradient(430px 150px at 0 0,rgba(56,214,201,.17),transparent 68%),linear-gradient(145deg,rgba(17,50,73,.84),rgba(8,25,41,.92));box-shadow:0 16px 38px rgba(0,0,0,.23),inset 0 1px 0 rgba(255,255,255,.11)}',
      '.ai-outlook:before{content:"";position:absolute;inset:0;pointer-events:none;background:linear-gradient(110deg,transparent 0 47%,rgba(255,255,255,.03) 49%,transparent 52%)}',
      '.ai-outlook__inner{position:relative;z-index:1;padding:15px 17px}',
      '.ai-outlook__summary{display:grid;grid-template-columns:auto minmax(0,1fr) auto;align-items:center;gap:14px}',
      '.ai-outlook__brand{display:flex;align-items:center;gap:9px;min-width:122px}',
      '.ai-outlook__mark{display:grid;place-items:center;width:34px;height:34px;flex:0 0 34px;border:1px solid rgba(126,238,238,.34);border-radius:11px;background:linear-gradient(145deg,rgba(56,214,201,.25),rgba(127,200,255,.12));color:#8ffff6;font-size:14px;font-weight:950;box-shadow:inset 0 1px 0 rgba(255,255,255,.18)}',
      '.ai-outlook__eyebrow{color:#8ffff6;font-size:10px;font-weight:950;letter-spacing:.13em;text-transform:uppercase}',
      '.ai-outlook__date{margin-top:2px;color:#8fa5b8;font-size:10px}',
      '.ai-outlook__lead{min-width:0}',
      '.ai-outlook h2{margin:0 0 8px;font-size:clamp(18px,2vw,24px);line-height:1.14;letter-spacing:-.035em;display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden}',
      '.ai-outlook__metrics{display:flex;flex-wrap:wrap;gap:7px}',
      '.ai-outlook__metric{display:inline-flex;align-items:center;gap:6px;padding:5px 8px;border:1px solid rgba(255,255,255,.11);border-radius:999px;background:rgba(5,20,34,.36);white-space:nowrap}',
      '.ai-outlook__metric small{color:#8299ad;font-size:8px;font-weight:850;letter-spacing:.06em;text-transform:uppercase}',
      '.ai-outlook__metric strong{color:#eef8ff;font-size:11px}',
      '.ai-outlook__event{margin:8px 0 0;color:#b9cedd;font-size:11px;line-height:1.4}',
      '.ai-outlook__event b{color:#91e8e3;font-size:9px;letter-spacing:.06em;text-transform:uppercase}',
      '.ai-outlook__toggle{display:inline-flex;align-items:center;justify-content:center;gap:8px;min-width:100px;min-height:38px;padding:0 13px;border:1px solid rgba(56,214,201,.3);border-radius:12px;background:rgba(56,214,201,.09);color:#91fff6;font:inherit;font-size:12px;font-weight:900;cursor:pointer;transition:background .18s ease,border-color .18s ease,transform .18s ease}',
      '.ai-outlook__toggle:hover{transform:translateY(-1px);background:rgba(56,214,201,.16);border-color:rgba(56,214,201,.48)}',
      '.ai-outlook__chevron{font-size:12px;transition:transform .22s ease}',
      '.ai-outlook.is-expanded .ai-outlook__chevron{transform:rotate(180deg)}',
      '.ai-outlook__details{max-height:0;overflow:hidden;opacity:0;visibility:hidden;transform:translateY(-5px);transition:max-height .38s ease,opacity .24s ease,transform .24s ease,visibility 0s linear .38s}',
      '.ai-outlook.is-expanded .ai-outlook__details{max-height:1900px;opacity:1;visibility:visible;transform:none;transition:max-height .5s ease,opacity .28s ease .06s,transform .28s ease .06s,visibility 0s}',
      '.ai-outlook__details-inner{margin-top:15px;padding-top:15px;border-top:1px solid rgba(255,255,255,.09)}',
      '.ai-outlook__thesis{max-width:980px;margin:0 0 14px;color:#d7e6f2;font-size:14px;line-height:1.58}',
      '.ai-outlook__grid{display:grid;grid-template-columns:1.25fr 1fr 1fr;gap:10px}',
      '.ai-outlook__note{padding:12px 13px;border:1px solid rgba(255,255,255,.09);border-radius:14px;background:rgba(255,255,255,.035)}',
      '.ai-outlook__note b{display:block;margin-bottom:5px;color:#91e8e3;font-size:9px;letter-spacing:.07em;text-transform:uppercase}',
      '.ai-outlook__note p{margin:0;color:#afc0cf;font-size:11px;line-height:1.48}',
      '.ai-outlook__analysis-grid{display:grid;grid-template-columns:1.15fr 1fr 1fr;gap:10px;margin-bottom:10px}',
      '.ai-outlook__analysis-grid .ai-outlook__note{background:linear-gradient(145deg,rgba(56,214,201,.075),rgba(255,255,255,.025))}',
      '.ai-outlook__direction{margin:0 0 10px;padding:13px 14px;border:1px solid rgba(143,255,246,.16);border-radius:14px;background:rgba(5,20,34,.34)}',
      '.ai-outlook__direction-head{display:flex;align-items:baseline;justify-content:space-between;gap:12px;margin-bottom:6px}',
      '.ai-outlook__direction-head b{color:#91e8e3;font-size:9px;letter-spacing:.07em;text-transform:uppercase}',
      '.ai-outlook__direction-head span{color:#839caf;font-size:9px;text-align:right}',
      '.ai-outlook__direction p{margin:0;color:#bdcfdd;font-size:11px;line-height:1.5}',
      '.ai-outlook__scenarios{display:grid;gap:7px;margin-top:10px}',
      '.ai-outlook__scenario{display:grid;grid-template-columns:minmax(110px,.55fr) 42px 1.45fr;gap:9px;align-items:start;padding-top:7px;border-top:1px solid rgba(255,255,255,.07)}',
      '.ai-outlook__scenario b{color:#e8f7ff;font-size:10px}.ai-outlook__scenario strong{color:#8ffff6;font-size:11px}.ai-outlook__scenario span{color:#9db2c2;font-size:10px;line-height:1.4}',
      '.ai-outlook__footer{display:flex;align-items:flex-end;justify-content:space-between;gap:14px;margin-top:13px;padding-top:12px;border-top:1px solid rgba(255,255,255,.09)}',
      '.ai-outlook__sources{display:flex;flex-wrap:wrap;gap:8px}',
      '.ai-outlook__source{display:inline-flex;align-items:center;gap:5px;color:#78e4df;font-size:10px;font-weight:850}',
      '.ai-outlook__source:hover{color:#fff}',
      '.ai-outlook__disclaimer{max-width:360px;color:#71889c;font-size:8px;line-height:1.4;text-align:right}',
      '@media(max-width:850px){.ai-outlook__summary{grid-template-columns:auto minmax(0,1fr)}.ai-outlook__toggle{grid-column:1/-1;width:100%}.ai-outlook__grid,.ai-outlook__analysis-grid{grid-template-columns:1fr}.ai-outlook__footer{display:block}.ai-outlook__disclaimer{margin-top:10px;max-width:none;text-align:left}}',
      '@media(max-width:560px){.ai-outlook__inner{padding:14px}.ai-outlook__summary{grid-template-columns:1fr;gap:10px}.ai-outlook__brand{min-width:0}.ai-outlook h2{font-size:19px}.ai-outlook__metrics{gap:6px}.ai-outlook__metric{padding:5px 7px}.ai-outlook__toggle{grid-column:auto}.ai-outlook__thesis{font-size:13px}}',
      '@media(prefers-reduced-motion:reduce){.ai-outlook__details,.ai-outlook__toggle,.ai-outlook__chevron{transition:none!important}}'
    ].join('');
    root.document.head.appendChild(style);
  }

  function textElement(tag, className, text) {
    var element = root.document.createElement(tag);
    if (className) element.className = className;
    element.textContent = String(text || '');
    return element;
  }

  function localizedOutlook(payload) {
    var language = String(root.document.documentElement.lang || 'pl').toLowerCase().indexOf('en') === 0 ? 'en' : 'pl';
    var content = payload && payload[language];
    if (!content || typeof content !== 'object') return null;
    return { language: language, content: content };
  }

  function validHttps(value) {
    try {
      var url = new URL(String(value || ''), root.location.href);
      return url.protocol === 'https:' ? url.href : '';
    } catch (error) {
      return '';
    }
  }

  function metric(label, value) {
    var element = textElement('span', 'ai-outlook__metric', '');
    element.appendChild(textElement('small', '', label));
    element.appendChild(textElement('strong', '', value));
    return element;
  }

  function directionSummary(direction, language) {
    if (!direction || typeof direction !== 'object') {
      return language === 'pl' ? 'nieoszacowany osobno' : 'not separately estimated';
    }
    if (direction.status === 'estimated' && Array.isArray(direction.scenarios)) {
      var ranked = direction.scenarios.slice().sort(function (a, b) {
        return Number(b && b.probability || 0) - Number(a && a.probability || 0);
      });
      if (ranked[0]) return String(ranked[0].label || '') + ' ' + String(ranked[0].probability || '') + '%';
    }
    if (direction.status === 'embedded_in_event') {
      return language === 'pl' ? 'ujęty w zdarzeniu' : 'included in event';
    }
    if (direction.status === 'insufficient_evidence') {
      return language === 'pl' ? 'brak podstaw do oceny' : 'insufficient evidence';
    }
    return '';
  }

  function addNote(container, label, value) {
    if (!value) return;
    var note = textElement('div', 'ai-outlook__note', '');
    note.appendChild(textElement('b', '', label));
    note.appendChild(textElement('p', '', value));
    container.appendChild(note);
  }

  function renderAiOutlook(payload) {
    if (!root.document || root.document.getElementById('ai-outlook')) return;
    var selected = localizedOutlook(payload);
    if (!selected) return;

    var language = selected.language;
    var item = selected.content;
    if (!item.title || !item.thesis || !item.horizon || !item.probability) return;

    var mainHead = root.document.querySelector('main .main-head');
    if (!mainHead || !mainHead.parentNode) return;

    addOutlookStyles();

    var section = root.document.createElement('section');
    section.id = 'ai-outlook';
    section.className = 'ai-outlook';
    section.setAttribute('aria-labelledby', 'ai-outlook-title');

    var inner = textElement('div', 'ai-outlook__inner', '');
    var summary = textElement('div', 'ai-outlook__summary', '');

    var brand = textElement('div', 'ai-outlook__brand', '');
    brand.appendChild(textElement('span', 'ai-outlook__mark', 'AI'));
    var brandText = textElement('div', '', '');
    brandText.appendChild(textElement('div', 'ai-outlook__eyebrow', 'AI Outlook'));
    brandText.appendChild(textElement('div', 'ai-outlook__date', item.date_label || payload.date || ''));
    brand.appendChild(brandText);
    summary.appendChild(brand);

    var lead = textElement('div', 'ai-outlook__lead', '');
    var title = textElement('h2', '', item.title);
    title.id = 'ai-outlook-title';
    lead.appendChild(title);
    var metrics = textElement('div', 'ai-outlook__metrics', '');
    metrics.appendChild(metric(language === 'pl' ? 'Prawdopodobieństwo' : 'Probability', String(item.probability) + '%'));
    if (item.assessment_perspective) {
      metrics.appendChild(metric(language === 'pl' ? 'Perspektywa' : 'Perspective', item.assessment_perspective));
    }
    if (item.assessment_confidence) {
      metrics.appendChild(metric(language === 'pl' ? 'Pewność oceny' : 'Assessment confidence', item.assessment_confidence));
    }
    metrics.appendChild(metric(language === 'pl' ? 'Horyzont' : 'Horizon', item.horizon));
    metrics.appendChild(metric(language === 'pl' ? 'Obszar' : 'Area', item.category || (language === 'pl' ? 'Gospodarka' : 'Economy')));
    var directionHeadline = directionSummary(item.direction, language);
    if (directionHeadline && !item.assessment_perspective) {
      metrics.appendChild(metric(language === 'pl' ? 'Kierunek' : 'Direction', directionHeadline));
    }
    lead.appendChild(metrics);
    var probabilityEvent = item.probability_event;
    if (!probabilityEvent && item.resolution && item.resolution.metric) {
      probabilityEvent = String(item.resolution.metric) + (item.resolution.resolution_date
        ? (language === 'pl' ? ' do ' : ' by ') + String(item.resolution.resolution_date)
        : '');
    }
    if (probabilityEvent) {
      var eventLine = textElement('p', 'ai-outlook__event', '');
      eventLine.appendChild(textElement('b', '', language === 'pl' ? String(item.probability) + '% dotyczy: ' : String(item.probability) + '% measures: '));
      eventLine.appendChild(root.document.createTextNode(String(probabilityEvent)));
      lead.appendChild(eventLine);
    }
    summary.appendChild(lead);

    var detailsId = 'ai-outlook-details';
    var toggle = textElement('button', 'ai-outlook__toggle', '');
    toggle.type = 'button';
    toggle.setAttribute('aria-expanded', 'false');
    toggle.setAttribute('aria-controls', detailsId);
    var toggleLabel = textElement('span', 'ai-outlook__toggle-label', language === 'pl' ? 'Rozwiń' : 'Expand');
    toggle.appendChild(toggleLabel);
    toggle.appendChild(textElement('span', 'ai-outlook__chevron', '▼'));
    summary.appendChild(toggle);
    inner.appendChild(summary);

    var details = textElement('div', 'ai-outlook__details', '');
    details.id = detailsId;
    details.setAttribute('aria-hidden', 'true');
    var detailsInner = textElement('div', 'ai-outlook__details-inner', '');
    detailsInner.appendChild(textElement('p', 'ai-outlook__thesis', item.thesis));

    var analysis = textElement('div', 'ai-outlook__analysis-grid', '');
    addNote(analysis, language === 'pl' ? 'Wniosek AI' : 'AI conclusion', item.analysis_summary);
    addNote(analysis, language === 'pl' ? 'Skutki' : 'Implications', item.impact);
    addNote(analysis, language === 'pl' ? 'Co zmieni ocenę' : 'What would change the assessment', item.watch_items);
    if (analysis.childNodes.length) detailsInner.appendChild(analysis);

    var direction = item.direction;
    if (direction && typeof direction === 'object' && direction.explanation) {
      var directionBox = textElement('div', 'ai-outlook__direction', '');
      var directionHead = textElement('div', 'ai-outlook__direction-head', '');
      directionHead.appendChild(textElement('b', '', language === 'pl' ? 'Ocena kierunku' : 'Direction assessment'));
      directionHead.appendChild(textElement('span', '', direction.perspective || ''));
      directionBox.appendChild(directionHead);
      directionBox.appendChild(textElement('p', '', direction.explanation));
      if (direction.status === 'estimated' && Array.isArray(direction.scenarios)) {
        var scenarioList = textElement('div', 'ai-outlook__scenarios', '');
        direction.scenarios.forEach(function (scenario) {
          if (!scenario || !scenario.label) return;
          var row = textElement('div', 'ai-outlook__scenario', '');
          row.appendChild(textElement('b', '', scenario.label));
          row.appendChild(textElement('strong', '', String(scenario.probability) + '%'));
          row.appendChild(textElement('span', '', scenario.meaning || ''));
          scenarioList.appendChild(row);
        });
        directionBox.appendChild(scenarioList);
      }
      detailsInner.appendChild(directionBox);
    }

    var notes = textElement('div', 'ai-outlook__grid', '');
    [
      [language === 'pl' ? 'Mechanizm' : 'Mechanism', item.rationale],
      [language === 'pl' ? 'Warunek trafności' : 'Success condition', item.confirmation],
      [language === 'pl' ? 'Warunek nietrafności' : 'Failure condition', item.invalidation]
    ].forEach(function (pair) {
      addNote(notes, pair[0], pair[1]);
    });
    detailsInner.appendChild(notes);

    var footer = textElement('div', 'ai-outlook__footer', '');
    var sources = textElement('div', 'ai-outlook__sources', '');
    (Array.isArray(item.sources) ? item.sources : []).slice(0, 3).forEach(function (source) {
      var href = validHttps(source && source.url);
      if (!href) return;
      var link = textElement('a', 'ai-outlook__source', (source.name || 'Source') + ' ↗');
      link.href = href;
      link.target = '_blank';
      link.rel = 'noopener noreferrer external';
      sources.appendChild(link);
    });
    footer.appendChild(sources);
    footer.appendChild(textElement(
      'div',
      'ai-outlook__disclaimer',
      language === 'pl'
        ? 'Prognoza AI oparta na wskazanych źródłach. Nie jest faktem ani poradą inwestycyjną.'
        : 'An AI forecast based on the listed sources. It is neither a fact nor investment advice.'
    ));
    detailsInner.appendChild(footer);
    details.appendChild(detailsInner);
    inner.appendChild(details);
    section.appendChild(inner);

    toggle.addEventListener('click', function () {
      var expanded = section.classList.toggle('is-expanded');
      toggle.setAttribute('aria-expanded', String(expanded));
      details.setAttribute('aria-hidden', String(!expanded));
      toggleLabel.textContent = expanded
        ? (language === 'pl' ? 'Zwiń' : 'Collapse')
        : (language === 'pl' ? 'Rozwiń' : 'Expand');
    });

    mainHead.insertAdjacentElement('afterend', section);
  }

  function loadAiOutlook() {
    if (!root.document || typeof root.fetch !== 'function') return;
    var day = new Date().toISOString().slice(0, 10);
    root.fetch(AI_OUTLOOK_URL + '?v=' + encodeURIComponent(day), { cache: 'no-store' })
      .then(function (response) {
        if (!response.ok) throw new Error('AI Outlook unavailable');
        return response.json();
      })
      .then(renderAiOutlook)
      .catch(function () {
        // A missing forecast must never block or damage the homepage.
      });
  }

  function start() {
    scan(root.document);
    loadAiOutlook();
    var container = root.document.getElementById('latest-briefs');
    if (container && typeof MutationObserver === 'function') {
      new MutationObserver(function (records) {
        records.forEach(function (record) {
          Array.prototype.forEach.call(record.addedNodes || [], function (node) {
            if (node && node.nodeType === 1) scan(node);
          });
        });
      }).observe(container, { childList: true, subtree: true });
    }
  }

  root.BriefRoomsHomepagePhotoOnly = {
    approvedPhoto: approvedPhoto,
    filterPayload: filterPayload,
    guardCard: guardCard,
    scan: scan,
    renderAiOutlook: renderAiOutlook,
    loadAiOutlook: loadAiOutlook
  };

  installFeedFilter();
  if (root.document) {
    if (root.document.readyState === 'loading') {
      root.document.addEventListener('DOMContentLoaded', start, { once: true });
    } else {
      start();
    }
  }
})(typeof globalThis !== 'undefined' ? globalThis : this);
