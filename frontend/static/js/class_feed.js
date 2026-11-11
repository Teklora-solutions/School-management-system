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
    var postedNow = new URLSearchParams(window.location.search).get('posted') === '1';
    wireDraft(composer, composerText, postDraftKey(classId), postedNow);
  }

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
    article.querySelectorAll('.reaction-btn').forEach(function (btn) {
      var emoji = btn.getAttribute('data-emoji');
      if (!emoji) return;
      var count = (json.counts && json.counts[emoji]) || 0;
      var countSpan = btn.querySelector('.reaction-count');
      if (count > 0) {
        if (!countSpan) {
          countSpan = document.createElement('span');
          countSpan.className = 'reaction-count';
          countSpan.setAttribute('data-count-for', emoji);
          btn.appendChild(document.createTextNode(' '));
          btn.appendChild(countSpan);
        }
        countSpan.textContent = count;
      } else if (countSpan) {
        if (countSpan.previousSibling) countSpan.previousSibling.remove();
        countSpan.remove();
      }
      btn.classList.remove('reaction-btn--active');
      btn.style.background = 'var(--color-surface-2)';
      btn.style.borderColor = 'var(--color-border)';
      if (json.viewer_emoji && json.viewer_emoji === emoji) {
        btn.classList.add('reaction-btn--active');
        btn.style.background = 'var(--accent-glow)';
        btn.style.borderColor = 'var(--accent)';
      }
    });
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
      var params = new URLSearchParams();
      params.set('filter', filter);
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
})();
