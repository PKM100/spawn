"use strict";

document.querySelectorAll("[data-scope]").forEach((button) => {
  button.addEventListener("click", () => {
    const shortened = button.dataset.scope === "short";
    document.querySelectorAll("[data-scope]").forEach((option) => {
      const selected = option === button;
      option.classList.toggle("active", selected);
      option.setAttribute("aria-pressed", String(selected));
    });
    const item = document.getElementById("optional-deliverable");
    item.classList.toggle("deferred", shortened);
    item.querySelector(".check").textContent = shortened ? "-" : "+";
    item.querySelector(".item-state").textContent = shortened ? "deferred" : "scheduled";
    document.getElementById("scope-note").textContent = shortened
      ? "Illustration: lower-priority work deferred."
      : "Illustration. No model is running.";
  });
});
