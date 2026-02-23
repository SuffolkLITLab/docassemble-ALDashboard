(function () {
  function widenResultsContainer() {
    const body = document.body;
    if (!body) return;
    const isResultsScreen =
      body.classList.contains("question-show-results") ||
      body.classList.contains("question-show_results");
    if (!isResultsScreen) return;
    const container = document.querySelector("div.container");
    if (!container) return;
    container.style.maxWidth = "96vw";
  }

  document.addEventListener("click", function (event) {
    const tag = event.target.closest(".al-lint-screen-id");
    if (!tag) return;
    if (event.detail === 3) {
      tag.classList.toggle("is-highlighted");
      const selection = window.getSelection();
      if (selection) {
        selection.removeAllRanges();
        const range = document.createRange();
        range.selectNodeContents(tag);
        selection.addRange(range);
      }
    }
  });

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", widenResultsContainer);
  } else {
    widenResultsContainer();
  }
})();
