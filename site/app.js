/* DALAL ARCHIVE — front-of-house logic
   One origin: the API, the site, and the videos share a domain,
   so session + CloudFront cookies ride on every fetch automatically. */
(() => {
  "use strict";

  const $ = (id) => document.getElementById(id);
  const views = {
    login: $("login-view"),
    app: $("app-view"),
    player: $("player-view"),
  };

  const state = { videos: [], username: null, folder: null, hls: null };

  /* ---------------- API (same-origin, credentials included) ------------- */

  async function api(path, opts = {}) {
    const res = await fetch(path, {
      method: opts.method || (opts.body ? "POST" : "GET"),
      credentials: "same-origin",
      headers: opts.body ? { "content-type": "application/json" } : undefined,
      body: opts.body ? JSON.stringify(opts.body) : undefined,
    });
    let data = {};
    try { data = await res.json(); } catch { /* empty body is fine */ }
    return { status: res.status, data };
  }

  /* ---------------- view switching -------------------------------------- */

  function show(name) {
    for (const [key, el] of Object.entries(views)) {
      el.hidden = key !== name;
    }
    window.scrollTo(0, 0);
  }

  /* ---------------- formatting ------------------------------------------ */

  const fmtMin = (s) => {
    const m = Math.round(s / 60);
    return m >= 60 ? `${(m / 60).toFixed(1)} HRS` : `${m} MIN`;
  };
  const totalHours = (list) =>
    `${(list.reduce((a, v) => a + v.duration_seconds, 0) / 3600).toFixed(1)} HOURS`;

  /* ---------------- folders + grids ------------------------------------- */

  const byFolder = () => {
    const map = new Map();
    for (const v of state.videos) {
      if (!map.has(v.folder)) map.set(v.folder, []);
      map.get(v.folder).push(v);
    }
    return map;
  };

  function renderFolderNav() {
    const nav = $("folder-nav");
    nav.textContent = "";
    const mk = (label, hash, active) => {
      const b = document.createElement("button");
      b.textContent = label;
      b.dataset.nav = "";
      b.dataset.hash = hash;
      b.className = active ? "active" : "";
      b.onclick = () => { location.hash = hash; };
      nav.appendChild(b);
    };
    mk("ALL", "#/", state.folder === null);
    for (const [folder, list] of byFolder()) {
      mk(`${folder.toUpperCase()} (${list.length})`,
         `#/folder/${encodeURIComponent(folder)}`,
         state.folder === folder);
    }
  }

  function renderFolders() {
    const grid = $("folder-grid");
    grid.textContent = "";
    grid.className = "folder-grid stagger";
    for (const [folder, list] of byFolder()) {
      const card = document.createElement("button");
      card.className = "folder-card";
      card.innerHTML = `
        <div>
          <span class="mint-tag">${list.length} ${list.length === 1 ? "TITLE" : "TITLES"}</span>
          <span class="folder-name">${folder}</span>
          <span class="mono-label">${fmtMin(list.reduce((a, v) => a + v.duration_seconds, 0))} OF FOOTAGE</span>
        </div>
        <span class="folder-arrow">&rarr;</span>`;
      card.onclick = () => { location.hash = `#/folder/${encodeURIComponent(folder)}`; };
      grid.appendChild(card);
    }
  }

  function renderGrid() {
    const list = state.folder === null
      ? state.videos
      : state.videos.filter((v) => v.folder === state.folder);

    $("grid-title").textContent = state.folder ? state.folder.toUpperCase() : "EVERYTHING";
    $("grid-count").textContent = `${list.length} ${list.length === 1 ? "TITLE" : "TITLES"}`;

    const grid = $("video-grid");
    grid.textContent = "";
    grid.className = "video-grid stagger";
    for (const v of list) {
      const card = document.createElement("button");
      card.className = "video-card";
      const resume = Number(localStorage.getItem(`resume:${v.slug}`) || 0);
      const pct = resume && v.duration_seconds
        ? Math.min(100, Math.round((resume / v.duration_seconds) * 100)) : 0;
      card.innerHTML = `
        <div class="video-thumb">
          <img src="${v.thumbnail}" alt="" loading="lazy">
          <span class="thumb-tag">${fmtMin(v.duration_seconds)}</span>
        </div>
        <span class="video-title">${v.title}</span>
        <div class="video-meta">
          <span class="mono-label">${v.folder.toUpperCase()}</span>
          ${pct ? `<span class="mono-label">${pct}% WATCHED</span>` :
                  `<span class="mono-label">${v.height ? `${v.height}P` : "HLS"}</span>`}
        </div>
        ${pct ? `<div class="video-progress"><div style="width:${pct}%"></div></div>` : ""}`;
      card.onclick = () => { location.hash = `#/watch/${v.slug}`; };
      grid.appendChild(card);
    }
    $("all-folders-btn").hidden = state.folder === null;
    $("folder-section").hidden = state.folder !== null;
  }

  function renderTicker() {
    const titles = state.videos.slice(0, 8)
      .map((v) => v.title.toUpperCase()).join(" — ");
    const text = `NOW STREAMING — ${titles} — NOW STREAMING — ${titles} — `;
    for (const id of ["login-ticker", "app-ticker"]) {
      const el = $(id);
      if (el) el.textContent = "";
      if (el) { const s1 = document.createElement("span"); s1.textContent = text; el.appendChild(s1); const s2 = document.createElement("span"); s2.textContent = text; el.appendChild(s2); }
    }
  }

  /* ---------------- HLS playback ---------------------------------------- */

  const canNativeHLS = document.createElement("video")
    .canPlayType("application/vnd.apple.mpegurl") !== "";

  function attachSource(video, src) {
    if (state.hls) { state.hls.destroy(); state.hls = null; }
    if (window.Hls && window.Hls.isSupported()) {
      const hls = new window.Hls({ enableWorker: true, maxBufferLength: 30 });
      hls.loadSource(src);
      hls.attachMedia(video);
      hls.on(window.Hls.Events.ERROR, (_e, data) => {
        // Expired CloudFront cookies -> renew once, then retry the source.
        if (data.response && data.response.code === 403 && !video.dataset.retried) {
          video.dataset.retried = "1";
          api("/api/stream-auth").then(() => attachSource(video, src));
        }
      });
      state.hls = hls;
    } else if (canNativeHLS) {
      video.src = src; // Safari plays HLS natively
    }
  }

  function playVideo(v) {
    const video = $("player-video");
    delete video.dataset.retried;
    $("player-title").textContent = v.title.toUpperCase();
    $("player-folder").textContent = `THE ARCHIVE — ${v.folder.toUpperCase()}`;
    $("player-duration").textContent = fmtMin(v.duration_seconds);
    $("player-resolution").textContent =
      v.width && v.height ? `${v.width} × ${v.height}` : "HLS";

    attachSource(video, v.playlist);

    const saved = Number(localStorage.getItem(`resume:${v.slug}`) || 0);
    if (saved > 5 && saved < v.duration_seconds - 5) {
      video.addEventListener("loadedmetadata", () => {
        video.currentTime = saved;
      }, { once: true });
    }
    video.ontimeupdate = () => {
      if (video.currentTime > 5) {
        localStorage.setItem(`resume:${v.slug}`, String(video.currentTime));
      }
    };
  }

  /* ---------------- hash router ----------------------------------------- */

  function route() {
    const hash = location.hash || "#/";
    const find = (slug) => state.videos.find((v) => v.slug === slug);

    if (hash.startsWith("#/watch/")) {
      const v = find(hash.slice("#/watch/".length));
      if (v) { playVideo(v); show("player"); return; }
      location.hash = "#/";
      return;
    }
    if (hash.startsWith("#/folder/")) {
      state.folder = decodeURIComponent(hash.slice("#/folder/".length));
      renderFolderNav(); renderGrid(); show("app");
      return;
    }
    state.folder = null;
    renderFolderNav(); renderFolders(); renderGrid(); show("app");
  }

  window.addEventListener("hashchange", route);
  document.addEventListener("click", (e) => {
    if (e.target.closest("[data-nav]") && e.target.closest("[data-nav]").dataset.hash) {
      e.preventDefault();
      location.hash = e.target.closest("[data-nav]").dataset.hash;
    }
  });

  /* ---------------- auth flow -------------------------------------------- */

  function enterApp(data) {
    state.videos = data.videos || [];
    state.username = data.username;
    $("nav-user").textContent = `@${(data.username || "").toUpperCase()}`;
    $("hero-count").textContent = `${state.videos.length} TITLES`;
    $("hero-hours").textContent = totalHours(state.videos);
    renderTicker();
    route();
  }

  $("login-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const err = $("login-error");
    err.hidden = true;
    const btn = $("login-submit");
    btn.disabled = true;
    btn.textContent = "SIGNING IN…";
    const { status, data } = await api("/api/login", {
      body: {
        username: $("login-username").value,
        password: $("login-password").value,
      },
    });
    btn.disabled = false;
    btn.textContent = "ENTER THE ARCHIVE";
    if (status !== 200) {
      err.textContent = data.error
        ? data.error.toUpperCase() : "SOMETHING WENT WRONG";
      err.hidden = false;
      return;
    }
    await api("/api/stream-auth"); // fresh CloudFront cookies alongside the session
    $("login-password").value = "";
    const { data: catalog } = await api("/api/videos");
    enterApp(catalog);
  });

  $("logout-btn").addEventListener("click", async () => {
    await api("/api/logout", { method: "POST" });
    state.videos = [];
    state.username = null;
    if (state.hls) { state.hls.destroy(); state.hls = null; }
    location.hash = "";
    show("login");
  });

  $("back-btn").addEventListener("click", () => {
    const v = state.videos.find((x) => location.hash === `#/watch/${x.slug}`);
    location.hash = state.folder ? `#/folder/${encodeURIComponent(state.folder)}` : "#/";
    if (v) { const video = $("player-video"); video.pause(); }
  });

  $("all-folders-btn").addEventListener("click", () => { location.hash = "#/"; });

  /* ---------------- boot -------------------------------------------------- */

  (async () => {
    const { status, data } = await api("/api/videos");
    if (status === 200) {
      enterApp(data); // cookie session still valid
    } else {
      show("login");
    }
  })();
})();
