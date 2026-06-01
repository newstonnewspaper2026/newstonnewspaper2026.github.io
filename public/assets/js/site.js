const toggle = document.querySelector(".nav-toggle");
const nav = document.querySelector(".site-nav");

if (toggle && nav) {
  toggle.addEventListener("click", () => {
    const isOpen = nav.classList.toggle("is-open");
    toggle.setAttribute("aria-expanded", String(isOpen));
  });
}

document.querySelectorAll("[data-search-scope]").forEach((input) => {
  const scope = input.getAttribute("data-search-scope");
  const container = document.querySelector(`[data-search-container="${scope}"]`);
  if (!container) return;

  input.addEventListener("input", () => {
    const query = input.value.trim().toLowerCase();
    container.querySelectorAll("[data-search-text]").forEach((card) => {
      const text = card.getAttribute("data-search-text") || "";
      card.classList.toggle("is-hidden", Boolean(query) && !text.includes(query));
    });
  });
});
