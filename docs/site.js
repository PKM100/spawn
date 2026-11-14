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
      ? "Illustration: defer lower-priority work. No model is running."
      : "Scope illustration only. No model is running.";
  });
});

document.getElementById("copy-install").addEventListener("click", async () => {
  const status = document.getElementById("copy-status");
  try {
    await navigator.clipboard.writeText(document.getElementById("install-command").textContent);
    status.textContent = "Copied. Paste into your terminal to start.";
  } catch {
    status.textContent = "Clipboard unavailable. Select and copy the commands above.";
  }
});

async function showResults() {
  try {
    const response = await fetch("results.json");
    if (!response.ok) throw new Error("Results unavailable");
    const data = await response.json();
    const body = document.getElementById("results-body");
    const names = {spawn: "Spawn", single_pass: "Fixed single pass", per_criterion: "Fixed per criterion"};
    const rows = Object.entries(names).map(([key, name]) => {
      const result = data.summary.policies[key];
      const row = document.createElement("tr");
      const heading = document.createElement("th");
      heading.scope = "row";
      heading.textContent = name;
      row.append(heading);
      const cells = [
        `${result.rubric_passes} / ${result.runs}`,
        String(result.calls),
        `${result.mean_end_to_end_seconds.toFixed(2)}s`,
        `$${result.total_usage_equivalent_usd.toFixed(7)}`,
      ];
      cells.forEach((value) => {
        const cell = document.createElement("td");
        cell.textContent = value;
        row.append(cell);
      });
      return row;
    });
    body.replaceChildren(...rows);
    document.getElementById("results-note").textContent =
      `Primary pilot: ${data.date}. ${data.summary.runs} runs; one repetition per task/policy. ` +
      "Time includes planning, CLI/SDK startup, calls, checks, and receipts. " +
      "Calls count application-level SDK invocations. " +
      "Dollars are Copilot usage equivalents, not incremental invoice spend. " +
      "Setup smoke and environment installation are excluded from policy comparisons.";
  } catch {
    document.getElementById("results-note").textContent =
      "The comparison could not load. The frozen protocol and raw JSON records remain available on GitHub.";
  }
}

showResults();
