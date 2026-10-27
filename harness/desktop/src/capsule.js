window.capsule.onUpdate(({ label, where }) => {
  document.getElementById("label").textContent = label || "Working…";
  document.getElementById("where").textContent = where;
});
document.getElementById("stop").addEventListener("click", () => window.capsule.stop());
