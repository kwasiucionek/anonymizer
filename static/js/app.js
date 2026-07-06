/* Drobne zachowania UI — bez frameworka, delegacja zdarzeń na dokumencie,
   więc działa też dla wierszy podmienianych przez HTMX. */

/* "Zaznacz wszystkie" w tabelach z masowym usuwaniem. */
document.addEventListener("change", (e) => {
  const master = e.target.closest("[data-check-all]");
  if (!master) return;
  const form = master.closest("form");
  if (!form) return;
  form.querySelectorAll('input[name="dokumenty"]').forEach((cb) => {
    cb.checked = master.checked;
  });
});

document.addEventListener("click", (e) => {
  /* Przełącznik paneli (Podgląd / Surowy plik). */
  const seg = e.target.closest("[data-show]");
  if (seg) {
    const group = seg.closest(".seg");
    group.querySelectorAll("[data-show]").forEach((btn) => {
      const on = btn === seg;
      btn.classList.toggle("active", on);
      btn.setAttribute("aria-pressed", on);
      const panel = document.querySelector(btn.dataset.show);
      if (panel) panel.hidden = !on;
    });
    return;
  }

  /* Kopiowanie zawartości elementu do schowka. */
  const copyBtn = e.target.closest("[data-copy]");
  if (copyBtn) {
    const source = document.querySelector(copyBtn.dataset.copy);
    if (!source) return;
    copyText(source.innerText).then(() => {
      const original = copyBtn.textContent;
      copyBtn.textContent = "Skopiowano ✓";
      setTimeout(() => {
        copyBtn.textContent = original;
      }, 1500);
    });
  }
});

function copyText(text) {
  if (navigator.clipboard && window.isSecureContext) {
    return navigator.clipboard.writeText(text);
  }
  /* Fallback dla http w sieci lokalnej (bez secure context). */
  const ta = document.createElement("textarea");
  ta.value = text;
  ta.style.position = "fixed";
  ta.style.opacity = "0";
  document.body.appendChild(ta);
  ta.select();
  document.execCommand("copy");
  ta.remove();
  return Promise.resolve();
}
