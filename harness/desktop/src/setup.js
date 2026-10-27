window.setup.onState(({ title, text, error, help }) => {
  document.getElementById("title").textContent = title;
  document.getElementById("text").textContent = text;
  document.getElementById("error").textContent = error || "";
  document.getElementById("help").hidden = !help;
  document.getElementById("retry").hidden = !error;
});
document.getElementById("retry").addEventListener("click", () => window.setup.retry());
