/*  labeler_dialogs.js
    Keyboard and screen reader behaviour for the labelers' custom modals.

    Both labelers show and hide dialogs by toggling a `hidden` class on an
    overlay element rather than by using Bootstrap's modal JS, so none of the
    usual dialog behaviour comes for free. Instead of hooking every open/close
    call site, this watches the `class` attribute of every `[role="dialog"]`
    and reacts when one becomes visible or hidden. New dialogs added to the
    templates are picked up automatically as long as they carry the role.

    Provides, per the ARIA dialog pattern:
      - focus moves into the dialog when it opens
      - focus returns to whatever opened it when it closes
      - Tab and Shift+Tab stay inside the open dialog
      - Escape closes it, reusing the dialog's own close/cancel button so any
        existing cleanup still runs
*/
(function () {
  "use strict";

  var FOCUSABLE = [
    "a[href]",
    "button:not([disabled])",
    "input:not([disabled]):not([type='hidden'])",
    "select:not([disabled])",
    "textarea:not([disabled])",
    "[tabindex]:not([tabindex='-1'])",
  ].join(", ");

  // Dialogs opened, in order, so Escape and the focus trap act on the topmost.
  var openDialogs = [];
  var returnFocusTo = new WeakMap();

  function isVisible(element) {
    return !!(
      element.offsetWidth ||
      element.offsetHeight ||
      element.getClientRects().length
    );
  }

  function focusableWithin(dialog) {
    return Array.prototype.filter.call(
      dialog.querySelectorAll(FOCUSABLE),
      isVisible,
    );
  }

  function isOpen(dialog) {
    return !dialog.classList.contains("hidden") && isVisible(dialog);
  }

  function handleOpened(dialog) {
    if (openDialogs.indexOf(dialog) !== -1) return;
    openDialogs.push(dialog);

    var previous = document.activeElement;
    if (previous && previous !== document.body) {
      returnFocusTo.set(dialog, previous);
    }

    var targets = focusableWithin(dialog);
    if (targets.length) {
      targets[0].focus();
    } else {
      // Nothing focusable inside, so make the dialog itself the focus target
      // rather than leaving focus behind on the page underneath.
      if (!dialog.hasAttribute("tabindex"))
        dialog.setAttribute("tabindex", "-1");
      dialog.focus();
    }
  }

  function handleClosed(dialog) {
    var index = openDialogs.indexOf(dialog);
    if (index === -1) return;
    openDialogs.splice(index, 1);

    var previous = returnFocusTo.get(dialog);
    returnFocusTo.delete(dialog);
    // Only restore if the opener is still around and reachable.
    if (previous && document.contains(previous) && isVisible(previous)) {
      previous.focus();
    }
  }

  function topDialog() {
    return openDialogs.length ? openDialogs[openDialogs.length - 1] : null;
  }

  function trapTab(event) {
    var dialog = topDialog();
    if (!dialog) return;
    var targets = focusableWithin(dialog);
    if (!targets.length) {
      event.preventDefault();
      dialog.focus();
      return;
    }
    var first = targets[0];
    var last = targets[targets.length - 1];
    var active = document.activeElement;

    if (!dialog.contains(active)) {
      event.preventDefault();
      (event.shiftKey ? last : first).focus();
      return;
    }
    if (event.shiftKey && active === first) {
      event.preventDefault();
      last.focus();
    } else if (!event.shiftKey && active === last) {
      event.preventDefault();
      first.focus();
    }
  }

  function closeTopDialog() {
    var dialog = topDialog();
    if (!dialog) return;
    // Prefer the dialog's own dismiss control so its existing click handler
    // runs any cleanup (resetting forms, clearing state, and so on).
    var dismiss = Array.prototype.filter.call(
      dialog.querySelectorAll(
        '.btn-close, [data-dismiss-dialog], button[id*="close"], button[id*="cancel"]',
      ),
      isVisible,
    )[0];
    if (dismiss) {
      dismiss.click();
      return;
    }
    dialog.classList.add("hidden");
  }

  document.addEventListener(
    "keydown",
    function (event) {
      if (!openDialogs.length) return;
      if (event.key === "Escape") {
        event.preventDefault();
        closeTopDialog();
      } else if (event.key === "Tab") {
        trapTab(event);
      }
    },
    true,
  );

  function watch(dialog) {
    var observer = new MutationObserver(function () {
      if (isOpen(dialog)) {
        handleOpened(dialog);
      } else {
        handleClosed(dialog);
      }
    });
    observer.observe(dialog, {
      attributes: true,
      attributeFilter: ["class", "style"],
    });
    if (isOpen(dialog)) handleOpened(dialog);
  }

  function init() {
    Array.prototype.forEach.call(
      document.querySelectorAll('[role="dialog"]'),
      watch,
    );
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
