/* Run before first paint. Preference is independent of the OS language. */
window.Appearance = (() => {
  let theme = "light";
  try { const saved = window.SSDNativePreferences?.theme || localStorage.getItem("ssd-theme"); if (["light","dark","miku"].includes(saved)) theme=saved; } catch (_) {}
  function apply(value) {
    if (!["light","dark","miku"].includes(value)) return;
    theme=value; document.documentElement.dataset.theme=value;
    document.documentElement.style.colorScheme=value==="light"?"light":"dark";
    try { localStorage.setItem("ssd-theme",value); } catch (_) {}
    document.querySelectorAll("[data-theme-choice]").forEach(button=>button.setAttribute("aria-pressed",String(button.dataset.themeChoice===value)));
    window.webkit?.messageHandlers?.appearance?.postMessage({theme:value,language:window.I18n?.locale||"en"});
    window.dispatchEvent(new Event("appearancechange"));
  }
  if(window.webkit?.messageHandlers?.appearance)document.documentElement.classList.add("native-shell");
  apply(theme);
  document.addEventListener("DOMContentLoaded",()=>{
    document.querySelectorAll("[data-theme-choice]").forEach(button=>button.addEventListener("click",()=>apply(button.dataset.themeChoice)));
    apply(theme);
    const icons={benchmark:'<path d="M4 19V5M4 19h16M8 15V9m5 6V5m5 10v-7"/>',plots:'<circle cx="6" cy="15" r="1.5"/><circle cx="10" cy="7" r="1.5"/><circle cx="15" cy="12" r="1.5"/><circle cx="19" cy="5" r="1.5"/><path d="M3 3v18h18"/>',smart:'<path d="M20.5 5.5c-2-2-5.2-1.5-8.5 2-3.3-3.5-6.5-4-8.5-2C-.5 9.5 5 15 12 20c7-5 12.5-10.5 8.5-14.5Z"/><path d="M4 12h4l2-4 3 8 2-4h5"/>',history:'<circle cx="12" cy="12" r="8"/><path d="M12 7v5l3 2"/>',methodology:'<path d="M5 4h14v16H5zM8 8h8M8 12h8M8 16h4"/>',settings:'<path d="M4 7h16M4 17h16"/><circle cx="9" cy="7" r="3"/><circle cx="15" cy="17" r="3"/>'};
    document.querySelectorAll(".nav-item").forEach(button=>{button.querySelector(".nav-icon").innerHTML='<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.65" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">'+icons[button.dataset.page]+'</svg>';});
  });
  return {apply,get theme(){return theme;}};
})();
