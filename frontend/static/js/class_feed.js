/**
 * Class feed — light vanilla JS.
 *
 * No framework and no build step. Everything degrades to a normal form POST
 * when JavaScript is off: the feed's first page is server-rendered, replies
 * and moderation are ordinary forms. "Show more", drafts and the polite poll
 * need JavaScript; without it the reader simply sees the first page.
 */
(function () {
  'use strict';

  var body = document.body;
  var USER_ID = body ? body.getAttribute('data-user-id') || '' : '';

  // -----------------------------------------------------------------------
  // Drafts — keyed to the logged-in user, never shared between accounts
  // -----------------------------------------------------------------------

  function postDraftKey(classId) {
    return 'feedDraft:u' + USER_ID + ':c' + classId;
  }

  function replyDraftKey(postId) {
    return 'feedReplyDraft:u' + USER_ID + ':p' + postId;
  }

  function readDraft(key) {
    try {
      return window.localStorage.getItem(key) || '';
    } catch (err) {
      return '';
    }
  }

  function writeDraft(key, value) {
    try {
      if (value) {
        window.localStorage.setItem(key, value);
      } else {
        window.localStorage.removeItem(key);
      }
    } catch (err) {
      /* storage full or blocked — typing still works */
    }
  }

  function clearDraft(key) {
    try {
      window.localStorage.removeItem(key);
    } catch (err) {
      /* ignore */
    }
  }

  function draftNote(form) {
    return form.querySelector('.feed-draft-note');
  }

  function wireDraft(form, textarea, key, clearNow) {
    if (!form || !textarea || !key) return;
    var note = draftNote(form);

    if (clearNow) {
      clearDraft(key);
      textarea.value = '';
      return;
    }

    var saved = readDraft(key);
    if (saved) {
      textarea.value = saved;
      if (note) note.hidden = false;
    }

    var timer = null;
    textarea.addEventListener('input', function () {
      if (timer) window.clearTimeout(timer);
      timer = window.setTimeout(function () {
        writeDraft(key, textarea.value.trim() ? textarea.value : '');
        if (note) note.hidden = !textarea.value;
      }, 300);
    });

    textarea.addEventListener('blur', function () {
      writeDraft(key, textarea.value.trim() ? textarea.value : '');
    });
  }

  // Composer (one per feed page)
  var composer = document.querySelector('form[data-feed-draft-kind="post"]');
  if (composer) {
    var classId = composer.getAttribute('data-feed-draft-class');
    var composerText = composer.querySelector('[data-feed-draft-input]');
    var params = new URLSearchParams(window.location.search);
    var postedNow = params.get('posted') === '1';
    // A duplicate (?copy=) is server-rendered into the box; the older local
    // draft must not overwrite it, so drop the saved draft and keep the copy.
    if (params.get('copy')) clearDraft(postDraftKey(classId));
    wireDraft(composer, composerText, postDraftKey(classId), postedNow);
  }

  // -----------------------------------------------------------------------
  // Collapsible composer — phones start folded, a draft or ?copy= keeps it open
  // -----------------------------------------------------------------------

  document.querySelectorAll('[data-feed-compose]').forEach(function (box) {
    var toggle = box.querySelector('[data-feed-compose-toggle]');
    var bodyEl = box.querySelector('.feed-compose__body');
    if (!toggle || !bodyEl) return;
    var textarea = bodyEl.querySelector('textarea');
    var hasText = textarea && textarea.value.trim();
    var params = new URLSearchParams(window.location.search);
    var forceOpen = params.get('copy') || window.location.hash === '#feedComposeBody';
    var narrow = window.matchMedia('(max-width: 991px)').matches;
    if (narrow && !hasText && !forceOpen) box.classList.add('is-collapsed');

    function setOpen(open) {
      box.classList.toggle('is-collapsed', !open);
      toggle.setAttribute('aria-expanded', open ? 'true' : 'false');
      if (open && textarea) textarea.focus();
    }

    toggle.addEventListener('click', function () {
      setOpen(box.classList.contains('is-collapsed'));
    });
    if (forceOpen) setOpen(true);
  });

  // -----------------------------------------------------------------------
  // Relative time — tap the "2h ago" to see the exact Nairobi time
  // -----------------------------------------------------------------------

  document.addEventListener('click', function (event) {
    var btn = event.target.closest('[data-feed-time]');
    if (!btn) return;
    var open = btn.classList.toggle('is-open');
    btn.setAttribute('aria-pressed', open ? 'true' : 'false');
  });

  // -----------------------------------------------------------------------
  // Mentions — insert "@Full Name " at the cursor, never free text
  // -----------------------------------------------------------------------

  document.querySelectorAll('[data-feed-mention]').forEach(function (chip) {
    chip.addEventListener('click', function () {
      var name = chip.getAttribute('data-feed-mention');
      if (!name) return;
      var form = chip.closest('form');
      var target = form && form.querySelector('textarea');
      if (!target) target = document.querySelector('[data-feed-draft-input]');
      if (!target) return;

      var token = '@' + name + ' ';
      var start = target.selectionStart || 0;
      var end = target.selectionEnd || 0;
      var value = target.value || '';
      target.value = value.slice(0, start) + token + value.slice(end);
      target.focus();
      var caret = start + token.length;
      try {
        target.setSelectionRange(caret, caret);
      } catch (err) {
        /* old browsers: focus alone is enough */
      }
      target.dispatchEvent(new Event('input', { bubbles: true }));
    });
  });

  // Reply composer (thread page)
  var replyForm = document.querySelector('form[data-feed-reply-form]');
  if (replyForm) {
    var replyPostId = replyForm.getAttribute('data-feed-reply-post');
    var replyClassId = replyForm.getAttribute('data-feed-reply-class');
    var replyText = replyForm.querySelector('[data-feed-reply-input]');
    var replyDone = replyForm.getAttribute('data-feed-reply-clear') === '1';
    var replyKey = replyDraftKey(replyPostId);
    wireDraft(replyForm, replyText, replyKey, replyDone);
    // Keep the class id in the key namespace as well, so a future class
    // switch cannot collide two identical post ids.
    if (replyClassId && replyText) {
      replyText.setAttribute('data-feed-reply-class', replyClassId);
    }

    // Starters: append the plain text and leave the cursor at the end, so the
    // writer carries on typing rather than replacing the prompt.
    document.querySelectorAll('[data-feed-starter]').forEach(function (btn) {
      btn.addEventListener('click', function () {
        if (!replyText) return;
        var starter = btn.getAttribute('data-feed-starter') || '';
        if (!starter) return;
        var current = replyText.value;
        var separator = current && !/\s$/.test(current) ? ' ' : '';
        replyText.value = current + separator + starter;
        replyText.focus();
        var end = replyText.value.length;
        try {
          replyText.setSelectionRange(end, end);
        } catch (err) {
          /* very old browsers: focus alone is enough */
        }
        replyText.dispatchEvent(new Event('input', { bubbles: true }));
      });
    });
  }

  // -----------------------------------------------------------------------
  // Rules banner — once per session
  // -----------------------------------------------------------------------

  var banner = document.getElementById('feedRulesBanner');
  if (banner) {
    var feedRoot = document.getElementById('feedCommunity');
    var rulesKey = 'feedRulesHidden_' + (feedRoot ? feedRoot.getAttribute('data-feed-class-id') : '');
    try {
      if (!window.sessionStorage.getItem(rulesKey)) banner.classList.add('visible');
    } catch (err) {
      banner.classList.add('visible');
    }
    window.dismissFeedRules = function () {
      try {
        window.sessionStorage.setItem(rulesKey, '1');
      } catch (err) {
        /* ignore */
      }
      banner.style.display = 'none';
    };
  }

  // -----------------------------------------------------------------------
  // Reactions — in-place update, normal POST without JS
  // -----------------------------------------------------------------------

  function applyReaction(article, json) {
    article.querySelectorAll('.feed-react').forEach(function (btn) {
      var emoji = btn.getAttribute('data-emoji');
      if (!emoji) return;
      var count = (json.counts && json.counts[emoji]) || 0;
      var countSpan = btn.querySelector('.feed-react__count');
      if (count > 0) {
        if (!countSpan) {
          countSpan = document.createElement('span');
          countSpan.className = 'feed-react__count';
          countSpan.setAttribute('data-count-for', emoji);
          btn.appendChild(countSpan);
        }
        countSpan.textContent = count;
      } else if (countSpan) {
        countSpan.remove();
      }
      var active = json.viewer_emoji && json.viewer_emoji === emoji;
      btn.classList.toggle('feed-react--active', !!active);
      btn.setAttribute('aria-pressed', active ? 'true' : 'false');
    });
    var hint = article.querySelector('.feed-react-hint');
    var total = 0;
    Object.keys(json.counts || {}).forEach(function (key) {
      total += json.counts[key] || 0;
    });
    if (hint) hint.hidden = total > 0;
  }

  function wireReactions(scope) {
    scope.querySelectorAll('.reaction-form').forEach(function (form) {
      if (form.dataset.reactionWired === '1') return;
      form.dataset.reactionWired = '1';
      form.addEventListener('submit', function (event) {
        var btn = form.querySelector('button[type="submit"]');
        if (btn && btn.disabled) return;
        event.preventDefault();
        fetch(form.action, {
          method: 'POST',
          body: new FormData(form),
          headers: { 'X-Requested-With': 'XMLHttpRequest' }
        })
          .then(function (response) { return response.json(); })
          .then(function (json) {
            if (!json.ok) return;
            var article = form.closest('article');
            if (article) applyReaction(article, json);
          })
          .catch(function () {
            form.submit();
          });
      });
    });
  }
  wireReactions(document);

  // -----------------------------------------------------------------------
  // Show more — fetch the next page and append the server-rendered cards
  // -----------------------------------------------------------------------

  var feedRootEl = document.getElementById('feedCommunity');
  var stream = document.getElementById('feedStream');
  var showMoreBtn = document.getElementById('feedShowMore');
  var skeletons = document.getElementById('feedSkeletons');

  function setShowMoreState(hasMore, nextBefore) {
    if (!showMoreBtn) return;
    if (!hasMore) {
      showMoreBtn.hidden = true;
      if (stream) stream.setAttribute('data-feed-has-more', '0');
      return;
    }
    showMoreBtn.hidden = false;
    showMoreBtn.setAttribute('data-feed-before', nextBefore || '');
    if (stream) {
      stream.setAttribute('data-feed-has-more', '1');
      stream.setAttribute('data-feed-next-before', nextBefore || '');
    }
  }

  if (showMoreBtn && stream) {
    showMoreBtn.addEventListener('click', function () {
      if (showMoreBtn.disabled) return;
      var url = showMoreBtn.getAttribute('data-feed-items-url');
      var filter = showMoreBtn.getAttribute('data-feed-filter') || 'all';
      var before = showMoreBtn.getAttribute('data-feed-before') || '';
      var search = showMoreBtn.getAttribute('data-feed-search') || '';
      var params = new URLSearchParams();
      params.set('filter', filter);
      if (search) params.set('q', search);
      if (before) params.set('before_id', before);

      showMoreBtn.disabled = true;
      showMoreBtn.textContent = 'Loading…';
      if (skeletons) skeletons.hidden = false;

      fetch(url + '?' + params.toString(), { headers: { 'X-Requested-With': 'XMLHttpRequest' } })
        .then(function (response) {
          if (!response.ok) throw new Error('HTTP ' + response.status);
          return response.text();
        })
        .then(function (html) {
          var doc = new DOMParser().parseFromString(html, 'text/html');
          var empty = stream.querySelector('.feed-empty');
          if (empty) empty.remove();
          doc.querySelectorAll('article.feed-post').forEach(function (article) {
            stream.appendChild(article);
          });
          wireReactions(stream);
          var marker = doc.querySelector('[data-feed-page]');
          var hasMore = marker && marker.getAttribute('data-has-more') === '1';
          var nextBefore = marker ? marker.getAttribute('data-next-before-id') : '';
          setShowMoreState(hasMore, nextBefore);
        })
        .catch(function () {
          showMoreBtn.insertAdjacentHTML(
            'afterend',
            '<div class="text-muted-sm mt-2" role="status">Could not load more right now. Check your connection and try again.</div>'
          );
        })
        .then(function () {
          showMoreBtn.disabled = false;
          showMoreBtn.textContent = 'Show more';
          if (skeletons) skeletons.hidden = true;
        });
    });
  }

  // -----------------------------------------------------------------------
  // Polite poll — visible tab only, random delay, back off, stop when idle
  // -----------------------------------------------------------------------

  var pollUrl = feedRootEl && feedRootEl.getAttribute('data-feed-poll-url');
  var latestId = feedRootEl && feedRootEl.getAttribute('data-feed-latest-id');
  var pollFilter = feedRootEl && (feedRootEl.getAttribute('data-feed-filter') || 'all');
  var newPostsBtn = document.getElementById('feedNewPostsBtn');
  var newPostsCount = document.getElementById('feedNewPostsCount');

  if (pollUrl && latestId && newPostsBtn) {
    var BASE_MIN = 30000;
    var BASE_MAX = 60000;
    var BACKOFF_MAX = 180000;
    var IDLE_LIMIT = 15 * 60 * 1000;
    var timer = null;
    var backoff = 0;
    var lastActivity = Date.now();

    function schedule(delay) {
      if (timer) window.clearTimeout(timer);
      timer = window.setTimeout(poll, delay);
    }

    function randomBase() {
      return BASE_MIN + Math.floor(Math.random() * (BASE_MAX - BASE_MIN));
    }

    function markActivity() {
      lastActivity = Date.now();
      if (!timer) schedule(randomBase());
    }

    function poll() {
      timer = null;
      if (document.visibilityState !== 'visible') return;
      if (Date.now() - lastActivity > IDLE_LIMIT) return; // quiet until touched

      var params = new URLSearchParams();
      params.set('after', latestId);
      params.set('filter', pollFilter);

      fetch(pollUrl + '?' + params.toString(), { headers: { 'X-Requested-With': 'XMLHttpRequest' } })
        .then(function (response) {
          if (!response.ok) throw new Error('HTTP ' + response.status);
          return response.json();
        })
        .then(function (json) {
          backoff = 0;
          var count = json.count || 0;
          if (count > 0) {
            newPostsBtn.hidden = false;
            if (newPostsCount) newPostsCount.textContent = count;
          } else {
            newPostsBtn.hidden = true;
          }
          schedule(randomBase());
        })
        .catch(function () {
          backoff = Math.min(BACKOFF_MAX, Math.max(BASE_MAX, backoff * 2 || BASE_MAX));
          schedule(backoff);
        });
    }

    newPostsBtn.addEventListener('click', function () {
      window.location.reload();
    });

    ['pointerdown', 'keydown', 'touchstart', 'scroll'].forEach(function (name) {
      window.addEventListener(name, markActivity, { passive: true });
    });

    document.addEventListener('visibilitychange', function () {
      if (document.visibilityState === 'visible') {
        schedule(2000 + Math.floor(Math.random() * 3000));
      } else if (timer) {
        window.clearTimeout(timer);
        timer = null;
      }
    });

    schedule(randomBase());
  }

  // -----------------------------------------------------------------------
  // Photos and files — picked list, size warnings, alt-text requirement
  // -----------------------------------------------------------------------

  function formatMb(bytes) {
    return (Math.round((bytes / 1048576) * 10) / 10) + ' MB';
  }

  function pickedChip(text, warn) {
    var el = document.createElement('span');
    el.className = 'feed-media-picked__chip' + (warn ? ' feed-media-picked__chip--warn' : '');
    el.textContent = text;
    return el;
  }

  function trimFiles(input, limit) {
    if (!input.files || input.files.length <= limit) return;
    if (typeof DataTransfer === 'undefined') return;
    var dt = new DataTransfer();
    for (var i = 0; i < limit; i += 1) dt.items.add(input.files[i]);
    input.files = dt.files;
  }

  var photoInput = document.querySelector('[data-feed-photo-input]');
  var fileInput = document.querySelector('[data-feed-file-input]');
  var pickedBox = document.querySelector('[data-feed-picked]');
  var altInput = document.querySelector('[data-feed-alt]');

  function renderPicked() {
    if (!pickedBox) return;
    pickedBox.innerHTML = '';
    [photoInput, fileInput].forEach(function (input) {
      if (!input || !input.files) return;
      var maxMb = parseInt(input.getAttribute('data-max-mb'), 10) || 0;
      Array.prototype.forEach.call(input.files, function (file) {
        var tooBig = maxMb && file.size > maxMb * 1048576;
        pickedBox.appendChild(pickedChip(
          file.name + ' · ' + formatMb(file.size) +
            (tooBig ? ' — too big, the limit is ' + maxMb + ' MB' : ''),
          tooBig
        ));
      });
    });
    var hasPhotos = !!(photoInput && photoInput.files && photoInput.files.length);
    if (altInput && altInput.getAttribute('data-required') === '1') {
      if (hasPhotos) {
        altInput.setAttribute('required', 'required');
      } else {
        altInput.removeAttribute('required');
      }
    }
  }

  [photoInput, fileInput].forEach(function (input) {
    if (!input) return;
    input.addEventListener('change', function () {
      var limit = parseInt(input.getAttribute('data-limit'), 10) || 0;
      var before = input.files ? input.files.length : 0;
      if (limit) trimFiles(input, limit);
      renderPicked();
      if (limit && before > limit && pickedBox) {
        pickedBox.appendChild(pickedChip('Only the first ' + limit + ' can be sent.', true));
      }
    });
  });
  renderPicked();

  // -----------------------------------------------------------------------
  // Photo viewer — one dialog per page, image from the permission-checked view
  // -----------------------------------------------------------------------

  var lightbox = document.querySelector('[data-feed-lightbox]');
  if (lightbox) {
    var lightboxStage = lightbox.querySelector('[data-feed-lightbox-stage]');
    var lightboxCaption = lightbox.querySelector('[data-feed-lightbox-caption]');
    var lightboxDownload = lightbox.querySelector('[data-feed-lightbox-download]');
    var lightboxClose = lightbox.querySelector('[data-feed-lightbox-close]');
    var lightboxReturn = null;

    function openLightbox(trigger) {
      var full = trigger.getAttribute('data-full');
      if (!lightboxStage || !full) return;
      var caption = trigger.getAttribute('data-caption') || '';
      // The image is created only when asked for, so a page with no photos
      // carries no <img> and a link card never looks like it has a preview.
      var img = document.createElement('img');
      img.className = 'feed-lightbox__img';
      img.src = full;
      img.alt = caption || 'Photo shared in the class feed';
      lightboxStage.innerHTML = '';
      lightboxStage.appendChild(img);
      if (lightboxCaption) lightboxCaption.textContent = caption;
      if (lightboxDownload) {
        lightboxDownload.href = trigger.getAttribute('data-download') || full;
      }
      lightboxReturn = document.activeElement;
      lightbox.hidden = false;
      document.body.classList.add('feed-lightbox-open');
      if (lightboxClose) lightboxClose.focus();
    }

    function closeLightbox() {
      if (lightbox.hidden) return;
      lightbox.hidden = true;
      document.body.classList.remove('feed-lightbox-open');
      if (lightboxStage) lightboxStage.innerHTML = '';
      if (lightboxReturn && lightboxReturn.focus) lightboxReturn.focus();
      lightboxReturn = null;
    }

    document.addEventListener('click', function (event) {
      var trigger = event.target.closest('[data-feed-photo]');
      if (trigger) {
        event.preventDefault();
        openLightbox(trigger);
        return;
      }
      if (event.target.closest('[data-feed-lightbox-close]')) {
        closeLightbox();
        return;
      }
      if (event.target === lightbox) closeLightbox();
    });

    document.addEventListener('keydown', function (event) {
      if (event.key === 'Escape') closeLightbox();
    });
  }
})();
