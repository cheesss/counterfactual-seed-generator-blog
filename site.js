const search = document.querySelector("#post-search");
const cards = Array.from(document.querySelectorAll("#post-grid .article-card"));
const chips = Array.from(document.querySelectorAll("#bottleneck-filter .chip"));
let activeFilter = "all";

function applyFilters() {
  const query = search ? search.value.trim().toLowerCase() : "";
  for (const card of cards) {
    const matchesText = query.length === 0 || card.innerText.toLowerCase().includes(query);
    const matchesChip = activeFilter === "all" || card.dataset.bottleneck === activeFilter;
    card.hidden = !(matchesText && matchesChip);
  }
}

if (search) {
  search.addEventListener("input", applyFilters);
}

for (const chip of chips) {
  chip.addEventListener("click", () => {
    activeFilter = chip.dataset.filter;
    for (const other of chips) {
      other.classList.toggle("is-active", other === chip);
    }
    applyFilters();
  });
}
