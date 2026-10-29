/**
 * base.js — School Management System
 * Sidebar toggle, toast auto-dismiss, active nav highlighting.
 */

(function () {
  "use strict";

  /* ── Sidebar toggle (mobile) ──────────────────────────────────────────── */
  const sidebar  = document.querySelector(".app-sidebar");
  const overlay  = document.querySelector(".sidebar-overlay");
  const toggles  = document.querySelectorAll(".sidebar-toggle");

  function openSidebar() {
    sidebar?.classList.add("open");
    overlay?.classList.add("show");
    document.body.style.overflow = "hidden";
  }

  function closeSidebar() {
    sidebar?.classList.remove("open");
    overlay?.classList.remove("show");
    document.body.style.overflow = "";
  }

  toggles.forEach((btn) => btn.addEventListener("click", openSidebar));
  overlay?.addEventListener("click", closeSidebar);

  /* Close sidebar on Escape */
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape") closeSidebar();
  });

  /* ── Sidebar sub-menu accordion ───────────────────────────────────────── */
  document.querySelectorAll(".sidebar-item.has-children").forEach((item) => {
    item.addEventListener("click", (e) => {
      e.preventDefault();
      const submenu = item.nextElementSibling;
      const isOpen  = item.classList.contains("open");

      // Close all open sub-menus
      document.querySelectorAll(".sidebar-item.has-children.open").forEach((el) => {
        el.classList.remove("open");
        el.nextElementSibling?.classList.remove("open");
      });

      if (!isOpen) {
        item.classList.add("open");
        submenu?.classList.add("open");
      }
    });
  });

  /* Open submenu if a child is active */
  document.querySelectorAll(".sidebar-subitem.active").forEach((el) => {
    const parent = el.closest(".sidebar-submenu");
    if (parent) {
      parent.classList.add("open");
      parent.previousElementSibling?.classList.add("open");
    }
  });

  /* ── Toast auto-dismiss ───────────────────────────────────────────────── */
  document.querySelectorAll(".toast-msg").forEach((toast) => {
    const closeBtn = toast.querySelector(".toast-close");
    closeBtn?.addEventListener("click", () => dismissToast(toast));
    setTimeout(() => dismissToast(toast), 5000);
  });

  function dismissToast(el) {
    el.style.transition = "opacity 0.3s ease, transform 0.3s ease";
    el.style.opacity    = "0";
    el.style.transform  = "translateX(120%)";
    setTimeout(() => el.remove(), 350);
  }

  /* ── Confirm dialogs ──────────────────────────────────────────────────── */
  document.querySelectorAll("[data-confirm]").forEach((el) => {
    el.addEventListener("click", (e) => {
      if (!confirm(el.dataset.confirm)) e.preventDefault();
    });
  });

})();
