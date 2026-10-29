/* =============================================================================
   school-website/js/main.js
   Vanilla JS for all 5 school website pages.
   ============================================================================= */

(function () {
  'use strict';

  /* ── Helpers ──────────────────────────────────────────────────────────── */
  const $  = (s, c = document) => c.querySelector(s);
  const $$ = (s, c = document) => [...c.querySelectorAll(s)];
  const on = (el, ev, fn) => el && el.addEventListener(ev, fn);

  /* ── 1. Navbar mobile toggle ──────────────────────────────────────────── */
  const navbar = $('.navbar');
  const navToggle = $('.navbar__toggle');
  on(navToggle, 'click', () => navbar && navbar.classList.toggle('is-open'));

  /* ── 2. Dark mode toggle ──────────────────────────────────────────────── */
  const themeToggle = $('.theme-toggle');
  const STORAGE_KEY = 'school-theme';

  function applyTheme(theme) {
    document.documentElement.setAttribute('data-theme', theme);
    if (themeToggle) {
      themeToggle.textContent = theme === 'light' ? '\u263D' : '\u2600'; // moon / sun
      themeToggle.setAttribute('aria-label', theme === 'light' ? 'Switch to dark mode' : 'Switch to light mode');
    }
  }

  const savedTheme = localStorage.getItem(STORAGE_KEY) ||
    (window.matchMedia('(prefers-color-scheme: light)').matches ? 'light' : 'dark');
  applyTheme(savedTheme);

  on(themeToggle, 'click', () => {
    const current = document.documentElement.getAttribute('data-theme');
    const next = current === 'light' ? 'dark' : 'light';
    applyTheme(next);
    localStorage.setItem(STORAGE_KEY, next);
  });

  /* ── 3. Carousel (hero) ────────────────────────────────────────────────── */
  function initCarousel() {
    const carousel = $('.carousel');
    if (!carousel) return;

    const track = $('.carousel__track', carousel);
    const slides = $$('.carousel__slide', carousel);
    const dotsContainer = $('.carousel__dots', carousel);
    const prevBtn = $('.carousel__btn--prev', carousel);
    const nextBtn = $('.carousel__btn--next', carousel);
    let index = 0;
    let autoTimer;

    // Build dots
    if (dotsContainer) {
      slides.forEach((_, i) => {
        const dot = document.createElement('button');
        dot.className = 'carousel__dot' + (i === 0 ? ' is-active' : '');
        dot.setAttribute('aria-label', `Go to slide ${i + 1}`);
        dot.addEventListener('click', () => goTo(i));
        dotsContainer.appendChild(dot);
      });
    }
    const dots = $$('.carousel__dot', carousel);

    function goTo(i) {
      index = (i + slides.length) % slides.length;
      track.style.transform = `translateX(-${index * 100}%)`;
      dots.forEach((d, di) => d.classList.toggle('is-active', di === index));
    }

    function next() { goTo(index + 1); }
    function prev() { goTo(index - 1); }

    on(nextBtn, 'click', () => { next(); resetAuto(); });
    on(prevBtn, 'click', () => { prev(); resetAuto(); });

    // Autoplay
    function startAuto() {
      autoTimer = setInterval(next, 5000);
    }
    function resetAuto() {
      clearInterval(autoTimer);
      startAuto();
    }

    // Pause on hover
    on(carousel, 'mouseenter', () => clearInterval(autoTimer));
    on(carousel, 'mouseleave', startAuto);

    // Swipe support
    let touchStartX = 0;
    on(carousel, 'touchstart', (e) => { touchStartX = e.touches[0].clientX; }, { passive: true });
    on(carousel, 'touchend', (e) => {
      const dx = e.changedTouches[0].clientX - touchStartX;
      if (Math.abs(dx) > 50) { dx < 0 ? next() : prev(); resetAuto(); }
    }, { passive: true });

    // Keyboard
    on(carousel, 'keydown', (e) => {
      if (e.key === 'ArrowLeft') { prev(); resetAuto(); }
      if (e.key === 'ArrowRight') { next(); resetAuto(); }
    });

    startAuto();
  }
  initCarousel();

  /* ── 4. Testimonial slider ─────────────────────────────────────────────── */
  function initTestimonials() {
    const container = $('.testimonials');
    if (!container) return;

    const track = $('.testimonial-track', container);
    const slides = $$('.testimonial', container);
    const dotsContainer = $('.testimonial__dots', container);
    let index = 0;

    if (dotsContainer && slides.length > 1) {
      slides.forEach((_, i) => {
        const dot = document.createElement('button');
        dot.className = 'testimonial__dot' + (i === 0 ? ' is-active' : '');
        dot.setAttribute('aria-label', `Testimonial ${i + 1}`);
        dot.addEventListener('click', () => goTo(i));
        dotsContainer.appendChild(dot);
      });
    }
    const dots = $$('.testimonial__dot', container);

    function goTo(i) {
      index = (i + slides.length) % slides.length;
      if (track) track.style.transform = `translateX(-${index * 100}%)`;
      dots.forEach((d, di) => d.classList.toggle('is-active', di === index));
    }

    if (slides.length > 1) {
      setInterval(() => goTo(index + 1), 6000);
    }
  }
  initTestimonials();

  /* ── 5. Gallery filter + lightbox ──────────────────────────────────────── */
  function initGallery() {
    const gallery = $('.gallery');
    if (!gallery) return;

    const items = $$('.gallery__item', gallery);
    const filterBtns = $$('.filter-btn');

    filterBtns.forEach(btn => {
      btn.addEventListener('click', () => {
        const filter = btn.dataset.filter;
        filterBtns.forEach(b => b.classList.remove('is-active'));
        btn.classList.add('is-active');
        items.forEach(item => {
          const cat = item.dataset.category;
          item.style.display = (filter === 'all' || cat === filter) ? '' : 'none';
        });
      });
    });

    // Lightbox
    const lightbox = $('.lightbox');
    const lbImg = $('.lightbox__img');
    const lbCaption = $('.lightbox__caption');
    let lbIndex = 0;
    const visibleItems = () => items.filter(i => i.style.display !== 'none');

    items.forEach((item, i) => {
      item.addEventListener('click', () => {
        const imgs = visibleItems();
        lbIndex = imgs.indexOf(item);
        openLightbox(imgs);
      });
    });

    function openLightbox(imgs) {
      if (!lightbox) return;
      lightbox.classList.add('is-open');
      document.body.style.overflow = 'hidden';
      showImage(imgs);
    }

    function showImage(imgs) {
      const item = imgs[lbIndex];
      const img = $('img', item);
      const caption = $('.gallery__overlay span', item);
      if (lbImg) { lbImg.src = img.src; lbImg.alt = img.alt; }
      if (lbCaption) lbCaption.textContent = caption ? caption.textContent : img.alt;
    }

    function closeLightbox() {
      if (!lightbox) return;
      lightbox.classList.remove('is-open');
      document.body.style.overflow = '';
    }

    function lbNav(dir) {
      const imgs = visibleItems();
      lbIndex = (lbIndex + dir + imgs.length) % imgs.length;
      showImage(imgs);
    }

    on($('.lightbox__close'), 'click', closeLightbox);
    on($('.lightbox__nav--prev'), 'click', () => lbNav(-1));
    on($('.lightbox__nav--next'), 'click', () => lbNav(1));
    on(lightbox, 'click', (e) => { if (e.target === lightbox) closeLightbox(); });

    document.addEventListener('keydown', (e) => {
      if (!lightbox || !lightbox.classList.contains('is-open')) return;
      if (e.key === 'Escape') closeLightbox();
      if (e.key === 'ArrowLeft') lbNav(-1);
      if (e.key === 'ArrowRight') lbNav(1);
    });
  }
  initGallery();

  /* ── 6. Tabs ───────────────────────────────────────────────────────────── */
  function initTabs() {
    const tabButtons = $$('.tab');
    if (!tabButtons.length) return;

    tabButtons.forEach(btn => {
      btn.addEventListener('click', () => {
        const target = btn.dataset.tab;
        const container = btn.closest('[data-tabs]') || document;
        $$('.tab', container).forEach(b => b.classList.remove('is-active'));
        $$('.tab-panel', container).forEach(p => p.classList.remove('is-active'));
        btn.classList.add('is-active');
        const panel = $(`[data-panel="${target}"]`, container);
        if (panel) panel.classList.add('is-active');
      });
    });
  }
  initTabs();

  /* ── 7. Accordion ──────────────────────────────────────────────────────── */
  function initAccordion() {
    const items = $$('.accordion__item');
    if (!items.length) return;

    items.forEach(item => {
      const header = $('.accordion__header', item);
      const body = $('.accordion__body', item);
      on(header, 'click', () => {
        const isOpen = item.classList.contains('is-open');
        items.forEach(i => { i.classList.remove('is-open'); const b = $('.accordion__body', i); if (b) b.style.maxHeight = null; });
        if (!isOpen) {
          item.classList.add('is-open');
          body.style.maxHeight = body.scrollHeight + 'px';
        }
      });
    });
  }
  initAccordion();

  /* ── 8. Scroll reveal (IntersectionObserver) ───────────────────────────── */
  function initScrollReveal() {
    const reveals = $$('[data-reveal]');
    if (!reveals.length) return;

    if (!('IntersectionObserver' in window)) {
      reveals.forEach(el => el.classList.add('is-visible'));
      return;
    }

    const observer = new IntersectionObserver((entries) => {
      entries.forEach(entry => {
        if (entry.isIntersecting) {
          entry.target.classList.add('is-visible');
          observer.unobserve(entry.target);
        }
      });
    }, { threshold: 0.15, rootMargin: '0px 0px -50px 0px' });

    reveals.forEach(el => observer.observe(el));
  }
  initScrollReveal();

  /* ── 9. Animated stat counters ─────────────────────────────────────────── */
  function initCounters() {
    const counters = $$('.stats__num[data-count]');
    if (!counters.length) return;

    function animate(el) {
      const target = parseInt(el.dataset.count, 10);
      const suffix = el.dataset.suffix || '';
      const duration = 1500;
      const start = performance.now();

      function step(now) {
        const progress = Math.min((now - start) / duration, 1);
        const eased = 1 - Math.pow(1 - progress, 3); // easeOutCubic
        el.innerHTML = Math.floor(eased * target).toLocaleString() + suffix;
        if (progress < 1) requestAnimationFrame(step);
        else el.innerHTML = target.toLocaleString() + suffix;
      }
      requestAnimationFrame(step);
    }

    if (!('IntersectionObserver' in window)) {
      counters.forEach(animate);
      return;
    }

    const observer = new IntersectionObserver((entries) => {
      entries.forEach(entry => {
        if (entry.isIntersecting) {
          animate(entry.target);
          observer.unobserve(entry.target);
        }
      });
    }, { threshold: 0.5 });

    counters.forEach(el => observer.observe(el));
  }
  initCounters();

  /* ── 10. Form validation ───────────────────────────────────────────────── */
  function initForm() {
    const form = $('#inquiry-form');
    if (!form) return;

    const successMsg = $('.form-success');

    function showError(field, msg) {
      const input = $(`[name="${field}"]`, form);
      const error = $(`[data-error="${field}"]`, form);
      if (input) input.classList.add('is-invalid');
      if (error) { error.textContent = msg; error.classList.add('is-visible'); }
    }

    function clearError(field) {
      const input = $(`[name="${field}"]`, form);
      const error = $(`[data-error="${field}"]`, form);
      if (input) input.classList.remove('is-invalid');
      if (error) error.classList.remove('is-visible');
    }

    function validate() {
      let valid = true;
      const name = $('[name="name"]', form);
      const email = $('[name="email"]', form);
      const phone = $('[name="phone"]', form);
      const message = $('[name="message"]', form);

      ['name','email','phone','message'].forEach(clearError);

      if (name && !name.value.trim()) { showError('name', 'Please enter your name.'); valid = false; }
      if (email && !email.value.trim()) { showError('email', 'Please enter your email.'); valid = false; }
      else if (email && !/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email.value)) { showError('email', 'Please enter a valid email address.'); valid = false; }
      if (phone && phone.value.trim() && !/^[\d\s()+\-]{7,20}$/.test(phone.value)) { showError('phone', 'Please enter a valid phone number.'); valid = false; }
      if (message && !message.value.trim()) { showError('message', 'Please enter a message.'); valid = false; }

      return valid;
    }

    on(form, 'submit', (e) => {
      e.preventDefault();
      if (!validate()) return;

      // Show success state
      if (successMsg) {
        successMsg.classList.add('is-visible');
        form.reset();
        successMsg.scrollIntoView({ behavior: 'smooth', block: 'center' });
        setTimeout(() => successMsg.classList.remove('is-visible'), 6000);
      }
    });
  }
  initForm();

  /* ── 11. Back to top ───────────────────────────────────────────────────── */
  const backToTop = $('.back-to-top');
  if (backToTop) {
    window.addEventListener('scroll', () => {
      backToTop.classList.toggle('is-visible', window.scrollY > 600);
    }, { passive: true });
    on(backToTop, 'click', () => window.scrollTo({ top: 0, behavior: 'smooth' }));
  }

  /* ── 12. Active nav highlight ──────────────────────────────────────────── */
  function highlightNav() {
    const path = window.location.pathname.split('/').pop() || 'index.html';
    $$('.navbar__link').forEach(link => {
      const href = link.getAttribute('href');
      if (href === path || (path === '' && href === 'index.html')) {
        link.classList.add('is-active');
      }
    });
  }
  highlightNav();

  /* ── 13. Lazy load images ──────────────────────────────────────────────── */
  function initLazyImages() {
    const imgs = $$('img[loading="lazy"]');
    if (!imgs.length || !('IntersectionObserver' in window)) return;

    const observer = new IntersectionObserver((entries) => {
      entries.forEach(entry => {
        if (entry.isIntersecting) {
          const img = entry.target;
          if (img.dataset.src) { img.src = img.dataset.src; img.removeAttribute('data-src'); }
          observer.unobserve(img);
        }
      });
    }, { rootMargin: '200px' });

    imgs.forEach(img => observer.observe(img));
  }
  initLazyImages();

})();
