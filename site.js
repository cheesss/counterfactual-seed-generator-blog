const search = document.querySelector("#post-search");
const cards = Array.from(document.querySelectorAll("#post-grid .article-card"));
const bottleneckChips = Array.from(document.querySelectorAll("#bottleneck-filter .chip"));
const sectorChips = Array.from(document.querySelectorAll("#sector-filter .chip"));
let activeBottleneck = "all";
let activeSector = "all";

function applyFilters() {
  const query = search ? search.value.trim().toLowerCase() : "";
  for (const card of cards) {
    const matchesText = query.length === 0 || card.innerText.toLowerCase().includes(query);
    const matchesBottleneck = activeBottleneck === "all" || card.dataset.bottleneck === activeBottleneck;
    const matchesSector = activeSector === "all" || card.dataset.sector === activeSector;
    card.hidden = !(matchesText && matchesBottleneck && matchesSector);
  }
}

if (search) {
  search.addEventListener("input", applyFilters);
}

for (const chip of bottleneckChips) {
  chip.addEventListener("click", () => {
    activeBottleneck = chip.dataset.filter;
    for (const other of bottleneckChips) {
      other.classList.toggle("is-active", other === chip);
    }
    applyFilters();
  });
}

for (const chip of sectorChips) {
  chip.addEventListener("click", () => {
    activeSector = chip.dataset.sfilter;
    for (const other of sectorChips) {
      other.classList.toggle("is-active", other === chip);
    }
    applyFilters();
  });
}
