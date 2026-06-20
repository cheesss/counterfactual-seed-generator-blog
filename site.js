const search = document.querySelector("#post-search");
const cards = Array.from(document.querySelectorAll("#post-grid .article-card"));

if (search) {
  search.addEventListener("input", () => {
    const query = search.value.trim().toLowerCase();
    for (const card of cards) {
      const text = card.innerText.toLowerCase();
      card.hidden = query.length > 0 && !text.includes(query);
    }
  });
}
