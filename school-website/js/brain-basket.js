/* ══════════════════════════════════════════════════════════════════════════
   Brain Basket — a tiny maths catching game for the homepage.
   Vanilla JS + canvas. Everything is drawn and synthesised in code.
   No network requests, no libraries, no cookies.

   TO CUSTOMISE: edit CONFIG and STRINGS below. To add a question type,
   add a function to GENERATORS.low / .mid / .high. All player-facing
   text lives in STRINGS (this is where Kiswahili translations go).
   ══════════════════════════════════════════════════════════════════════════ */
(function () {
  'use strict';

  /* ── Editable config ─────────────────────────────────────────────────── */
  var CONFIG = {
    name: 'Brain Basket',
    tagline: 'Catch the right answer!',
    admissionsUrl: 'admissions.html',           // game-over "enquire" link
    admissionsText: 'Want your child to learn like this? Enquire about admissions',
    showAdmissionsLink: true,
    breakAfterGames: 3,                          // "take a break" screen every N games
    maxPixelRatio: 2,
    // fall speed, in canvas-heights per second
    speedStart: 0.16,
    speedStep: 0.012,                            // added every speedEvery correct answers
    speedEvery: 3,
    speedMax: 0.34,
    calmSpeed: 0.10,
    hearts: 3,
    correctPoints: 10,
    streakBonusEvery: 3,
    streakBonusPoints: 5,
    spawnGapMin: 0.55,                           // seconds between answer cards
    spawnGapMax: 0.95,
    storagePrefix: 'brainbasket.'
  };

  /* ── All player-facing text (Kiswahili translations go here) ────────── */
  var STRINGS = {
    play: 'Play',
    back: 'Back',
    soundOn: 'Sound on',
    soundOff: 'Sound off',
    calmMode: 'Take your time',
    calmHint: 'Calm mode: no hearts, gentle speed. Works well with keyboard play too.',
    levelsTitle: 'Pick your level',
    levelLow: 'Grades 1 to 3',
    levelMid: 'Grades 4 to 6',
    levelHigh: 'Grades 7 to 9',
    levelLowHint: 'Adding, taking away, counting',
    levelMidHint: 'Times tables, bigger sums, simple fractions',
    levelHighHint: 'Mixed sums, percentages, missing numbers',
    practice: 'Practice questions without the game',
    practiceHint: 'One question at a time with big answer buttons.',
    startHint: 'Slide your finger, move the mouse, or use the arrow keys to catch the right answer.',
    score: 'Score',
    best: 'Best',
    heartsLabel: 'Hearts left',
    correct: 'Correct!',
    wrong: 'Not quite — try the next one!',
    missed: 'So close! That one got away.',
    nice: 'Nice!',
    great: 'Great!',
    amazing: 'Amazing!',
    levelUp: 'Speeding up a little!',
    gameOverTitle: 'Great effort!',
    gameOverText: function (right) {
      return 'You got ' + right + ' right. Every try makes your brain stronger!';
    },
    playAgain: 'Play again',
    changeLevel: 'Change level',
    breakTitle: 'Nice playing! Time for a break?',
    breakText: 'Your brain has worked hard. Stretch, drink some water, and come back when you like.',
    takeABreak: 'Take a break',
    keepPlaying: 'Keep playing',
    practiceAgain: 'Next question',
    practiceCorrect: 'Correct!',
    practiceWrong: 'Have another try — you can do it!',
    practiceScore: 'Right so far',
    loading: 'Warming up the basket…'
  };

  /* ── Question generators ───────────────────────────────────────────────
     Each returns { prompt: '7 + 5 = ?', answer: <number> }.
     Add new question types here — the game code does not need changes. */
  var GENERATORS = {
    low: [
      function (r) { // addition, sums to 20
        var a = 1 + Math.floor(r() * 10), b = 1 + Math.floor(r() * (20 - a));
        return { prompt: a + ' + ' + b + ' = ?', answer: a + b };
      },
      function (r) { // addition to 50
        var a = 5 + Math.floor(r() * 20), b = 5 + Math.floor(r() * 20);
        return { prompt: a + ' + ' + b + ' = ?', answer: a + b };
      },
      function (r) { // subtraction, never negative
        var a = 5 + Math.floor(r() * 30), b = 1 + Math.floor(r() * a);
        return { prompt: a + ' − ' + b + ' = ?', answer: a - b };
      },
      function (r) { // missing number in addition, answer always positive
        var b = 1 + Math.floor(r() * 9), c = b + 1 + Math.floor(r() * 12);
        return { prompt: '? + ' + b + ' = ' + c, answer: c - b };
      }
    ],
    mid: [
      function (r) { // multiplication tables to 12
        var a = 2 + Math.floor(r() * 11), b = 2 + Math.floor(r() * 11);
        return { prompt: a + ' × ' + b + ' = ?', answer: a * b };
      },
      function (r) { // division with clean answers
        var b = 2 + Math.floor(r() * 11), ans = 2 + Math.floor(r() * 11);
        return { prompt: (b * ans) + ' ÷ ' + b + ' = ?', answer: ans };
      },
      function (r) { // larger addition/subtraction, never negative
        var small = 15 + Math.floor(r() * 70);
        var big = small + 5 + Math.floor(r() * 80);
        if (r() < 0.5) {
          var a = 20 + Math.floor(r() * 80), b = 15 + Math.floor(r() * 80);
          return { prompt: a + ' + ' + b + ' = ?', answer: a + b };
        }
        return { prompt: big + ' − ' + small + ' = ?', answer: big - small };
      },
      function (r) { // simple fraction of a number
        var fr = [[1, 2], [1, 4], [3, 4], [1, 5], [2, 5], [3, 5], [1, 10], [3, 10]][Math.floor(r() * 8)];
        var n = 1 + Math.floor(r() * 4);
        return { prompt: 'What is ' + fr[0] + '/' + fr[1] + ' of ' + (fr[1] * n) + '?', answer: fr[0] * n };
      }
    ],
    high: [
      function (r) { // two-step mixed operations
        var a = 3 + Math.floor(r() * 15), b = 2 + Math.floor(r() * 12), c = 2 + Math.floor(r() * 9);
        if (r() < 0.5) return { prompt: a + ' × ' + b + ' − ' + c + ' = ?', answer: a * b - c };
        return { prompt: '(' + a + ' + ' + b + ') × ' + c + ' = ?', answer: (a + b) * c };
      },
      function (r) { // percentage of a whole number
        var p = [10, 20, 25, 50][Math.floor(r() * 4)];
        var step = 100 / p;
        var base = (2 + Math.floor(r() * 10)) * step;
        return { prompt: 'What is ' + p + '% of ' + base + '?', answer: base * p / 100 };
      },
      function (r) { // simple equation x + a = b
        var a = 2 + Math.floor(r() * 30), x = 1 + Math.floor(r() * 30);
        return { prompt: 'x + ' + a + ' = ' + (x + a) + '. What is x?', answer: x };
      },
      function (r) { // squares and friendly negative sums
        if (r() < 0.5) {
          var n = 3 + Math.floor(r() * 12);
          return { prompt: n + '² = ?', answer: n * n };
        }
        var a2 = -9 + Math.floor(r() * 19), b2 = -9 + Math.floor(r() * 19);
        return { prompt: '(' + a2 + ') + (' + b2 + ') = ?', answer: a2 + b2 };
      }
    ]
  };

  var LEVELS = [
    { id: 'low', label: STRINGS.levelLow, hint: STRINGS.levelLowHint, gens: GENERATORS.low },
    { id: 'mid', label: STRINGS.levelMid, hint: STRINGS.levelMidHint, gens: GENERATORS.mid },
    { id: 'high', label: STRINGS.levelHigh, hint: STRINGS.levelHighHint, gens: GENERATORS.high }
  ];

  /* ── Helpers ────────────────────────────────────────────────────────── */
  function $(sel, root) { return (root || document).querySelector(sel); }
  function el(tag, cls, html) {
    var e = document.createElement(tag);
    if (cls) e.className = cls;
    if (html != null) e.innerHTML = html;
    return e;
  }
  function clamp(v, lo, hi) { return Math.max(lo, Math.min(hi, v)); }
  function pick(arr) { return arr[Math.floor(Math.random() * arr.length)]; }
  function store(key, val) {
    try { localStorage.setItem(CONFIG.storagePrefix + key, val); } catch (e) {}
  }
  function load(key, fallback) {
    try {
      var v = localStorage.getItem(CONFIG.storagePrefix + key);
      return v == null ? fallback : v;
    } catch (e) { return fallback; }
  }

  /* Build a question + 3 distinct options with exactly one correct answer. */
  function makeQuestion(level, lastPrompt) {
    var gens = level.gens, q, guard = 0;
    do {
      q = pick(gens)(Math.random);
      guard++;
    } while (q.prompt === lastPrompt && guard < 20);

    var opts = [q.answer], tries = 0;
    while (opts.length < 3 && tries < 60) {
      tries++;
      var spread = Math.max(2, Math.round(Math.abs(q.answer) * 0.3));
      var delta = 1 + Math.floor(Math.random() * spread);
      var cand = Math.random() < 0.5 ? q.answer - delta : q.answer + delta;
      if (opts.indexOf(cand) === -1) opts.push(cand);
    }
    var n = 1;
    while (opts.length < 3) {
      if (opts.indexOf(q.answer + n) === -1) opts.push(q.answer + n);
      n++;
    }
    for (var i = opts.length - 1; i > 0; i--) {
      var j = Math.floor(Math.random() * (i + 1));
      var t = opts[i]; opts[i] = opts[j]; opts[j] = t;
    }
    return { prompt: q.prompt, answer: q.answer, options: opts };
  }

  /* ── Sound: tiny WebAudio blips, off by default ─────────────────────── */
  var sound = {
    ctx: null,
    enabled: load('sound', 'off') === 'on',
    ensure: function () {
      if (!this.ctx) {
        var AC = window.AudioContext || window.webkitAudioContext;
        if (AC) { try { this.ctx = new AC(); } catch (e) {} }
      }
      if (this.ctx && this.ctx.state === 'suspended') this.ctx.resume();
    },
    blip: function (freqs, dur, type, vol) {
      if (!this.enabled || !this.ctx) return;
      var self = this, t = this.ctx.currentTime;
      freqs.forEach(function (f, i) {
        var o = self.ctx.createOscillator(), g = self.ctx.createGain();
        var start = t + i * (dur / freqs.length);
        o.type = type || 'sine'; o.frequency.value = f;
        g.gain.setValueAtTime(0.0001, start);
        g.gain.exponentialRampToValueAtTime(vol || 0.08, start + 0.02);
        g.gain.exponentialRampToValueAtTime(0.0001, start + dur / freqs.length);
        o.connect(g); g.connect(self.ctx.destination);
        o.start(start); o.stop(start + dur / freqs.length + 0.05);
      });
    },
    catch: function () { this.blip([660, 880], 0.18, 'sine', 0.07); },
    miss: function () { this.blip([220, 180], 0.25, 'triangle', 0.05); },
    level: function () { this.blip([523, 659, 784], 0.3, 'sine', 0.06); }
  };

  /* ══════════════════════════════════════════════════════════════════════
     GAME
     ══════════════════════════════════════════════════════════════════════ */
  function Game(root) {
    this.root = root;
    this.state = 'menu'; // menu | playing | practice | over
    var savedLevel = load('level', 'low');
    this.level = LEVELS.filter(function (l) { return l.id === savedLevel; })[0] || LEVELS[0];
    this.calm = load('calm', 'off') === 'on' ||
      !!(window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches);
    this.soundOn = sound.enabled;
    this.best = parseInt(load('best', '0'), 10) || 0;
    this.gamesPlayed = parseInt(load('games', '0'), 10) || 0;
    this.lastPrompt = '';
    this._build();
  }

  Game.prototype._build = function () {
    var self = this;
    var card = el('div', 'bb');
    card.innerHTML =
      '<div class="bb__hud">' +
        '<span class="bb__score"></span>' +
        '<span class="bb__hearts"></span>' +
        '<span class="bb__best"></span>' +
        '<button type="button" class="bb__sound"></button>' +
      '</div>' +
      '<div class="bb__question" role="status" aria-live="polite"></div>' +
      '<div class="bb__stage">' +
        '<canvas class="bb__canvas" aria-hidden="true"></canvas>' +
        '<div class="bb__overlay"></div>' +
      '</div>' +
      '<div class="bb__hint"></div>' +
      '<div class="bb__announce" role="status" aria-live="polite" aria-atomic="true"></div>';
    this.root.appendChild(card);
    this.card = card;
    this.hudScore = $('.bb__score', card);
    this.hudHearts = $('.bb__hearts', card);
    this.hudBest = $('.bb__best', card);
    this.qEl = $('.bb__question', card);
    this.stage = $('.bb__stage', card);
    this.canvas = $('.bb__canvas', card);
    this.overlay = $('.bb__overlay', card);
    this.hint = $('.bb__hint', card);
    this.announce = $('.bb__announce', card);
    this.ctx = this.canvas.getContext('2d');
    this.soundBtn = $('.bb__sound', card);

    this.soundBtn.addEventListener('click', function () {
      self.soundOn = !self.soundOn;
      sound.enabled = self.soundOn;
      store('sound', self.soundOn ? 'on' : 'off');
      self._syncSoundBtn();
      if (self.soundOn) sound.ensure();
    });
    this._syncSoundBtn();

    this._bindInput();
    this._setupCanvas();
    this.showMenu();
  };

  Game.prototype._syncSoundBtn = function () {
    this.soundBtn.textContent = '♪ ' + (this.soundOn ? STRINGS.soundOn : STRINGS.soundOff);
    this.soundBtn.setAttribute('aria-pressed', this.soundOn ? 'true' : 'false');
    this.soundBtn.setAttribute('aria-label', this.soundOn ? STRINGS.soundOn : STRINGS.soundOff);
  };

  /* ── Canvas: crisp on retina (capped), responsive, state kept on resize ─ */
  Game.prototype._setupCanvas = function () {
    var self = this;
    this.dpr = Math.min(window.devicePixelRatio || 1, CONFIG.maxPixelRatio);
    var fit = function () {
      var w = self.stage.clientWidth, h = self.stage.clientHeight;
      if (!w || !h) return;
      self.canvas.width = Math.round(w * self.dpr);
      self.canvas.height = Math.round(h * self.dpr);
      self.W = w; self.H = h;
      self.bagW = clamp(w * 0.15, 46, 88);
      self.bagY = h - self.bagW * 0.95;
      if (self.bagX == null) self.bagX = w / 2;
      self.bagX = clamp(self.bagX, self.bagW / 2 + 4, w - self.bagW / 2 - 4);
      (self.cards || []).forEach(function (c) {
        c.x = clamp(c.x, c.w / 2, w - c.w / 2);
        c.w = clamp(w * 0.16, 56, 96);
      });
    };
    fit();
    if (window.ResizeObserver) new ResizeObserver(fit).observe(this.stage);
    else window.addEventListener('resize', fit);

    this.visible = true;
    if ('IntersectionObserver' in window) {
      new IntersectionObserver(function (entries) {
        self.visible = entries[0].isIntersecting;
        self._last = null;
      }).observe(this.stage);
    }
    var onVis = function () { self._last = null; };
    document.addEventListener('visibilitychange', onVis);
  };

  /* ── Input: drag / mouse move / arrow keys / tap halves ─────────────── */
  Game.prototype._bindInput = function () {
    var self = this;
    var targetX = null, dragging = false, lastTap = 0;

    function setFromClientX(clientX) {
      var rect = self.stage.getBoundingClientRect();
      targetX = clamp(clientX - rect.left, self.bagW / 2 + 4, self.W - self.bagW / 2 - 4);
    }
    this.stage.addEventListener('pointerdown', function (e) {
      if (self.state !== 'playing') return;
      e.preventDefault();
      try { self.stage.setPointerCapture(e.pointerId); } catch (err) {}
      dragging = true;
      setFromClientX(e.clientX);
    });
    this.stage.addEventListener('pointermove', function (e) {
      if (self.state !== 'playing') return;
      if (dragging || e.pointerType === 'mouse' || e.pointerType === 'pen') setFromClientX(e.clientX);
    });
    this.stage.addEventListener('pointerup', function (e) {
      dragging = false;
      // double-tap left/right half = nudge that way (fallback for kids)
      if (self.state === 'playing' && e.pointerType !== 'mouse') {
        var now = Date.now();
        if (now - lastTap < 400) {
          var rect = self.stage.getBoundingClientRect();
          var dir = (e.clientX - rect.left) < rect.width / 2 ? -1 : 1;
          targetX = clamp(self.bagX + dir * self.W * 0.18,
            self.bagW / 2 + 4, self.W - self.bagW / 2 - 4);
        }
        lastTap = now;
      }
    });
    this.stage.addEventListener('pointercancel', function () { dragging = false; });

    window.addEventListener('keydown', function (e) {
      if (self.state !== 'playing') return;
      var step = self.W * 0.08;
      if (e.key === 'ArrowLeft' || e.key === 'a' || e.key === 'A') {
        targetX = clamp(self.bagX - step, self.bagW / 2 + 4, self.W - self.bagW / 2 - 4);
        e.preventDefault();
      } else if (e.key === 'ArrowRight' || e.key === 'd' || e.key === 'D') {
        targetX = clamp(self.bagX + step, self.bagW / 2 + 4, self.W - self.bagW / 2 - 4);
        e.preventDefault();
      }
    });

    this._getTarget = function () { return targetX; };
    this._setTarget = function (v) { targetX = v; };
  };

  /* ── Screens ────────────────────────────────────────────────────────── */
  Game.prototype._clearOverlay = function () {
    this.overlay.innerHTML = '';
    this.overlay.classList.remove('is-visible');
  };

  Game.prototype.showMenu = function () {
    this.state = 'menu';
    this._stopLoop();
    this._clearOverlay();
    this.qEl.textContent = CONFIG.name + ' — ' + CONFIG.tagline;
    this.hudScore.textContent = '';
    this.hudHearts.textContent = '';
    this.hudBest.textContent = '';
    var self = this;
    var box = el('div', 'bb__menu');
    box.innerHTML =
      '<p class="bb__menu-title">' + CONFIG.name + '</p>' +
      '<p class="bb__menu-tag">' + CONFIG.tagline + '</p>' +
      '<p class="bb__menu-sub">' + STRINGS.levelsTitle + '</p>';
    LEVELS.forEach(function (lv) {
      var b = el('button', 'bb__level' + (lv.id === self.level.id ? ' is-selected' : ''),
        '<strong>' + lv.label + '</strong><span>' + lv.hint + '</span>');
      b.type = 'button';
      b.addEventListener('click', function () {
        self.level = lv;
        store('level', lv.id);
        box.querySelectorAll('.bb__level').forEach(function (x) { x.classList.remove('is-selected'); });
        b.classList.add('is-selected');
      });
      box.appendChild(b);
    });
    var calmLabel = el('label', 'bb__calm');
    var calmBox = document.createElement('input');
    calmBox.type = 'checkbox';
    calmBox.checked = this.calm;
    calmBox.addEventListener('change', function () {
      self.calm = calmBox.checked;
      store('calm', self.calm ? 'on' : 'off');
    });
    calmLabel.appendChild(calmBox);
    calmLabel.appendChild(document.createTextNode(' ' + STRINGS.calmMode));
    box.appendChild(calmLabel);
    box.appendChild(el('p', 'bb__menu-note', STRINGS.calmHint));

    var row = el('div', 'bb__menu-row');
    var playBtn = el('button', 'bb__btn bb__btn--big', STRINGS.play);
    playBtn.type = 'button';
    playBtn.addEventListener('click', function () { self._startGame(); });
    row.appendChild(playBtn);
    var pracBtn = el('button', 'bb__btn', STRINGS.practice);
    pracBtn.type = 'button';
    pracBtn.addEventListener('click', function () { self._startPractice(); });
    row.appendChild(pracBtn);
    box.appendChild(row);
    box.appendChild(el('p', 'bb__menu-note', STRINGS.practiceHint));
    this.hint.textContent = STRINGS.startHint;
    this.overlay.appendChild(box);
    this.overlay.classList.add('is-visible');
  };

  Game.prototype._startGame = function () {
    sound.ensure();
    this.state = 'playing';
    this.score = 0; this.streak = 0; this.right = 0; this.hearts = CONFIG.hearts;
    this.speedLevel = 0; this.lastPrompt = '';
    this.cards = []; this.pops = [];
    this._pendingNext = false;
    this._setTarget(null);
    this.hint.textContent = STRINGS.startHint;
    this._clearOverlay();
    this._syncHud();
    this._newQuestion();
    this._startLoop();
  };

  Game.prototype._startPractice = function () {
    sound.ensure();
    this.state = 'practice';
    this.practiceRight = 0;
    this.lastPrompt = '';
    this._clearOverlay();
    this.qEl.textContent = '';
    this._practiceNext();
  };

  Game.prototype._practiceNext = function () {
    var self = this;
    var q = makeQuestion(this.level, this.lastPrompt);
    this.lastPrompt = q.prompt;
    this.qEl.textContent = q.prompt;
    this.announce.textContent = q.prompt;
    this.overlay.innerHTML = '';
    var box = el('div', 'bb__practice');
    q.options.forEach(function (opt) {
      var b = el('button', 'bb__btn bb__btn--opt', String(opt));
      b.type = 'button';
      b.addEventListener('click', function () {
        if (b.disabled) return;
        if (opt === q.answer) {
          b.classList.add('is-right');
          self.practiceRight++;
          self.announce.textContent = STRINGS.practiceCorrect + ' ' + STRINGS.practiceScore + ': ' + self.practiceRight;
          sound.catch();
          setTimeout(function () { self._practiceAdvance(box); }, 450);
        } else {
          b.classList.add('is-wrong');
          b.disabled = true;
          self.announce.textContent = STRINGS.practiceWrong;
          sound.miss();
        }
      });
      box.appendChild(b);
    });
    var quit = el('button', 'bb__btn bb__btn--ghost', STRINGS.back);
    quit.type = 'button';
    quit.addEventListener('click', function () { self.showMenu(); });
    box.appendChild(quit);
    this.overlay.appendChild(box);
    this.overlay.classList.add('is-visible');
    this._stopLoop();
  };

  Game.prototype._practiceAdvance = function (box) {
    box.querySelectorAll('button').forEach(function (x) { x.disabled = true; });
    var self = this;
    var next = el('button', 'bb__btn', STRINGS.practiceAgain);
    next.type = 'button';
    next.addEventListener('click', function () { self._practiceNext(); });
    box.appendChild(next);
  };

  /* ── Question / card lifecycle ──────────────────────────────────────── */
  Game.prototype._newQuestion = function () {
    var q = makeQuestion(this.level, this.lastPrompt);
    this.lastPrompt = q.prompt;
    this.question = q;
    this.qEl.textContent = q.prompt;
    this.announce.textContent = q.prompt;
    this.cards = (this.cards || []).filter(function (c) { return !c.resolved; });
    this._spawnQueue = q.options.slice();
    this._spawnTimer = 0;
  };

  Game.prototype._syncHud = function () {
    this.hudScore.textContent = STRINGS.score + ' ' + this.score;
    this.hudBest.textContent = STRINGS.best + ' ' + this.best;
    var hearts = '';
    for (var i = 0; i < CONFIG.hearts; i++) hearts += i < this.hearts ? '♥' : '♡';
    this.hudHearts.textContent = hearts;
    this.hudHearts.setAttribute('aria-label', STRINGS.heartsLabel + ': ' + this.hearts);
  };

  Game.prototype._fallSpeed = function () {
    if (this.calm) return CONFIG.calmSpeed;
    return Math.min(CONFIG.speedStart + this.speedLevel * CONFIG.speedStep, CONFIG.speedMax);
  };

  /* ── Main loop: rAF, delta-time clamped, pauses when hidden/offscreen ─ */
  Game.prototype._startLoop = function () {
    if (this._raf) return;
    var self = this;
    var step = function (t) {
      self._raf = requestAnimationFrame(step);
      if (self.state !== 'playing' || self.visible === false) { self._last = null; return; }
      var now = t / 1000;
      var dt = self._last == null ? 0.016 : Math.min(now - self._last, 0.05);
      self._last = now;
      self._update(dt);
      self._draw();
    };
    this._last = null;
    this._raf = requestAnimationFrame(step);
  };

  Game.prototype._stopLoop = function () {
    if (this._raf) cancelAnimationFrame(this._raf);
    this._raf = null;
    this._last = null;
  };

  Game.prototype._update = function (dt) {
    var W = this.W, H = this.H;
    if (!W) return;

    var tx = this._getTarget();
    if (tx != null) {
      this.bagX += (tx - this.bagX) * Math.min(1, dt * 12);
      if (Math.abs(tx - this.bagX) < 0.5) this._setTarget(null);
    }

    if (this._spawnQueue.length) {
      this._spawnTimer -= dt;
      if (this._spawnTimer <= 0) {
        this._spawnCard(this._spawnQueue.shift());
        this._spawnTimer = CONFIG.spawnGapMin + Math.random() * (CONFIG.spawnGapMax - CONFIG.spawnGapMin);
      }
    }

    var speed = this._fallSpeed() * H;
    var bagTop = this.bagY - this.bagW * 0.55;
    var anyResolved = false;

    for (var i = this.cards.length - 1; i >= 0; i--) {
      var c = this.cards[i];
      c.y += speed * c.vary * dt;
      if (!c.resolved && c.y >= bagTop - c.h / 2 && c.y <= this.bagY + this.bagW * 0.4) {
        if (Math.abs(c.x - this.bagX) < (this.bagW + c.w) * 0.32) {
          c.resolved = true; anyResolved = true;
          if (c.value === this.question.answer) this._onCorrect(c);
          else this._onWrong(c, 'wrong');
        }
      }
      if (!c.resolved && c.y > H + c.h) {
        c.resolved = true; anyResolved = true;
        if (c.value === this.question.answer) this._onWrong(c, 'missed');
      }
      if (c.resolved) c.done = true;
    }
    this.cards = this.cards.filter(function (c) { return !c.done; });

    if (anyResolved && !this._pendingNext) {
      var stillFalling = this.cards.some(function (c) { return !c.resolved; }) ||
        this._spawnQueue.length > 0;
      if (!stillFalling) {
        this._pendingNext = true;
        var self = this;
        setTimeout(function () {
          self._pendingNext = false;
          if (self.state === 'playing') self._newQuestion();
        }, 600);
      }
    }

    for (var p = this.pops.length - 1; p >= 0; p--) {
      var pop = this.pops[p];
      pop.life -= dt * 1.4;
      pop.y -= 34 * dt;
      if (pop.life <= 0) this.pops.splice(p, 1);
    }
  };

  Game.prototype._spawnCard = function (value) {
    var laneW = this.W / 3;
    var lane = Math.floor(Math.random() * 3);
    var used = {};
    var self = this;
    this.cards.forEach(function (c) {
      if (c.y < self.H * 0.45) used[Math.floor(c.x / laneW)] = true;
    });
    var tries = 0;
    while (used[lane] && tries < 5) { lane = Math.floor(Math.random() * 3); tries++; }
    var w = clamp(this.W * 0.16, 56, 96);
    this.cards.push({
      value: value,
      x: clamp(laneW * lane + laneW / 2 + (Math.random() - 0.5) * laneW * 0.3,
        w / 2, this.W - w / 2),
      y: -w,
      w: w, h: w * 0.82,
      vary: 0.85 + Math.random() * 0.3,
      resolved: false, done: false
    });
  };

  Game.prototype._onCorrect = function (c) {
    this.right++;
    this.streak++;
    var bonus = (this.streak % CONFIG.streakBonusEvery === 0) ? CONFIG.streakBonusPoints : 0;
    var pts = CONFIG.correctPoints + bonus;
    this.score += pts;
    if (this.score > this.best) { this.best = this.score; store('best', String(this.best)); }
    this.speedLevel++;
    if (!this.calm && this.speedLevel % CONFIG.speedEvery === 0 &&
        this._fallSpeed() < CONFIG.speedMax) {
      sound.level();
      this._pop(c.x, c.y - c.h, STRINGS.levelUp);
      this.announce.textContent = STRINGS.levelUp;
    } else {
      sound.catch();
      var praise = this.streak % 5 === 0 ? STRINGS.amazing :
        (this.streak % 3 === 0 ? STRINGS.great : pick([STRINGS.nice, STRINGS.correct]));
      this._pop(c.x, c.y - c.h, praise);
      this.announce.textContent = STRINGS.correct + ' ' + STRINGS.score + ' ' + this.score +
        (bonus ? ' (+' + bonus + ')' : '');
    }
    this._syncHud();
    this._pop(c.x, c.y, '+' + pts, true);
  };

  Game.prototype._onWrong = function (c, kind) {
    this.streak = 0;
    sound.miss();
    if (!this.calm) {
      this.hearts--;
      this._syncHud();
    }
    var msg = kind === 'missed' ? STRINGS.missed : STRINGS.wrong;
    this._pop(c.x, Math.min(c.y, this.H * 0.8), msg);
    this.announce.textContent = msg + (this.calm ? '' :
      ' ' + STRINGS.heartsLabel + ' ' + this.hearts);
    if (!this.calm && this.hearts <= 0) {
      var self = this;
      setTimeout(function () { if (self.state === 'playing') self._gameOver(); }, 500);
    }
  };

  Game.prototype._pop = function (x, y, text, small) {
    this.pops.push({ x: x, y: y, text: text, life: 1, small: !!small });
  };

  Game.prototype._gameOver = function () {
    this.state = 'over';
    this._stopLoop();
    this.gamesPlayed++;
    store('games', String(this.gamesPlayed));
    var showBreak = CONFIG.breakAfterGames > 0 &&
      this.gamesPlayed % CONFIG.breakAfterGames === 0 &&
      load('breakShown', 'no') !== 'yes';
    if (showBreak) store('breakShown', 'yes');
    var self = this;
    var box = el('div', 'bb__over');
    box.innerHTML =
      '<p class="bb__over-title">' + (showBreak ? STRINGS.breakTitle : STRINGS.gameOverTitle) + '</p>' +
      '<p class="bb__over-text">' + (showBreak ? STRINGS.breakText : STRINGS.gameOverText(this.right)) + '</p>' +
      '<p class="bb__over-score">' + STRINGS.score + ' ' + this.score + ' · ' + STRINGS.best + ' ' + this.best + '</p>';
    var row = el('div', 'bb__menu-row');
    var again = el('button', 'bb__btn bb__btn--big',
      showBreak ? STRINGS.takeABreak : STRINGS.playAgain);
    again.type = 'button';
    again.addEventListener('click', function () {
      if (showBreak) self.showMenu(); else self._startGame();
    });
    row.appendChild(again);
    if (showBreak) {
      var keep = el('button', 'bb__btn', STRINGS.keepPlaying);
      keep.type = 'button';
      keep.addEventListener('click', function () { self._startGame(); });
      row.appendChild(keep);
    }
    var change = el('button', 'bb__btn bb__btn--ghost', STRINGS.changeLevel);
    change.type = 'button';
    change.addEventListener('click', function () { self.showMenu(); });
    row.appendChild(change);
    box.appendChild(row);
    if (CONFIG.showAdmissionsLink) {
      var a = el('a', 'bb__admissions', CONFIG.admissionsText);
      a.href = CONFIG.admissionsUrl;
      box.appendChild(a);
    }
    this.announce.textContent = STRINGS.gameOverTitle + ' ' + STRINGS.score + ' ' + this.score;
    this.overlay.appendChild(box);
    this.overlay.classList.add('is-visible');
    this.qEl.textContent = '';
    this.hint.textContent = '';
  };

  /* ── Drawing ────────────────────────────────────────────────────────── */
  var PAPER = ['#fff8e7', '#e8f4ff', '#ffeef5', '#eefbe8'];
  var INK = '#33415c';

  Game.prototype._draw = function () {
    var ctx = this.ctx, W = this.W, H = this.H;
    if (!W || !H) return;
    ctx.setTransform(this.dpr, 0, 0, this.dpr, 0, 0);
    ctx.clearRect(0, 0, W, H);

    // chalkboard-green play area
    var g = ctx.createLinearGradient(0, 0, 0, H);
    g.addColorStop(0, '#2f5d50'); g.addColorStop(1, '#274e43');
    ctx.fillStyle = g;
    ctx.fillRect(0, 0, W, H);
    // faint notebook lines
    ctx.strokeStyle = 'rgba(255,255,255,0.05)';
    ctx.lineWidth = 1;
    for (var y = 24; y < H; y += 28) {
      ctx.beginPath(); ctx.moveTo(0, y); ctx.lineTo(W, y); ctx.stroke();
    }
    ctx.strokeStyle = 'rgba(255,255,255,0.25)';
    ctx.lineWidth = 2;
    ctx.beginPath(); ctx.moveTo(0, H - 6); ctx.lineTo(W, H - 6); ctx.stroke();

    for (var i = 0; i < this.cards.length; i++) {
      var c = this.cards[i];
      this._drawCard(ctx, c.x, c.y, c.w, c.h, String(c.value), PAPER[i % PAPER.length]);
    }

    for (var p = 0; p < this.pops.length; p++) {
      var pop = this.pops[p];
      ctx.globalAlpha = Math.max(0, pop.life);
      ctx.fillStyle = pop.small ? '#ffd166' : '#ffffff';
      ctx.font = '700 ' + (pop.small ? 16 : 20) + 'px system-ui, sans-serif';
      ctx.textAlign = 'center';
      ctx.textBaseline = 'middle';
      ctx.fillText(pop.text, pop.x, pop.y);
      ctx.globalAlpha = 1;
    }

    if (this.state === 'playing' || this.state === 'over') {
      this._drawBag(ctx, this.bagX || this.W / 2, this.bagY || this.H - 40, this.bagW || 60);
    }
  };

  Game.prototype._drawCard = function (ctx, x, y, w, h, text, fill) {
    var r = h * 0.28;
    ctx.save();
    ctx.translate(x, y);
    ctx.rotate(Math.sin(y * 0.02) * 0.04);
    ctx.fillStyle = 'rgba(0,0,0,0.18)';
    this._rr(ctx, -w / 2 + 2, -h / 2 + 3, w, h, r); ctx.fill();
    ctx.fillStyle = fill;
    this._rr(ctx, -w / 2, -h / 2, w, h, r); ctx.fill();
    ctx.strokeStyle = 'rgba(51,65,92,0.25)'; ctx.lineWidth = 1.5;
    this._rr(ctx, -w / 2, -h / 2, w, h, r); ctx.stroke();
    ctx.fillStyle = 'rgba(245,158,11,0.5)';
    ctx.fillRect(-w * 0.18, -h / 2 - 2, w * 0.36, 6); // tape strip
    ctx.fillStyle = INK;
    ctx.font = '700 ' + Math.round(h * 0.44) + 'px system-ui, sans-serif';
    ctx.textAlign = 'center'; ctx.textBaseline = 'middle';
    ctx.fillText(text, 0, h * 0.04);
    ctx.restore();
  };

  Game.prototype._rr = function (ctx, x, y, w, h, r) {
    r = Math.min(r, w / 2, h / 2);
    ctx.beginPath();
    ctx.moveTo(x + r, y);
    ctx.arcTo(x + w, y, x + w, y + h, r);
    ctx.arcTo(x + w, y + h, x, y + h, r);
    ctx.arcTo(x, y + h, x, y, r);
    ctx.arcTo(x, y, x + w, y, r);
    ctx.closePath();
  };

  /* School bag drawn in code: handle, body, flap, pocket, wheels. */
  Game.prototype._drawBag = function (ctx, x, y, w) {
    var h = w * 0.92;
    ctx.save();
    ctx.translate(x, y);
    ctx.fillStyle = 'rgba(0,0,0,0.25)';
    ctx.beginPath(); ctx.ellipse(0, h * 0.52, w * 0.55, w * 0.12, 0, 0, Math.PI * 2); ctx.fill();
    ctx.strokeStyle = '#8a5a2b'; ctx.lineWidth = w * 0.09; ctx.lineCap = 'round';
    ctx.beginPath(); ctx.arc(0, -h * 0.28, w * 0.3, Math.PI, 0); ctx.stroke();
    ctx.fillStyle = '#c97b2d';
    this._rr(ctx, -w * 0.48, -h * 0.3, w * 0.96, h * 0.8, w * 0.14); ctx.fill();
    ctx.strokeStyle = '#8a5a2b'; ctx.lineWidth = 2; ctx.stroke();
    ctx.fillStyle = '#a8621f';
    this._rr(ctx, -w * 0.48, -h * 0.3, w * 0.96, h * 0.34, w * 0.12); ctx.fill();
    ctx.fillStyle = '#e8a04c';
    this._rr(ctx, -w * 0.3, h * 0.02, w * 0.6, h * 0.3, w * 0.1); ctx.fill();
    ctx.strokeStyle = '#8a5a2b'; ctx.lineWidth = 1.5; ctx.stroke();
    ctx.fillStyle = '#fbbf24';
    ctx.beginPath(); ctx.arc(0, -h * 0.12, w * 0.06, 0, Math.PI * 2); ctx.fill();
    ctx.fillStyle = '#4a3b2a';
    ctx.beginPath(); ctx.arc(-w * 0.3, h * 0.46, w * 0.07, 0, Math.PI * 2); ctx.fill();
    ctx.beginPath(); ctx.arc(w * 0.3, h * 0.46, w * 0.07, 0, Math.PI * 2); ctx.fill();
    ctx.restore();
  };

  /* ── Boot: only runs when the visitor taps Play ─────────────────────── */
  function boot(root) {
    try {
      // the template passes the real admissions URL via data attribute
      if (root.dataset && root.dataset.admissionsUrl) {
        CONFIG.admissionsUrl = root.dataset.admissionsUrl;
      }
      // remove the static preview, mount the game
      root.innerHTML = '';
      return new Game(root);
    } catch (err) {
      if (window.console && console.error) console.error('BrainBasket:', err);
      var p = document.createElement('p');
      p.className = 'bb__error';
      p.textContent = 'Brain Basket could not start on this device. The rest of the page works fine!';
      root.appendChild(p);
      return null;
    }
  }

  // exposed for tests / debugging
  window.BrainBasket = {
    CONFIG: CONFIG,
    STRINGS: STRINGS,
    LEVELS: LEVELS,
    makeQuestion: makeQuestion,
    boot: boot
  };
})();
