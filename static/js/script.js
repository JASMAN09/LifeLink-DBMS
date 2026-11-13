"use strict";

const menuToggle = document.querySelector(".menu-toggle");
const navigation = document.querySelector(".primary-nav");

if (menuToggle && navigation) {
  menuToggle.addEventListener("click", () => {
    const isOpen = navigation.classList.toggle("open");
    menuToggle.setAttribute("aria-expanded", String(isOpen));
  });

  navigation.querySelectorAll("a").forEach((link) => {
    link.addEventListener("click", () => {
      navigation.classList.remove("open");
      menuToggle.setAttribute("aria-expanded", "false");
    });
  });
}

const recordsSearch = document.querySelector("#records-search");
if (recordsSearch) {
  const tableRows = Array.from(document.querySelectorAll(".table-panel tbody tr"));
  const recordCount = document.querySelector("#record-count");
  recordsSearch.addEventListener("input", () => {
    const query = recordsSearch.value.trim().toLocaleLowerCase();
    let visible = 0;
    tableRows.forEach((row) => {
      const matches = row.textContent.toLocaleLowerCase().includes(query);
      row.hidden = !matches;
      if (matches) visible += 1;
    });
    if (recordCount) recordCount.textContent = `${visible} matching record(s)`;
  });
}
