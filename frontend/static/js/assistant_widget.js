/*
  assistant_widget.js
  -------------------
  The public chat widget (Module 3, CP3). No libraries, no build step.

  Rules this file keeps:

  * Nothing from the API is ever written as HTML. Every visitor message, answer,
    citation and error goes in with textContent; the script has no way to inject
    markup at all.
  * The widget is invisible until /assistant/chat/state/ says it is on. With the
    default switches (all off) or no JavaScript, nothing shows.
  * Replies are streamed as server-sent events, but only after the server has
    verified the whole answer. The browser never relays provider output.
  * The proof-of-work check is solved here, in small slices, so a phone stays
    responsive. A solved nonce is reused only for the challenge it belongs to.
  * English and Kiswahili UI words live in TEXT; the opening line, consent text
    and suggestions come from the server in both languages.
*/
(function () {
  'use strict';

  var root = document.getElementById('assistant-widget');
  if (!root) { return; }

  var URLS = {
    state: root.getAttribute('data-state-url'),
    send: root.getAttribute('data-send-url'),
    newConversation: root.getAttribute('data-new-url'),
    feedback: root.getAttribute('data-feedback-url'),
    callback: root.getAttribute('data-callback-url')
  };
  var SCHOOL = root.getAttribute('data-school') || 'the school';
  var LANG_KEY = 'aw-lang';
  var FETCH_TIMEOUT_MS = 60000;
  var SLOW_ANSWER_MS = 10000;

  var TEXT = {
    en: {
      askSchool: 'Ask the school',
      aiNotice: 'AI assistant — not a person',
      switchToSw: 'Switch to Kiswahili',
      switchToEn: 'Switch to English',
      closeChat: 'Close the chat',
      tryAsking: 'Try asking',
      yourQuestion: 'Your question',
      placeholder: 'Type your question',
      send: 'Send',
      talkToPerson: 'Talk to a person',
      startAgain: 'Start again',
      youSaid: 'You said:',
      assistantSaid: 'Assistant said:',
      sources: 'Sources',
      feedbackPrompt: 'Was this answer helpful?',
      helpful: 'Helpful',
      notHelpful: 'Not helpful',
      report: 'Report a problem',
      reasonLabel: 'What went wrong? (optional)',
      sendFeedback: 'Send feedback',
      thanks: 'Thank you — your feedback helps the school improve.',
      thinking: 'The assistant is answering…',
      stillThinking: 'Still working on the answer…',
      checking: 'Checking…',
      offline: 'We could not reach the school. Check your connection and try again.',
      restoreHint: 'Your question is back in the box — press send to try again.',
      newStarted: 'A new conversation has started.',
      callbackTitle: 'Ask the school to contact you',
      callbackHelp: 'Leave your details and a member of staff will get back to you. Only the school office can see them.',
      nameLabel: 'Your name',
      phoneLabel: 'Phone number',
      emailLabel: 'Email address',
      sendRequest: 'Send request',
      cancel: 'Cancel',
      sending: 'Sending…',
      contactMissing: 'Give a phone number or an email address.',
      callbackSent: 'Thank you. The school has your request and will contact you.',
      callbackFailed: 'The request could not be sent. Please try again.'
    },
    sw: {
      askSchool: 'Uliza shule',
      aiNotice: 'Msaidizi wa AI — si mtu',
      switchToSw: 'Badilisha kwenda Kiswahili',
      switchToEn: 'Badilisha kwenda Kiingereza',
      closeChat: 'Funga mazungumzo',
      tryAsking: 'Jaribu kuuliza',
      yourQuestion: 'Swali lako',
      placeholder: 'Andika swali lako',
      send: 'Tuma',
      talkToPerson: 'Zungumza na mtu',
      startAgain: 'Anza upya',
      youSaid: 'Ulisema:',
      assistantSaid: 'Msaidizi alisema:',
      sources: 'Vyanzo',
      feedbackPrompt: 'Jibu hili lilikusaidia?',
      helpful: 'Ilisaidia',
      notHelpful: 'Haikunisaidia',
      report: 'Ripoti tatizo',
      reasonLabel: 'Nini hakikuenda vizuri? (si lazima)',
      sendFeedback: 'Tuma maoni',
      thanks: 'Asante — maoni yako hutusaidia shule kuboresha.',
      thinking: 'Msaidizi anajibu…',
      stillThinking: 'Bado anafanya kazi kwenye jibu…',
      checking: 'Inakagua…',
      offline: 'Hatukuweza kufikia shule. Angalia mtandao wako na ujaribu tena.',
      restoreHint: 'Swali lako limerudishwa kwenye kisanduku — bonyeza tuma kujaribu tena.',
      newStarted: 'Mazungumzo mapya yameanza.',
      callbackTitle: 'Omba shule ikwasiliane nawe',
      callbackHelp: 'Acha maelezo yako na mfanyakazi wa shule atakujibu. Ofisi ya shule pekee inaweza kuyaona.',
      nameLabel: 'Jina lako',
      phoneLabel: 'Namba ya simu',
      emailLabel: 'Barua pepe',
      sendRequest: 'Tuma ombi',
      cancel: 'Ghairi',
      sending: 'Inatuma…',
      contactMissing: 'Toa namba ya simu au barua pepe.',
      callbackSent: 'Asante. Shule imepokea ombi lako na itakuwasiliana nawe.',
      callbackFailed: 'Ombi halikuweza kutumwa. Tafadhali jaribu tena.'
    }
  };

  var els = {
    launcher: document.getElementById('aw-launcher'),
    panel: document.getElementById('aw-panel'),
    close: document.getElementById('aw-close'),
    lang: document.getElementById('aw-lang'),
    consent: document.getElementById('aw-consent'),
    log: document.getElementById('aw-log'),
    status: document.getElementById('aw-status'),
    suggests: document.getElementById('aw-suggests'),
    chips: document.getElementById('aw-chips'),
    composer: document.getElementById('aw-composer'),
    input: document.getElementById('aw-input'),
    send: document.getElementById('aw-send'),
    person: document.getElementById('aw-person'),
    restart: document.getElementById('aw-new'),
    callback: document.getElementById('aw-callback'),
    callbackForm: document.getElementById('aw-callback-form'),
    callbackPurpose: document.getElementById('aw-callback-purpose'),
    callbackMsg: document.getElementById('aw-callback-msg'),
    callbackSend: document.getElementById('aw-callback-send'),
    callbackCancel: document.getElementById('aw-callback-cancel')
  };

  var state = null;
  var lang = readLang();
  var busy = false;
  var openingShown = false;
  var powSolved = { challenge: '', nonce: '' };
  var powPromise = null;
  var refreshPromise = null;
  var slowTimer = null;

  // ── Language ──────────────────────────────────────────────────────────

  function readLang() {
    try {
      var saved = window.localStorage.getItem(LANG_KEY);
      if (saved === 'en' || saved === 'sw') { return saved; }
    } catch (err) { /* storage can be blocked; fall through */ }
    return String(document.documentElement.lang || '').toLowerCase().indexOf('sw') === 0 ? 'sw' : 'en';
  }

  function t(key) {
    var pack = TEXT[lang] || TEXT.en;
    return pack[key] || TEXT.en[key] || '';
  }

  function applyLanguage() {
    var nodes = root.querySelectorAll('[data-i18n]');
    for (var i = 0; i < nodes.length; i++) {
      nodes[i].textContent = t(nodes[i].getAttribute('data-i18n'));
    }
    var labelled = root.querySelectorAll('[data-i18n-label]');
    for (var j = 0; j < labelled.length; j++) {
      labelled[j].setAttribute('aria-label', t(labelled[j].getAttribute('data-i18n-label')));
    }
    if (els.input) {
      els.input.setAttribute('placeholder', t('placeholder'));
      els.input.setAttribute('lang', lang);
    }
    if (els.panel) { els.panel.setAttribute('lang', lang); }
    if (els.lang) {
      els.lang.textContent = lang === 'sw' ? 'EN' : 'SW';
      els.lang.setAttribute('aria-label', lang === 'sw' ? t('switchToEn') : t('switchToSw'));
    }
    renderConsent();
    refreshOpening();
    renderSuggestions();
  }

  function setLanguage(next) {
    lang = next === 'sw' ? 'sw' : 'en';
    try { window.localStorage.setItem(LANG_KEY, lang); } catch (err) { /* ignore */ }
    applyLanguage();
  }

  // ── Small helpers ─────────────────────────────────────────────────────

  function el(tag, cls, text) {
    var node = document.createElement(tag);
    if (cls) { node.className = cls; }
    if (text !== undefined && text !== null) { node.textContent = text; }
    return node;
  }

  function csrfToken() {
    var match = document.cookie.match(/(?:^|;\s*)csrftoken=([^;]+)/);
    return match ? decodeURIComponent(match[1]) : '';
  }

  function jsonHeaders() {
    var headers = { 'Content-Type': 'application/json', 'Accept': 'application/json' };
    var token = csrfToken();
    if (token) { headers['X-CSRFToken'] = token; }
    return headers;
  }

  function request(url, options) {
    var controller = typeof AbortController === 'function' ? new AbortController() : null;
    var opts = options || {};
    if (controller) { opts.signal = controller.signal; }
    var timer = window.setTimeout(function () {
      if (controller) { controller.abort(); }
    }, FETCH_TIMEOUT_MS);
    return window.fetch(url, opts).then(
      function (response) {
        window.clearTimeout(timer);
        return response;
      },
      function (error) {
        window.clearTimeout(timer);
        throw error;
      }
    );
  }

  function readJSON(response) {
    return response.json().catch(function () { return {}; }).then(function (data) {
      return { status: response.status, data: data || {} };
    });
  }

  function announce(text) {
    if (els.status) { els.status.textContent = text || ''; }
  }

  function scrollToEnd() {
    if (els.log) { els.log.scrollTop = els.log.scrollHeight; }
  }

  function hideSuggestions() {
    if (els.suggests) { els.suggests.hidden = true; }
  }

  function showSuggestions() {
    renderSuggestions();
    if (els.suggests && els.chips && els.chips.childElementCount) {
      els.suggests.hidden = false;
    }
  }

  // ── Proof of work ─────────────────────────────────────────────────────

  var K = [
    0x428a2f98, 0x71374491, 0xb5c0fbcf, 0xe9b5dba5, 0x3956c25b, 0x59f111f1,
    0x923f82a4, 0xab1c5ed5, 0xd807aa98, 0x12835b01, 0x243185be, 0x550c7dc3,
    0x72be5d74, 0x80deb1fe, 0x9bdc06a7, 0xc19bf174, 0xe49b69c1, 0xefbe4786,
    0x0fc19dc6, 0x240ca1cc, 0x2de92c6f, 0x4a7484aa, 0x5cb0a9dc, 0x76f988da,
    0x983e5152, 0xa831c66d, 0xb00327c8, 0xbf597fc7, 0xc6e00bf3, 0xd5a79147,
    0x06ca6351, 0x14292967, 0x27b70a85, 0x2e1b2138, 0x4d2c6dfc, 0x53380d13,
    0x650a7354, 0x766a0abb, 0x81c2c92e, 0x92722c85, 0xa2bfe8a1, 0xa81a664b,
    0xc24b8b70, 0xc76c51a3, 0xd192e819, 0xd6990624, 0xf40e3585, 0x106aa070,
    0x19a4c116, 0x1e376c08, 0x2748774c, 0x34b0bcb5, 0x391c0cb3, 0x4ed8aa4a,
    0x5b9cca4f, 0x682e6ff3, 0x748f82ee, 0x78a5636f, 0x84c87814, 0x8cc70208,
    0x90befffa, 0xa4506ceb, 0xbef9a3f7, 0xc67178f2
  ];

  function rotr(value, bits) {
    return (value >>> bits) | (value << (32 - bits));
  }

  function utf8Bytes(text) {
    var out = [];
    for (var i = 0; i < text.length; i++) {
      var code = text.charCodeAt(i);
      if (code < 0x80) {
        out.push(code);
      } else if (code < 0x800) {
        out.push(0xc0 | (code >> 6), 0x80 | (code & 63));
      } else if (code >= 0xd800 && code <= 0xdbff && i + 1 < text.length) {
        var low = text.charCodeAt(++i);
        var point = 0x10000 + ((code - 0xd800) << 10) + (low - 0xdc00);
        out.push(
          0xf0 | (point >> 18),
          0x80 | ((point >> 12) & 63),
          0x80 | ((point >> 6) & 63),
          0x80 | (point & 63)
        );
      } else {
        out.push(0xe0 | (code >> 12), 0x80 | ((code >> 6) & 63), 0x80 | (code & 63));
      }
    }
    return out;
  }

  function sha256Hex(text) {
    var bytes = utf8Bytes(text);
    var length = bytes.length;
    var blocks = Math.ceil((length + 9) / 64);
    var size = blocks * 64;
    var message = new Array(size);
    var i;
    for (i = 0; i < size; i++) { message[i] = 0; }
    for (i = 0; i < length; i++) { message[i] = bytes[i]; }
    message[length] = 0x80;
    var bitsHigh = Math.floor(length * 8 / 4294967296);
    var bitsLow = (length * 8) >>> 0;
    message[size - 8] = (bitsHigh >>> 24) & 255;
    message[size - 7] = (bitsHigh >>> 16) & 255;
    message[size - 6] = (bitsHigh >>> 8) & 255;
    message[size - 5] = bitsHigh & 255;
    message[size - 4] = (bitsLow >>> 24) & 255;
    message[size - 3] = (bitsLow >>> 16) & 255;
    message[size - 2] = (bitsLow >>> 8) & 255;
    message[size - 1] = bitsLow & 255;

    var hash = [0x6a09e667, 0xbb67ae85, 0x3c6ef372, 0xa54ff53a,
                0x510e527f, 0x9b05688c, 0x1f83d9ab, 0x5be0cd19];
    var w = new Array(64);

    for (var block = 0; block < blocks; block++) {
      var offset = block * 64;
      var t;
      for (t = 0; t < 16; t++) {
        w[t] = ((message[offset + t * 4] << 24) |
                (message[offset + t * 4 + 1] << 16) |
                (message[offset + t * 4 + 2] << 8) |
                message[offset + t * 4 + 3]) | 0;
      }
      for (t = 16; t < 64; t++) {
        var s0 = rotr(w[t - 15], 7) ^ rotr(w[t - 15], 18) ^ (w[t - 15] >>> 3);
        var s1 = rotr(w[t - 2], 17) ^ rotr(w[t - 2], 19) ^ (w[t - 2] >>> 10);
        w[t] = (w[t - 16] + s0 + w[t - 7] + s1) | 0;
      }

      var a = hash[0], b = hash[1], c = hash[2], d = hash[3];
      var e = hash[4], f = hash[5], g = hash[6], h = hash[7];

      for (t = 0; t < 64; t++) {
        var bigS1 = rotr(e, 6) ^ rotr(e, 11) ^ rotr(e, 25);
        var choose = (e & f) ^ (~e & g);
        var temp1 = (h + bigS1 + choose + K[t] + w[t]) | 0;
        var bigS0 = rotr(a, 2) ^ rotr(a, 13) ^ rotr(a, 22);
        var majority = (a & b) ^ (a & c) ^ (b & c);
        var temp2 = (bigS0 + majority) | 0;

        h = g; g = f; f = e;
        e = (d + temp1) | 0;
        d = c; c = b; b = a;
        a = (temp1 + temp2) | 0;
      }

      hash[0] = (hash[0] + a) | 0;
      hash[1] = (hash[1] + b) | 0;
      hash[2] = (hash[2] + c) | 0;
      hash[3] = (hash[3] + d) | 0;
      hash[4] = (hash[4] + e) | 0;
      hash[5] = (hash[5] + f) | 0;
      hash[6] = (hash[6] + g) | 0;
      hash[7] = (hash[7] + h) | 0;
    }

    var hex = '';
    for (var p = 0; p < 8; p++) {
      hex += ('00000000' + (hash[p] >>> 0).toString(16)).slice(-8);
    }
    return hex;
  }

  function solvePow(challenge, difficulty) {
    return new Promise(function (resolve) {
      var zeros = '';
      for (var z = 0; z < difficulty; z++) { zeros += '0'; }
      var nonce = 0;
      var BATCH = 1500;
      function step() {
        for (var i = 0; i < BATCH; i++) {
          if (sha256Hex(challenge + ':' + nonce).slice(0, difficulty) === zeros) {
            resolve(String(nonce));
            return;
          }
          nonce++;
        }
        window.setTimeout(step, 0);
      }
      step();
    });
  }

  function powFields() {
    if (!state || !state.pow || !state.pow.challenge) {
      return Promise.resolve({ pow_challenge: '', pow_nonce: '' });
    }
    var challenge = String(state.pow.challenge);
    var difficulty = Number(state.pow.difficulty) || 0;
    if (powSolved.challenge === challenge) {
      return Promise.resolve({ pow_challenge: challenge, pow_nonce: powSolved.nonce });
    }
    if (powPromise && powPromise.challenge === challenge) {
      return powPromise.promise;
    }
    announce(t('checking'));
    var promise = solvePow(challenge, difficulty).then(function (nonce) {
      powSolved = { challenge: challenge, nonce: nonce };
      powPromise = null;
      return { pow_challenge: challenge, pow_nonce: nonce };
    });
    powPromise = { challenge: challenge, promise: promise };
    return promise;
  }

  // ── State ─────────────────────────────────────────────────────────────

  function loadState() {
    return request(URLS.state, { method: 'GET', headers: { 'Accept': 'application/json' }, credentials: 'same-origin' })
      .then(readJSON)
      .then(function (result) {
        var payload = result.data;
        if (result.status !== 200 || !payload.enabled) {
          hideWidget();
          return null;
        }
        state = payload;
        if (els.input && payload.max_chars) {
          els.input.setAttribute('maxlength', String(payload.max_chars));
        }
        return state;
      })
      .catch(function () {
        hideWidget();
        return null;
      });
  }

  function refreshState() {
    if (refreshPromise) { return refreshPromise; }
    refreshPromise = request(URLS.state, { method: 'GET', headers: { 'Accept': 'application/json' }, credentials: 'same-origin' })
      .then(readJSON)
      .then(function (result) {
        refreshPromise = null;
        if (result.status === 200 && result.data.enabled) {
          state = result.data;
          if (els.input && state.max_chars) {
            els.input.setAttribute('maxlength', String(state.max_chars));
          }
        } else if (result.status === 200) {
          hideWidget();
        }
        return state;
      })
      .catch(function () {
        refreshPromise = null;
        return state;
      });
    return refreshPromise;
  }

  function hideWidget() {
    if (els.launcher) { els.launcher.hidden = true; }
    if (els.panel) { els.panel.hidden = true; }
  }

  function revealLauncher() {
    if (els.launcher) { els.launcher.hidden = false; }
  }

  // ── Rendering ─────────────────────────────────────────────────────────

  function renderConsent() {
    if (!els.consent) { return; }
    var text = state && state.consent ? state.consent[lang] || state.consent.en : '';
    els.consent.textContent = text || '';
    els.consent.hidden = !text;
    els.consent.setAttribute('lang', lang);
  }

  function renderSuggestions() {
    if (!els.chips || !els.suggests) { return; }
    els.chips.textContent = '';
    var items = state && state.suggestions ? state.suggestions : [];
    if (!items.length) {
      els.suggests.hidden = true;
      return;
    }
    items.forEach(function (item) {
      var label = lang === 'sw' && item.sw ? item.sw : item.en;
      var button = el('button', 'aw-chip', label);
      button.type = 'button';
      button.addEventListener('click', function () {
        sendQuestion(item.prompt || item.en);
      });
      els.chips.appendChild(button);
    });
  }

  function typingBubble() {
    var bubble = el('p', 'aw-msg aw-msg--assistant');
    bubble.setAttribute('aria-busy', 'true');
    var dots = el('span', 'aw-typing');
    dots.appendChild(el('span'));
    dots.appendChild(el('span'));
    dots.appendChild(el('span'));
    bubble.appendChild(dots);
    return bubble;
  }

  function addVisitor(text) {
    var bubble = el('p', 'aw-msg aw-msg--visitor');
    var who = el('span', 'aw-sr', t('youSaid'));
    who.setAttribute('data-i18n', 'youSaid');
    bubble.appendChild(who);
    bubble.appendChild(document.createTextNode(text));
    els.log.appendChild(bubble);
    scrollToEnd();
    return bubble;
  }

  function addNotice(text, cls) {
    var bubble = el('p', 'aw-msg aw-msg--notice' + (cls ? ' ' + cls : ''), text);
    els.log.appendChild(bubble);
    scrollToEnd();
    return bubble;
  }

  function attachSources(body, citations) {
    if (!citations || !citations.length) { return; }
    var list = el('ul', 'aw-sources');
    list.setAttribute('aria-label', t('sources'));
    list.setAttribute('data-i18n-label', 'sources');
    citations.forEach(function (citation) {
      var line = (citation.label ? citation.label + ' — ' : '') + (citation.title || '');
      list.appendChild(el('li', null, line));
    });
    body.appendChild(list);
  }

  function attachFeedback(body, messageId) {
    if (!state || !state.feedback || !messageId) { return; }
    var box = el('div', 'aw-feedback');
    var prompt = el('span', 'aw-sr', t('feedbackPrompt'));
    prompt.setAttribute('data-i18n', 'feedbackPrompt');
    box.appendChild(prompt);

    function chip(key, handler) {
      var button = el('button', 'aw-chip', t(key));
      button.type = 'button';
      button.setAttribute('data-i18n', key);
      button.addEventListener('click', handler);
      box.appendChild(button);
      return button;
    }

    function replaceWithThanks() {
      box.textContent = '';
      var thanks = el('p', 'aw-thanks', t('thanks'));
      thanks.setAttribute('data-i18n', 'thanks');
      box.appendChild(thanks);
    }

    function send(kind, rating, reason) {
      request(URLS.feedback, {
        method: 'POST',
        headers: jsonHeaders(),
        credentials: 'same-origin',
        body: JSON.stringify({
          message_id: messageId,
          kind: kind,
          rating: rating,
          reason: reason || ''
        })
      })
        .then(readJSON)
        .then(function (result) {
          if (result.status === 200 && result.data.ok) {
            replaceWithThanks();
            announce(t('thanks'));
          }
        })
        .catch(function () { /* the chips stay; the visitor can try again */ });
    }

    function reasonForm(kind, rating) {
      var form = el('div', 'aw-feedback');
      var field = el('label', 'aw-field');
      var reason = el('span', 'aw-sr', t('reasonLabel'));
      reason.setAttribute('data-i18n', 'reasonLabel');
      field.appendChild(reason);
      var input = document.createElement('textarea');
      input.rows = 2;
      input.maxLength = 1000;
      input.setAttribute('placeholder', t('reasonLabel'));
      field.appendChild(input);
      form.appendChild(field);
      var sendButton = el('button', 'aw-chip', t('sendFeedback'));
      sendButton.type = 'button';
      sendButton.setAttribute('data-i18n', 'sendFeedback');
      sendButton.addEventListener('click', function () {
        send(kind, rating, input.value);
      });
      form.appendChild(sendButton);
      box.appendChild(form);
      input.focus();
    }

    chip('helpful', function () { send('rating', 'helpful', ''); });
    chip('notHelpful', function () { reasonForm('rating', 'not_helpful'); });
    chip('report', function () { reasonForm('report', ''); });
    body.appendChild(box);
  }

  function renderMessage(message) {
    var bubble;
    var who;
    if (message.role === 'visitor') {
      bubble = el('p', 'aw-msg aw-msg--visitor');
      who = el('span', 'aw-sr', t('youSaid'));
      who.setAttribute('data-i18n', 'youSaid');
    } else {
      bubble = el('p', 'aw-msg aw-msg--assistant');
      who = el('span', 'aw-sr', t('assistantSaid'));
      who.setAttribute('data-i18n', 'assistantSaid');
    }
    bubble.appendChild(who);
    bubble.appendChild(document.createTextNode(message.body || ''));
    if (message.role === 'assistant' && message.citations && message.citations.length) {
      attachSources(bubble, message.citations);
    }
    els.log.appendChild(bubble);
    if (message.role === 'assistant') {
      attachFeedback(bubble, message.id);
    }
  }

  function renderOpening() {
    var text = state && state.opening ? state.opening[lang] || state.opening.en : '';
    if (!text) { return; }
    openingShown = true;
    var bubble = el('p', 'aw-msg aw-msg--assistant');
    bubble.setAttribute('data-opening', '1');
    bubble.appendChild(document.createTextNode(text));
    els.log.appendChild(bubble);
  }

  function refreshOpening() {
    if (!openingShown || !els.log) { return; }
    var node = els.log.querySelector('[data-opening]');
    if (!node || !state || !state.opening) { return; }
    node.textContent = state.opening[lang] || state.opening.en;
  }

  function renderConversation() {
    els.log.textContent = '';
    openingShown = false;
    var messages = state && state.conversation ? state.conversation.messages : [];
    if (messages.length) {
      messages.forEach(renderMessage);
      hideSuggestions();
    } else {
      renderOpening();
      showSuggestions();
    }
    scrollToEnd();
  }

  // ── Sending ───────────────────────────────────────────────────────────

  function parseEventBlock(block) {
    var event = 'message';
    var data = '';
    block.split('\n').forEach(function (line) {
      if (line.indexOf('event:') === 0) {
        event = line.slice(6).trim();
      } else if (line.indexOf('data:') === 0) {
        data += line.slice(5).trim();
      }
    });
    if (!data) { return null; }
    try {
      return { event: event, data: JSON.parse(data) };
    } catch (err) {
      return null;
    }
  }

  function finishReply(bubble, reply) {
    bubble.removeAttribute('aria-busy');
    bubble.textContent = '';
    bubble.appendChild(el('span', 'aw-sr', t('assistantSaid')));
    bubble.appendChild(document.createTextNode(reply.body || ''));
    if (reply.citations && reply.citations.length) {
      attachSources(bubble, reply.citations);
    }
    attachFeedback(bubble, reply.id);
    announce(reply.body || '');
    scrollToEnd();
  }

  function restoreQuestion(text) {
    if (!text) { return; }
    els.input.value = text;
    els.input.focus();
  }

  function sendQuestion(text) {
    if (busy) { return; }
    var question = String(text || els.input.value || '').trim();
    if (!question) { return; }
    busy = true;
    hideSuggestions();
    announce(t('thinking'));
    els.send.disabled = true;
    els.input.value = '';
    addVisitor(question);
    var bubble = typingBubble();
    els.log.appendChild(bubble);
    scrollToEnd();

    slowTimer = window.setTimeout(function () {
      if (busy) { announce(t('stillThinking')); }
    }, SLOW_ANSWER_MS);

    powFields()
      .then(function (pow) {
        var payload = {
          message: question,
          website: document.getElementById('aw-website') ? document.getElementById('aw-website').value : '',
          pow_challenge: pow.pow_challenge,
          pow_nonce: pow.pow_nonce,
          stream: true
        };
        return streamReply(payload, bubble, question);
      })
      .catch(function () {
        showFailure(bubble, question);
      })
      .then(function () {
        window.clearTimeout(slowTimer);
        busy = false;
        els.send.disabled = !els.input.value.trim();
        refreshState();
      });
  }

  function showFailure(bubble, question) {
    bubble.remove();
    addNotice(t('offline'), 'aw-msg--error');
    addNotice(t('restoreHint'));
    restoreQuestion(question);
    announce(t('offline'));
    powSolved = { challenge: '', nonce: '' };
  }

  function handleRefusal(bubble, question, data) {
    bubble.remove();
    var message = (data && data.message) || '';
    if (message) {
      addNotice(message, data && data.error === 'check_failed' ? 'aw-msg--error' : '');
      announce(message);
    } else {
      addNotice(t('offline'), 'aw-msg--error');
    }
    if (data && data.error === 'check_failed') {
      powSolved = { challenge: '', nonce: '' };
      refreshState();
      restoreQuestion(question);
    }
    if (data && (data.error === 'limit' || data.error === 'too_long' || data.error === 'off')) {
      hideSuggestions();
    }
  }

  function streamReply(payload, bubble, question) {
    if (typeof window.fetch !== 'function') {
      return jsonReply(payload, bubble, question);
    }
    var headers = jsonHeaders();
    headers['Accept'] = 'text/event-stream';
    return request(URLS.send, {
      method: 'POST',
      headers: headers,
      credentials: 'same-origin',
      body: JSON.stringify(payload)
    }).then(function (response) {
      var type = response.headers.get('Content-Type') || '';
      if (!response.ok || type.indexOf('text/event-stream') === -1) {
        return readJSON(response).then(function (result) {
          if (result.status === 200 && result.data.ok && result.data.reply) {
            finishReply(bubble, result.data.reply);
            return;
          }
          handleRefusal(bubble, question, result.data);
        });
      }
      if (!response.body || typeof response.body.getReader !== 'function') {
        return readJSON(response).then(function (result) {
          if (result.data && result.data.reply) {
            finishReply(bubble, result.data.reply);
          } else {
            showFailure(bubble, question);
          }
        });
      }

      var reader = response.body.getReader();
      var decoder = new TextDecoder('utf-8');
      var buffer = '';
      var reply = null;
      var streamBuffer = null;

      function handle(block) {
        var parsed = parseEventBlock(block);
        if (!parsed) { return; }
        var data = parsed.data;
        if (parsed.event === 'start' && data.message_id) {
          streamBuffer = { id: data.message_id, body: '' };
          bubble.setAttribute('data-message-id', String(data.message_id));
        } else if (parsed.event === 'delta' && data.text) {
          bubble.textContent = (bubble.textContent || '') + data.text;
          scrollToEnd();
        } else if (parsed.event === 'done' && data.reply) {
          reply = data.reply;
        }
      }

      function pump() {
        return reader.read().then(function (chunk) {
          if (chunk.done) {
            buffer += decoder.decode();
            if (buffer.trim()) { handle(buffer); }
            if (reply) {
              finishReply(bubble, reply);
              announce('');
            } else {
              showFailure(bubble, question);
            }
            return;
          }
          buffer += decoder.decode(chunk.value, { stream: true });
          var parts = buffer.split('\n\n');
          buffer = parts.pop();
          parts.forEach(handle);
          return pump();
        });
      }

      return pump().catch(function () {
        if (reply) {
          finishReply(bubble, reply);
        } else {
          showFailure(bubble, question);
        }
      });
    });
  }

  function jsonReply(payload, bubble, question) {
    payload.stream = false;
    return request(URLS.send, {
      method: 'POST',
      headers: jsonHeaders(),
      credentials: 'same-origin',
      body: JSON.stringify(payload)
    })
      .then(readJSON)
      .then(function (result) {
        if (result.status === 200 && result.data.ok && result.data.reply) {
          finishReply(bubble, result.data.reply);
          return;
        }
        handleRefusal(bubble, question, result.data);
      });
  }

  // ── Panel ─────────────────────────────────────────────────────────────

  function openPanel() {
    if (!state) { return; }
    els.panel.hidden = false;
    els.launcher.setAttribute('aria-expanded', 'true');
    if (!els.log.childElementCount) { renderConversation(); }
    renderConsent();
    els.input.focus();
  }

  function closePanel() {
    els.panel.hidden = true;
    els.launcher.setAttribute('aria-expanded', 'false');
    els.launcher.focus();
  }

  function togglePanel() {
    if (els.panel.hidden) {
      openPanel();
    } else {
      closePanel();
    }
  }

  // ── New conversation ──────────────────────────────────────────────────

  function startAgain() {
    request(URLS.newConversation, {
      method: 'POST',
      headers: jsonHeaders(),
      credentials: 'same-origin',
      body: '{}'
    })
      .then(readJSON)
      .then(function (result) {
        if (result.status === 200 && result.data.enabled) {
          state = result.data;
          powSolved = { challenge: '', nonce: '' };
          els.log.textContent = '';
          openingShown = false;
          renderConversation();
          renderConsent();
          announce(t('newStarted'));
          els.input.focus();
        }
      })
      .catch(function () { /* the old conversation stays on screen */ });
  }

  // ── Callback ──────────────────────────────────────────────────────────

  function openCallback() {
    if (!els.callback) { return; }
    els.callback.hidden = false;
    if (els.callbackPurpose) {
      els.callbackPurpose.textContent = state && state.callback ? state.callback.purpose || '' : '';
      els.callbackPurpose.setAttribute('lang', 'en');
    }
    if (els.callbackMsg) { els.callbackMsg.textContent = ''; }
    var name = document.getElementById('aw-callback-name');
    if (name) { name.focus(); }
  }

  function closeCallback() {
    if (!els.callback) { return; }
    els.callback.hidden = true;
    els.person.focus();
  }

  function submitCallback(event) {
    if (event) { event.preventDefault(); }
    var name = document.getElementById('aw-callback-name');
    var phone = document.getElementById('aw-callback-phone');
    var email = document.getElementById('aw-callback-email');
    var website = document.getElementById('aw-callback-website');
    var payload = {
      name: name ? name.value : '',
      phone: phone ? phone.value : '',
      email: email ? email.value : '',
      website: website ? website.value : ''
    };
    if (!payload.name.trim()) {
      els.callbackMsg.textContent = t('nameLabel');
      if (name) { name.focus(); }
      return;
    }
    if (!payload.phone.trim() && !payload.email.trim()) {
      els.callbackMsg.textContent = t('contactMissing');
      if (phone) { phone.focus(); }
      return;
    }
    els.callbackSend.disabled = true;
    els.callbackMsg.textContent = t('sending');
    request(URLS.callback, {
      method: 'POST',
      headers: jsonHeaders(),
      credentials: 'same-origin',
      body: JSON.stringify(payload)
    })
      .then(readJSON)
      .then(function (result) {
        els.callbackSend.disabled = false;
        if (result.status === 200 && result.data.ok) {
          els.callbackForm.reset();
          els.callbackMsg.textContent = t('callbackSent');
          announce(t('callbackSent'));
          els.callbackMsg.focus();
        } else {
          els.callbackMsg.textContent = (result.data && result.data.message) || t('callbackFailed');
        }
      })
      .catch(function () {
        els.callbackSend.disabled = false;
        els.callbackMsg.textContent = t('callbackFailed');
      });
  }

  // ── Wiring ────────────────────────────────────────────────────────────

  els.launcher.addEventListener('click', togglePanel);
  els.close.addEventListener('click', closePanel);
  els.lang.addEventListener('click', function () {
    setLanguage(lang === 'sw' ? 'en' : 'sw');
  });
  els.person.addEventListener('click', openCallback);
  els.restart.addEventListener('click', startAgain);
  els.callbackForm.addEventListener('submit', submitCallback);
  els.callbackCancel.addEventListener('click', closeCallback);

  els.input.addEventListener('keydown', function (event) {
    if (event.key === 'Enter' && !event.shiftKey) {
      event.preventDefault();
      els.composer.requestSubmit ? els.composer.requestSubmit() : sendQuestion();
    }
  });

  els.input.addEventListener('input', function () {
    els.send.disabled = busy || !els.input.value.trim();
  });

  els.composer.addEventListener('submit', function (event) {
    event.preventDefault();
    sendQuestion();
  });

  document.addEventListener('keydown', function (event) {
    if (event.key !== 'Escape') { return; }
    if (els.callback && !els.callback.hidden) {
      closeCallback();
    } else if (els.panel && !els.panel.hidden) {
      closePanel();
    }
  });

  applyLanguage();
  els.send.disabled = true;

  loadState().then(function (payload) {
    if (!payload) { return; }
    revealLauncher();
    renderConversation();
  });
})();
