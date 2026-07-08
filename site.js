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

// Scroll-reveal. Armed only when the head set .js-anim (i.e. the visitor has not
// asked for reduced motion). Elements rise+fade as they enter view; a failsafe
// reveals anything the observer never fires for.
if (document.documentElement.classList.contains("js-anim") && "IntersectionObserver" in window) {
  const io = new IntersectionObserver((entries) => {
    for (const entry of entries) {
      if (entry.isIntersecting) {
        entry.target.classList.add("is-in");
        io.unobserve(entry.target);
      }
    }
  }, { rootMargin: "0px 0px -6% 0px", threshold: 0.08 });
  for (const el of document.querySelectorAll(".reveal")) {
    const sibs = el.parentElement
      ? Array.from(el.parentElement.children).filter((c) => c.classList.contains("reveal"))
      : [el];
    el.style.transitionDelay = Math.min(sibs.indexOf(el), 6) * 60 + "ms";
    io.observe(el);
  }
  setTimeout(() => {
    for (const el of document.querySelectorAll(".reveal:not(.is-in)")) el.classList.add("is-in");
  }, 2500);
}

// Header condenses once the page scrolls past the fold edge.
const siteHeader = document.querySelector(".site-header");
if (siteHeader) {
  const onScroll = () => siteHeader.classList.toggle("is-scrolled", window.scrollY > 12);
  addEventListener("scroll", onScroll, { passive: true });
  onScroll();
}
